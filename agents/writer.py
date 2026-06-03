"""Generates LinkedIn posts and PM briefs from analyst insights."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Optional
from typing_extensions import TypedDict

from prompts import WRITER_SYSTEM_PROMPT, PM_BRIEF_SYSTEM_PROMPT

if TYPE_CHECKING:
    from core.db import SupabaseClient
    from core.llm_client import LLMClient
    from core.retriever import InsightResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class PostDraft(TypedDict):
    """Output of WriterAgent.generate_linkedin_post()."""

    linkedin_post: str
    character_count: int
    hook_line: str
    hashtags: list[str]
    estimated_engagement: str


class PMBrief(TypedDict):
    """Output of WriterAgent.generate_pm_brief()."""

    title: str
    executive_summary: str
    problem_statement: str
    user_evidence: list[dict]
    proposed_feature: dict
    success_metrics: list[str]
    risks: list[str]
    competitive_context: str


class WriterOutput(TypedDict):
    """Output of WriterAgent.generate_both() — single Orchestrator entry point."""

    post_draft: PostDraft
    pm_brief: PMBrief
    topic: str
    company: str


# ---------------------------------------------------------------------------
# WriterAgent
# ---------------------------------------------------------------------------

class WriterAgent:
    """Generates LinkedIn posts and PM briefs from InsightResult objects."""

    _PM_BRIEF_REQUIRED_FIELDS = frozenset({
        "title", "executive_summary", "problem_statement",
        "user_evidence", "proposed_feature", "success_metrics",
        "risks", "competitive_context",
    })

    def __init__(self, llm: "LLMClient", db: "SupabaseClient") -> None:
        """Initialise with injected LLM and DB clients."""
        self.llm = llm
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_linkedin_post(self, insight: "InsightResult") -> PostDraft:
        """Generate a LinkedIn post from an insight; validates and retries once if invalid."""
        user_prompt = self._build_post_prompt(insight)
        start = time.monotonic()

        raw = self.llm.complete_json(system=WRITER_SYSTEM_PROMPT, user=user_prompt)
        validation_error = self._validate_post(raw)

        if validation_error:
            logger.warning("Post validation failed (%s) -- regenerating.", validation_error)
            retry_prompt = f"{user_prompt}\n\nCORRECTION NEEDED: {validation_error}"
            raw = self.llm.complete_json(system=WRITER_SYSTEM_PROMPT, user=retry_prompt)

        duration_ms = int((time.monotonic() - start) * 1000)

        # Recompute character_count — don't trust LLM's self-reported count
        post_text = raw.get("linkedin_post", "")
        char_count = len(post_text)

        self.db.log_run(
            agent_name="writer_post",
            status="success",
            input_summary=f"topic={insight['topic']} company={insight['company']}",
            output_summary=(
                f"chars={char_count} "
                f"engagement={raw.get('estimated_engagement', 'unknown')}"
            ),
            duration_ms=duration_ms,
        )

        return PostDraft(
            linkedin_post=post_text,
            character_count=char_count,
            hook_line=raw.get("hook_line", ""),
            hashtags=raw.get("hashtags", []),
            estimated_engagement=raw.get("estimated_engagement", "medium"),
        )

    def generate_pm_brief(self, insight: "InsightResult") -> PMBrief:
        """Generate a PM brief from an insight; validates required JSON fields once."""
        user_prompt = self._build_brief_prompt(insight)
        start = time.monotonic()

        raw = self.llm.complete_json(system=PM_BRIEF_SYSTEM_PROMPT, user=user_prompt)

        missing = self._PM_BRIEF_REQUIRED_FIELDS - set(raw.keys())
        if missing:
            logger.warning("PM brief missing fields %s -- retrying.", missing)
            retry_prompt = (
                f"{user_prompt}\n\nCORRECTION NEEDED: "
                f"Missing required fields: {', '.join(sorted(missing))}. Include all of them."
            )
            raw = self.llm.complete_json(system=PM_BRIEF_SYSTEM_PROMPT, user=retry_prompt)

        duration_ms = int((time.monotonic() - start) * 1000)

        self.db.log_run(
            agent_name="writer_brief",
            status="success",
            input_summary=f"topic={insight['topic']} company={insight['company']}",
            output_summary=f"title={raw.get('title', '')[:60]}",
            duration_ms=duration_ms,
        )

        return PMBrief(
            title=raw.get("title", ""),
            executive_summary=raw.get("executive_summary", ""),
            problem_statement=raw.get("problem_statement", ""),
            user_evidence=raw.get("user_evidence", []),
            proposed_feature=raw.get("proposed_feature", {}),
            success_metrics=raw.get("success_metrics", []),
            risks=raw.get("risks", []),
            competitive_context=raw.get("competitive_context", ""),
        )

    def generate_both(self, insight: "InsightResult") -> WriterOutput:
        """Generate LinkedIn post and PM brief; single entry point for Orchestrator."""
        post_draft = self.generate_linkedin_post(insight)
        pm_brief = self.generate_pm_brief(insight)
        return WriterOutput(
            post_draft=post_draft,
            pm_brief=pm_brief,
            topic=insight["topic"],
            company=insight["company"],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_post_prompt(self, insight: "InsightResult") -> str:
        """Format user prompt for LinkedIn post generation."""
        data = insight["insight"]
        pain_points = data.get("pain_points", [])
        pain_summary = "\n".join(
            f"- {pp['title']}: {pp['description']}"
            for pp in pain_points[:3]
        )
        positive = ", ".join(data.get("positive_signals", []))
        return (
            f"Topic: {insight['topic']}\n"
            f"Company: {insight['company']}\n"
            f"Overall sentiment: {data.get('sentiment_label', 'unknown')}\n"
            f"Source count: {insight['source_count']}\n"
            f"Confidence: {insight['confidence']}\n\n"
            f"Top pain points:\n{pain_summary}\n\n"
            f"Positive signals: {positive}\n\n"
            "Write a LinkedIn post following the rules in the system prompt."
        )

    def _build_brief_prompt(self, insight: "InsightResult") -> str:
        """Format user prompt for PM brief generation."""
        return (
            "Write a PM brief for the following analyst insight.\n\n"
            f"Insight JSON:\n{json.dumps(insight['insight'], indent=2)}\n\n"
            f"Topic: {insight['topic']}\n"
            f"Company: {insight['company']}\n"
            f"Source count: {insight['source_count']}\n"
            f"Confidence: {insight['confidence']}"
        )

    def _validate_post(self, raw: dict) -> Optional[str]:
        """Return a correction string if the post fails validation, else None."""
        post_text = raw.get("linkedin_post", "")
        if len(post_text) > 1300:
            return (
                f"Post is {len(post_text)} characters -- must be under 1300. Shorten it."
            )
        if not raw.get("hashtags"):
            return "No hashtags present -- add 3-5 relevant hashtags on the last line."
        if not raw.get("hook_line"):
            return "hook_line field is missing -- provide the first line of the post as hook_line."
        return None
