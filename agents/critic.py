"""Quality-gates posts with a 1-25 scoring rubric; triggers auto-post or alert."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Optional
from typing_extensions import TypedDict

import config
from prompts import CRITIC_SYSTEM_PROMPT

if TYPE_CHECKING:
    from core.db import SupabaseClient
    from core.llm_client import LLMClient
    from core.retriever import InsightResult
    from agents.writer import PostDraft

logger = logging.getLogger(__name__)

_SCORE_FIELDS = frozenset({
    "factual_grounding",
    "insight_novelty",
    "writing_quality",
    "pm_relevance",
    "engagement_potential",
})

# Numeric patterns that represent factual claims worth verifying against source chunks.
# Deliberately excludes plain integers to avoid false positives on source counts,
# bullet counts, and other conversational numbers.
_NUMERIC_PATTERNS = [
    r'\d+(?:\.\d+)?%',                                                        # 85%, 17.5%
    r'\d+(?:\.\d+)?[xX]\b',                                                   # 3x, 2.5x
    r'\$\d+(?:\.\d+)?(?:\s*[KMBkmb]|\s*(?:million|billion|thousand))?\b',    # $50M, $17.5
    r'\d+(?:\.\d+)?\s*(?:million|billion|thousand)\b',                        # 15 million
    r'\d+(?:\.\d+)?[KMBkmb]\b',                                               # 50K, 3M
]


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class CriticResult(TypedDict):
    """Output of CriticAgent.evaluate()."""

    scores: dict
    total: int
    decision: str
    primary_weakness: str
    improvement_suggestion: str
    hallucination_check: str
    hallucination_detail: str


# ---------------------------------------------------------------------------
# CriticAgent
# ---------------------------------------------------------------------------

class CriticAgent:
    """Scores LinkedIn posts on a 1-25 rubric; gates auto-post decisions."""

    def __init__(self, llm: "LLMClient", db: "SupabaseClient") -> None:
        """Initialise with injected LLM and DB clients."""
        self.llm = llm
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        post_draft: "PostDraft",
        insight: "InsightResult",
        topic_id: str = "",
        source_chunks: Optional[list] = None,
    ) -> CriticResult:
        """Score a post via LLM, recompute decision in Python, log to DB."""
        user_prompt = (
            f"LinkedIn post to evaluate:\n\n{post_draft['linkedin_post']}\n\n"
            f"Source insight (JSON):\n{json.dumps(insight['insight'], indent=2)}"
        )

        start = time.monotonic()
        raw = self.llm.complete_json(system=CRITIC_SYSTEM_PROMPT, user=user_prompt)
        duration_ms = int((time.monotonic() - start) * 1000)

        # --- Validate all 5 score fields present ---
        scores: dict = raw.get("scores", {})
        missing = _SCORE_FIELDS - set(scores.keys())
        if missing:
            logger.warning("Critic missing score fields %s -- retrying.", missing)
            raw = self.llm.complete_json(
                system=CRITIC_SYSTEM_PROMPT,
                user=user_prompt + "\n\nCORRECTION: Include all 5 score fields.",
            )
            scores = raw.get("scores", {})

        # --- Recompute total in Python; ignore LLM's self-reported sum ---
        total = sum(int(scores.get(f, 0)) for f in _SCORE_FIELDS)

        # --- Decision: Python threshold gate, not LLM field ---
        thresholds = config.CRITIC_THRESHOLDS
        if total >= thresholds["auto_post"]:
            decision = "auto_post"
        elif total >= thresholds["soft_approval"]:
            decision = "soft_approval"
        else:
            decision = "auto_reject"

        # --- Hallucination check: keyword gate + numeric-claim gate ---
        post_text = post_draft["linkedin_post"]
        kw_result = self._check_hallucination(post_text=post_text, insight=insight)
        num_result, num_detail = self._check_numeric_claims(
            post_text=post_text,
            insight=insight,
            source_chunks=source_chunks or [],
        )
        hallucination_check = (
            "passed" if (kw_result == "passed" and num_result == "passed") else "failed"
        )
        hallucination_detail = (
            num_detail if num_result == "failed"
            else ("pain_point keyword mismatch" if kw_result == "failed" else "")
        )

        result = CriticResult(
            scores=scores,
            total=total,
            decision=decision,
            primary_weakness=raw.get("primary_weakness", ""),
            improvement_suggestion=raw.get("improvement_suggestion", ""),
            hallucination_check=hallucination_check,
            hallucination_detail=hallucination_detail,
        )

        self.db.log_run(
            agent_name="critic",
            status="success",
            input_summary=f"topic={insight['topic']} company={insight['company']}",
            output_summary=(
                f"total={total} decision={decision} "
                f"hallucination={hallucination_check}"
                + (f" detail={hallucination_detail}" if hallucination_detail else "")
            ),
            duration_ms=duration_ms,
        )

        return result

    def notify_if_needed(
        self,
        result: CriticResult,
        post_draft: Optional[dict] = None,
    ) -> bool:
        """Send soft-approval alert via notifier; return True if sent."""
        if result["decision"] != "soft_approval":
            return False
        try:
            from tools.notifier import Notifier
            return Notifier().notify_soft_approval(post_draft or {}, result)
        except Exception as exc:
            logger.warning("Notifier failed (non-fatal): %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_hallucination(
        self,
        post_text: str,
        insight: "InsightResult",
    ) -> str:
        """Python keyword gate: require 2+ pain_point title keywords in post.

        Keywords = words >4 chars from each pain_point title (lowercased).
        If fewer than 2 pain points have a keyword match, returns 'failed'.
        """
        pain_points = insight["insight"].get("pain_points", [])
        post_lower = post_text.lower()

        matched = 0
        for pp in pain_points:
            title = pp.get("title", "")
            keywords = [w for w in title.lower().split() if len(w) > 4]
            if any(kw in post_lower for kw in keywords):
                matched += 1
            if matched >= 2:
                return "passed"

        logger.warning(
            "Hallucination check FAILED: only %d/2 pain_point keywords found in post.",
            matched,
        )
        return "failed"

    def _check_numeric_claims(
        self,
        post_text: str,
        insight: "InsightResult",
        source_chunks: list,
    ) -> tuple[str, str]:
        """Verify that numeric claims in the post appear verbatim in source chunks.

        Targets high-confidence fabrication patterns only: percentages, x-multiples,
        million/billion/K/M scale numbers, and dollar amounts. Plain integers are not
        checked to avoid false positives on source counts and conversational numbers.
        Returns ("passed"|"failed", detail_string).
        """
        if not source_chunks:
            return "passed", "no source chunks to verify against"

        corpus = " ".join((c.get("content") or "") for c in source_chunks).lower()

        # Pipeline-generated counts that are never in source chunks but are legitimate
        exempt: set[str] = set()
        sc = insight.get("source_count")
        if sc is not None:
            exempt.add(str(int(sc)))
        cc = (insight.get("insight") or {}).get("chunk_count")
        if cc is not None:
            exempt.add(str(int(cc)))

        # Strip hashtags and version strings before pattern matching
        post_clean = re.sub(r'#\S+', '', post_text)
        post_clean = re.sub(
            r'\b(?:iOS|GPT|v|version|Android|Windows|macOS|watchOS)\s*\d+(?:\.\d+)?',
            '', post_clean, flags=re.IGNORECASE,
        )

        # Extract all targeted numeric claims from the cleaned post
        found: set[str] = set()
        for pattern in _NUMERIC_PATTERNS:
            for m in re.finditer(pattern, post_clean, re.IGNORECASE):
                found.add(m.group(0).strip())

        if not found:
            return "passed", "no numeric claims to verify"

        unsupported: list[str] = []
        for claim in found:
            bare_m = re.search(r'\d+(?:\.\d+)?', claim)
            if not bare_m:
                continue
            bare = bare_m.group(0)
            if bare in exempt:
                continue
            # Exempt calendar years (4-digit 19XX / 20XX)
            if re.fullmatch(r'(?:19|20)\d{2}', bare):
                continue
            if not re.search(r'\b' + re.escape(bare) + r'\b', corpus):
                unsupported.append(claim)

        if unsupported:
            detail = f"Ungrounded numeric claims: {', '.join(sorted(unsupported))}"
            logger.warning("Numeric hallucination check FAILED: %s", detail)
            return "failed", detail

        return "passed", f"all {len(found)} numeric claim(s) verified in source chunks"
