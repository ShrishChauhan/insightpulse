"""RAG query logic: similarity search against Supabase pgvector embeddings."""

from __future__ import annotations

import logging
import time
import warnings
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, TypedDict

import config
from prompts import ANALYST_SYSTEM_PROMPT, ANALYST_USER_PROMPT, QUERY_VARIATION_PROMPT

if TYPE_CHECKING:
    from core.db import SupabaseClient
    from core.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class RetrievalResult(TypedDict):
    """Output of Retriever.retrieve()."""
    topic: str
    company: str
    chunks: list[dict]
    metadata: dict
    chunk_count: int


class InsightResult(TypedDict):
    """Output of Retriever.generate_insight()."""
    topic: str
    company: str
    insight: dict
    source_count: int
    confidence: str
    tokens_used: int


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class Retriever:
    """RAG retriever: embeds queries, fetches chunks, generates structured insights."""

    def __init__(self, db: "SupabaseClient", llm: "LLMClient") -> None:
        """Initialise with injected DB and LLM clients."""
        self.db = db
        self.llm = llm
        self._embedder = None  # lazy-loaded to avoid slow model load at import

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        topic: str,
        company: str,
        days: int = config.DAYS_LOOKBACK,
    ) -> RetrievalResult:
        """Embed topic, run similarity search with query variations, return deduplicated chunks."""
        from core.embedder import Embedder  # local import to avoid circular deps

        if self._embedder is None:
            self._embedder = Embedder()

        # --- Primary query ---
        primary_embedding = self._embed_query(topic, company)
        primary_hits = self.db.similarity_search(
            query_embedding=primary_embedding,
            match_count=config.MAX_RETRIEVAL_CHUNKS,
            company_filter=company if company else None,
        )

        # --- Query variations (one LLM call for 2 alternatives) ---
        variation_hits: list[dict] = []
        try:
            variations = self._get_query_variations(topic, company)
            for alt_query in variations:
                alt_embedding = self._embed_query(alt_query, company)
                hits = self.db.similarity_search(
                    query_embedding=alt_embedding,
                    match_count=10,
                    company_filter=company if company else None,
                )
                variation_hits.extend(hits)
        except Exception as exc:
            logger.warning("Query variation failed (non-fatal): %s", exc)

        # --- Merge + deduplicate by content ---
        all_hits = primary_hits + variation_hits
        seen: set[str] = set()
        unique: list[dict] = []
        for chunk in all_hits:
            key = (chunk.get("content") or "")[:120]
            if key not in seen:
                seen.add(key)
                unique.append(chunk)

        # --- Date filter: only chunks within the lookback window ---
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        filtered: list[dict] = []
        for chunk in unique:
            created_raw = chunk.get("created_at")
            if created_raw:
                try:
                    created = datetime.fromisoformat(
                        created_raw.replace("Z", "+00:00")
                    )
                    if created >= cutoff:
                        filtered.append(chunk)
                    continue
                except (ValueError, AttributeError):
                    pass
            # If no timestamp or unparseable, include by default
            filtered.append(chunk)

        return RetrievalResult(
            topic=topic,
            company=company,
            chunks=filtered,
            metadata={"days": days, "cutoff_iso": cutoff.isoformat()},
            chunk_count=len(filtered),
        )

    def generate_insight(self, retrieval: RetrievalResult) -> InsightResult:
        """Generate structured JSON insight from retrieval chunks via LLM."""
        chunks = retrieval["chunks"]
        topic = retrieval["topic"]
        company = retrieval["company"]
        days = retrieval["metadata"].get("days", config.DAYS_LOOKBACK)

        # Format chunks as readable context block
        retrieved_chunks = self._format_chunks(chunks)

        # Fill in prompt template
        user_prompt = ANALYST_USER_PROMPT.format(
            topic=topic,
            company=company,
            days=days,
            retrieved_chunks=retrieved_chunks,
        )

        start = time.monotonic()
        raw_insight = self.llm.complete_json(
            system=ANALYST_SYSTEM_PROMPT,
            user=user_prompt,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        # --- Schema validation ---
        if "pain_points" not in raw_insight:
            logger.warning("Insight missing pain_points — retrying with stricter prompt.")
            raw_insight = self.llm.complete_json(
                system=ANALYST_SYSTEM_PROMPT,
                user=user_prompt + "\n\nOutput only valid JSON.",
            )

        if "source_count" not in raw_insight:
            raw_insight["source_count"] = len(chunks)

        # --- Confidence self-check ---
        pain_count = len(raw_insight.get("pain_points") or [])
        chunk_count = retrieval["chunk_count"]
        if pain_count < 3 or chunk_count < 5:
            raw_insight["confidence"] = "low"
            logger.warning(
                "Low confidence: pain_points=%d chunk_count=%d topic=%s",
                pain_count,
                chunk_count,
                topic,
            )

        confidence = raw_insight.get("confidence", "low")
        source_count = raw_insight.get("source_count", len(chunks))

        # Log to DB
        try:
            self.db.log_run(
                agent_name="retriever",
                status="success",
                input_summary=f"topic={topic} company={company} chunks={chunk_count}",
                output_summary=f"confidence={confidence} pain_points={pain_count}",
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.warning("DB log failed (non-fatal): %s", exc)

        return InsightResult(
            topic=topic,
            company=company,
            insight=raw_insight,
            source_count=source_count,
            confidence=confidence,
            tokens_used=0,  # tokens are tracked inside LLMClient._log_call
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed_query(self, query: str, company: str) -> list[float]:
        """Embed a query string using the sentence-transformer model."""
        # Prefix company name to bias the embedding toward relevant content
        text = f"{company} {query}".strip() if company else query
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            if not hasattr(self, "_st_model"):
                self._st_model = SentenceTransformer(config.EMBEDDING_MODEL)
            embedding = self._st_model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def _get_query_variations(self, topic: str, company: str) -> list[str]:
        """Ask LLM for 2 alternative search queries; returns list of 0-2 strings."""
        prompt = QUERY_VARIATION_PROMPT.format(topic=topic, company=company)
        result = self.llm.complete_json(
            system="You generate search query variations. Output only valid JSON.",
            user=prompt,
        )
        return result.get("queries", [])[:2]

    def _format_chunks(self, chunks: list[dict]) -> str:
        """Format retrieval chunks as a numbered context block for the LLM."""
        if not chunks:
            return "(no chunks retrieved)"
        lines: list[str] = []
        for i, chunk in enumerate(chunks[:15], start=1):
            source = chunk.get("source_url") or chunk.get("company") or "unknown"
            content = (chunk.get("content") or "").strip()
            score = chunk.get("similarity", 0.0)
            lines.append(
                f"[{i}] source={source} relevance={score:.2f}\n{content}"
            )
        return "\n\n".join(lines)
