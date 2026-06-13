"""AnalystAgent: wraps Retriever to provide the standard agent interface for the orchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.retriever import Retriever, RetrievalResult, InsightResult

if TYPE_CHECKING:
    from core.db import SupabaseClient


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
        insight = self._retriever.generate_insight(retrieval)
        return retrieval, insight
