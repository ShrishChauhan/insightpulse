"""Quality-gates posts with a 1-25 scoring rubric; triggers auto-post or alert."""

from __future__ import annotations

import json
import logging
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

        # --- Hallucination check: Python keyword gate ---
        hallucination_check = self._check_hallucination(
            post_text=post_draft["linkedin_post"],
            insight=insight,
        )

        result = CriticResult(
            scores=scores,
            total=total,
            decision=decision,
            primary_weakness=raw.get("primary_weakness", ""),
            improvement_suggestion=raw.get("improvement_suggestion", ""),
            hallucination_check=hallucination_check,
            hallucination_detail=raw.get("hallucination_detail", ""),
        )

        # --- Log post record (if topic_id provided) ---
        if topic_id:
            try:
                self.db.log_post(
                    topic_id=topic_id,
                    linkedin_post=post_draft["linkedin_post"],
                    critic_score=total,
                    decision=decision,
                )
            except Exception as exc:
                logger.warning("DB log_post failed (non-fatal): %s", exc)

        self.db.log_run(
            agent_name="critic",
            status="success",
            input_summary=f"topic={insight['topic']} company={insight['company']}",
            output_summary=(
                f"total={total} decision={decision} "
                f"hallucination={hallucination_check}"
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
