"""AnalystAgent: wraps Retriever to provide the standard agent interface for the orchestrator."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.retriever import Retriever, RetrievalResult, InsightResult

if TYPE_CHECKING:
    from core.db import SupabaseClient

logger = logging.getLogger(__name__)

# Fewer than this many chunks → narrow scope, force low confidence
_THIN_RETRIEVAL_THRESHOLD = 6


class AnalystAgent:
    """Orchestrates RAG retrieval and LLM insight generation for a given topic.

    Thin wrapper around Retriever that exposes a single analyze() entrypoint
    consistent with the other agents in the InsightPulse agent roster.
    Accepts a shared Retriever instance to avoid loading the sentence-transformer
    model twice when orchestrated alongside the Scout agent.
    """

    def __init__(self, retriever: Retriever, db: "SupabaseClient") -> None:
        """Accept shared Retriever instance to avoid duplicate model loads."""
        self._retriever = retriever
        self._db = db

    def analyze(self, topic: str, company: str) -> tuple[RetrievalResult, InsightResult]:
        """Retrieve chunks and generate a structured insight for the given topic.

        Returns (RetrievalResult, InsightResult). The orchestrator stores both
        in pipeline state as retrieval_result and insight respectively.
        """
        retrieval = self._retriever.retrieve(topic=topic, company=company)

        chunk_count = retrieval["chunk_count"]
        if chunk_count < _THIN_RETRIEVAL_THRESHOLD:
            logger.warning(
                "Thin retrieval for topic=%r company=%r: %d chunks (threshold=%d) -- "
                "analyst will narrow scope and output low-confidence insight",
                topic, company, chunk_count, _THIN_RETRIEVAL_THRESHOLD,
            )

        insight = self._retriever.generate_insight(retrieval)

        # Enforce low confidence on thin retrieval regardless of LLM self-assessment.
        # The system prompt instructs the LLM to do this, but we hard-gate here as a
        # safeguard against the model over-reporting confidence on sparse data.
        if chunk_count < _THIN_RETRIEVAL_THRESHOLD:
            insight["confidence"] = "low"
            raw = insight["insight"]
            raw["confidence"] = "low"
            if "data_gap" not in raw:
                raw["data_gap"] = (
                    f"Only {chunk_count} source chunks retrieved "
                    f"(threshold: {_THIN_RETRIEVAL_THRESHOLD}). "
                    "Claims are narrowed to what the source data directly supports."
                )

        return retrieval, insight
