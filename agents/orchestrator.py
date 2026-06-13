"""LangGraph orchestrator: wires Scout -> Retriever -> Writer -> Critic into a stateful graph."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Literal, Optional

from typing_extensions import TypedDict
from langgraph.graph import StateGraph, END

import config
from core.db import SupabaseClient
from core.llm_client import LLMClient
from core.retriever import Retriever
from agents.analyst import AnalystAgent
from agents.scout import ScoutAgent
from agents.writer import WriterAgent
from agents.critic import CriticAgent
from tools.linkedin_poster import LinkedInPoster

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class InsightPulseState(TypedDict, total=False):
    """Full pipeline state passed between LangGraph nodes."""

    week_plan: list[dict]           # TopicCandidates from Scout
    current_topic: dict             # Single selected TopicCandidate
    retrieval_result: dict          # RetrievalResult from Retriever.retrieve()
    insight: dict                   # InsightResult from Retriever.generate_insight()
    post_draft: dict                # PostDraft from WriterAgent
    pm_brief: dict                  # PMBrief from WriterAgent
    critic_result: dict             # CriticResult from CriticAgent
    retry_count: int                # topic-level retries, starts 0, max 3
    writer_retry_count: int         # writer-level retries, starts 0, max 1
    errors: list[str]               # accumulated error messages
    run_id: str                     # UUID for this pipeline run
    status: str                     # running / success / failed / escalated
    dry_run: bool                   # True = no live Buffer post; False = live


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------

class RunResult(TypedDict):
    """Returned by run_weekly() and run_single()."""

    run_id: str
    status: str
    topics_processed: int
    posts_created: int
    errors: list[str]
    duration_ms: int
    dry_run: bool


# ---------------------------------------------------------------------------
# OrchestratorAgent
# ---------------------------------------------------------------------------

class OrchestratorAgent:
    """Builds and runs the LangGraph InsightPulse pipeline."""

    def __init__(
        self,
        db: Optional[SupabaseClient] = None,
        llm: Optional[LLMClient] = None,
    ) -> None:
        """Instantiate all agents via dependency injection; compile the graph."""
        self._db = db or SupabaseClient()
        self._llm = llm or LLMClient()
        self._retriever = Retriever(db=self._db, llm=self._llm)
        self._analyst = AnalystAgent(retriever=self._retriever, db=self._db)
        self._scout = ScoutAgent(db=self._db, llm=self._llm)
        self._writer = WriterAgent(llm=self._llm, db=self._db)
        self._critic = CriticAgent(llm=self._llm, db=self._db)
        self._poster = LinkedInPoster(db=self._db)

        self._graph = self._build_graph()
        logger.info("OrchestratorAgent compiled successfully.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_weekly(self, dry_run: bool = True) -> RunResult:
        """Run the full pipeline for the top 2 topics from Scout.

        Calls plan_node first, then runs 2 sequential single-topic pipelines.
        Both runs share the same week_plan but have independent state.
        """
        t0 = time.monotonic()
        run_id = str(uuid.uuid4())
        all_errors: list[str] = []
        posts_created = 0
        topics_processed = 0

        self._db.log_run(
            agent_name="orchestrator",
            status="success",
            input_summary=f"run_weekly dry_run={dry_run} run_id={run_id}",
            output_summary="pipeline started",
        )

        # Initial state — plan_node will populate week_plan
        initial: InsightPulseState = {
            "run_id": run_id,
            "status": "running",
            "dry_run": dry_run,
            "retry_count": 0,
            "writer_retry_count": 0,
            "errors": [],
            "week_plan": [],
            "current_topic": {},
            "retrieval_result": {},
            "insight": {},
            "post_draft": {},
            "pm_brief": {},
            "critic_result": {},
        }

        result = self._graph.invoke(
            initial,
            config={"recursion_limit": 20},
        )

        status = result.get("status", "failed")
        all_errors = result.get("errors", [])
        if status in ("success", "posted", "soft_approval"):
            topics_processed = 1
            if status in ("success", "posted"):
                posts_created = 1

        # Run a second topic if week_plan has more candidates
        week_plan = result.get("week_plan", [])
        if len(week_plan) >= 2:
            second_topic = week_plan[1]
            second_result = self._run_from_topic(
                topic=second_topic.get("topic", ""),
                company=second_topic.get("company", ""),
                dry_run=dry_run,
                run_id=f"{run_id}-2",
            )
            topics_processed += 1
            all_errors.extend(second_result.get("errors", []))
            if second_result.get("status") in ("success", "posted"):
                posts_created += 1

        duration_ms = int((time.monotonic() - t0) * 1000)
        final_status = "success" if not all_errors else "failed"

        self._db.log_run(
            agent_name="orchestrator",
            status="success",
            input_summary=f"run_weekly completed run_id={run_id}",
            output_summary=(
                f"topics={topics_processed} posts={posts_created} "
                f"errors={len(all_errors)} dry_run={dry_run}"
            ),
            duration_ms=duration_ms,
        )

        return RunResult(
            run_id=run_id,
            status=final_status,
            topics_processed=topics_processed,
            posts_created=posts_created,
            errors=all_errors,
            duration_ms=duration_ms,
            dry_run=dry_run,
        )

    def run_single(
        self,
        topic: str,
        company: str,
        dry_run: bool = True,
    ) -> RunResult:
        """Run pipeline for a specific topic, bypassing plan and select nodes."""
        t0 = time.monotonic()
        run_id = str(uuid.uuid4())

        result = self._run_from_topic(
            topic=topic,
            company=company,
            dry_run=dry_run,
            run_id=run_id,
        )

        duration_ms = int((time.monotonic() - t0) * 1000)
        return RunResult(
            run_id=run_id,
            status=result.get("status", "failed"),
            topics_processed=1,
            posts_created=1 if result.get("status") in ("success", "posted") else 0,
            errors=result.get("errors", []),
            duration_ms=duration_ms,
            dry_run=dry_run,
        )

    def get_graph_image(self, path: str = "data/graph_viz.png") -> None:
        """Save a LangGraph visualization PNG to the given path (portfolio artifact)."""
        try:
            from PIL import Image  # type: ignore[import]
            import io
            png_bytes = self._graph.get_graph().draw_mermaid_png()
            img = Image.open(io.BytesIO(png_bytes))
            img.save(path)
            print(f"[orchestrator] Graph image saved to {path}")
        except Exception as exc:
            logger.warning("get_graph_image failed (non-fatal): %s", exc)
            print(f"[orchestrator] get_graph_image skipped: {exc}")

    # ------------------------------------------------------------------
    # Internal: run from a pre-set topic (bypasses plan + select)
    # ------------------------------------------------------------------

    def _run_from_topic(
        self,
        topic: str,
        company: str,
        dry_run: bool,
        run_id: str,
    ) -> InsightPulseState:
        """Invoke graph with current_topic pre-filled; graph starts at retrieve_node."""
        initial: InsightPulseState = {
            "run_id": run_id,
            "status": "running",
            "dry_run": dry_run,
            "retry_count": 0,
            "writer_retry_count": 0,
            "errors": [],
            "week_plan": [],
            "current_topic": {"topic": topic, "company": company},
            "retrieval_result": {},
            "insight": {},
            "post_draft": {},
            "pm_brief": {},
            "critic_result": {},
        }
        return self._graph.invoke(
            initial,
            config={"recursion_limit": 20},
        )

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self) -> StateGraph:
        """Build and compile the LangGraph StateGraph."""
        graph = StateGraph(InsightPulseState)

        # Register nodes
        graph.add_node("plan_node", self._plan_node)
        graph.add_node("select_topic_node", self._select_topic_node)
        graph.add_node("retrieve_node", self._retrieve_node)
        graph.add_node("write_node", self._write_node)
        graph.add_node("critique_node", self._critique_node)
        graph.add_node("post_node", self._post_node)
        graph.add_node("alert_node", self._alert_node)
        graph.add_node("retry_node", self._retry_node)
        graph.add_node("end_node", self._end_node)

        # Entry point: if current_topic already set, skip plan + select
        graph.set_conditional_entry_point(
            self._entry_router,
            {
                "plan_node": "plan_node",
                "retrieve_node": "retrieve_node",
            },
        )

        # Linear flow
        graph.add_edge("plan_node", "select_topic_node")
        graph.add_edge("select_topic_node", "retrieve_node")
        graph.add_edge("retrieve_node", "write_node")
        graph.add_edge("write_node", "critique_node")

        # Conditional edges from critique_node
        graph.add_conditional_edges(
            "critique_node",
            self._critique_router,
            {
                "post_node": "post_node",
                "alert_node": "alert_node",
                "write_node": "write_node",
                "retry_node": "retry_node",
                "end_node": "end_node",
            },
        )

        # Terminal flows
        graph.add_edge("post_node", "end_node")
        graph.add_edge("alert_node", "end_node")
        graph.add_edge("retry_node", "select_topic_node")
        graph.add_edge("end_node", END)

        return graph.compile()

    # ------------------------------------------------------------------
    # Entry router
    # ------------------------------------------------------------------

    def _entry_router(
        self, state: InsightPulseState
    ) -> Literal["plan_node", "retrieve_node"]:
        """Route to retrieve_node if current_topic already set (run_single path)."""
        topic = state.get("current_topic") or {}
        if topic.get("topic") and topic.get("company"):
            return "retrieve_node"
        return "plan_node"

    # ------------------------------------------------------------------
    # Critique router
    # ------------------------------------------------------------------

    def _critique_router(
        self, state: InsightPulseState
    ) -> Literal["post_node", "alert_node", "write_node", "retry_node", "end_node"]:
        """Route after critique_node based on decision + retry counters."""
        critic = state.get("critic_result") or {}
        decision = critic.get("decision", "auto_reject")
        retry_count = state.get("retry_count", 0)
        writer_retry_count = state.get("writer_retry_count", 0)

        # Error state: route straight to end
        if state.get("status") == "failed":
            return "end_node"

        if decision == "auto_post":
            return "post_node"

        if decision == "soft_approval":
            return "alert_node"

        # auto_reject: check if it was a format failure (writer issue) vs insight failure
        post_text = (state.get("post_draft") or {}).get("linkedin_post", "")
        hallucination = critic.get("hallucination_check", "passed")
        is_format_failure = (
            len(post_text) > 1300
            or len(post_text) == 0
            or hallucination == "failed"
        )

        if is_format_failure and writer_retry_count < 1:
            return "write_node"

        if retry_count < 3:
            return "retry_node"

        return "alert_node"

    # ------------------------------------------------------------------
    # Node implementations
    # ------------------------------------------------------------------

    def _plan_node(self, state: InsightPulseState) -> dict:
        """Call ScoutAgent.discover_topics(); populate week_plan."""
        try:
            topics = self._scout.discover_topics(top_n=5)
            if not topics:
                return {
                    "week_plan": [],
                    "errors": (state.get("errors") or []) + ["Scout returned 0 topics."],
                    "status": "failed",
                }
            print(f"[plan_node] Scout found {len(topics)} topics. Top: {topics[0]['topic']}")
            return {"week_plan": [dict(t) for t in topics]}
        except Exception as exc:
            msg = f"plan_node error: {exc}"
            logger.error(msg)
            return {
                "errors": (state.get("errors") or []) + [msg],
                "status": "failed",
            }

    def _select_topic_node(self, state: InsightPulseState) -> dict:
        """Pick week_plan[retry_count] as the current topic; reset writer counter."""
        try:
            week_plan = state.get("week_plan") or []
            retry_count = state.get("retry_count", 0)
            idx = min(retry_count, len(week_plan) - 1)

            if not week_plan:
                return {
                    "errors": (state.get("errors") or []) + ["No topics in week_plan."],
                    "status": "failed",
                }

            topic = week_plan[idx]
            print(
                f"[select_topic_node] topic[{idx}]: "
                f"'{topic['topic']}' / '{topic['company']}'"
            )
            return {
                "current_topic": topic,
                "post_draft": {},
                "pm_brief": {},
                "critic_result": {},
                "writer_retry_count": 0,
            }
        except Exception as exc:
            msg = f"select_topic_node error: {exc}"
            logger.error(msg)
            return {
                "errors": (state.get("errors") or []) + [msg],
                "status": "failed",
            }

    def _retrieve_node(self, state: InsightPulseState) -> dict:
        """AnalystAgent.analyze(); fill retrieval_result + insight."""
        try:
            topic_dict = state.get("current_topic") or {}
            topic = topic_dict.get("topic", "")
            company = topic_dict.get("company", "")

            print(f"[retrieve_node] topic='{topic}' company='{company}'")
            retrieval, insight = self._analyst.analyze(topic=topic, company=company)
            print(
                f"[retrieve_node] {retrieval['chunk_count']} chunks retrieved. "
                f"confidence={insight['confidence']} "
                f"pain_points={len(insight['insight'].get('pain_points', []))}"
            )
            return {
                "retrieval_result": dict(retrieval),
                "insight": dict(insight),
            }
        except Exception as exc:
            msg = f"retrieve_node error: {exc}"
            logger.error(msg)
            return {
                "errors": (state.get("errors") or []) + [msg],
                "status": "failed",
            }

    def _write_node(self, state: InsightPulseState) -> dict:
        """WriterAgent.generate_both(); increment writer_retry_count on re-entry."""
        try:
            insight_dict = state.get("insight") or {}
            if not insight_dict:
                return {
                    "errors": (state.get("errors") or []) + ["write_node: no insight in state."],
                    "status": "failed",
                }

            writer_retry = state.get("writer_retry_count", 0)
            if writer_retry > 0:
                print(f"[write_node] Writer retry #{writer_retry} — same insight, new draft.")

            output = self._writer.generate_both(insight_dict)  # type: ignore[arg-type]
            print(
                f"[write_node] Post: {output['post_draft']['character_count']} chars "
                f"hashtags={len(output['post_draft'].get('hashtags', []))}"
            )
            return {
                "post_draft": dict(output["post_draft"]),
                "pm_brief": dict(output["pm_brief"]),
                "writer_retry_count": writer_retry + 1,
            }
        except Exception as exc:
            msg = f"write_node error: {exc}"
            logger.error(msg)
            return {
                "errors": (state.get("errors") or []) + [msg],
                "status": "failed",
            }

    def _critique_node(self, state: InsightPulseState) -> dict:
        """CriticAgent.evaluate(); log topic; fill critic_result."""
        try:
            post_draft = state.get("post_draft")
            insight = state.get("insight")

            if not post_draft or not insight:
                return {
                    "errors": (state.get("errors") or []) + [
                        "critique_node: missing post_draft or insight."
                    ],
                    "status": "failed",
                }

            topic_id = self._db.log_topic(
                topic=insight.get("topic", ""),
                company=insight.get("company", ""),
            ) or ""

            result = self._critic.evaluate(
                post_draft=post_draft,  # type: ignore[arg-type]
                insight=insight,        # type: ignore[arg-type]
                topic_id=topic_id,
            )
            print(
                f"[critique_node] score={result['total']}/25 "
                f"decision={result['decision']} "
                f"hallucination={result['hallucination_check']}"
            )
            return {"critic_result": dict(result)}
        except Exception as exc:
            msg = f"critique_node error: {exc}"
            logger.error(msg)
            return {
                "errors": (state.get("errors") or []) + [msg],
                "status": "failed",
            }

    def _post_node(self, state: InsightPulseState) -> dict:
        """Call LinkedInPoster.post(); log to Supabase posts table on success."""
        try:
            dry_run = state.get("dry_run", True)
            post_draft = state.get("post_draft") or {}
            critic_result = state.get("critic_result") or {}
            insight = state.get("insight") or {}
            content = post_draft.get("linkedin_post", "")

            result = self._poster.post(content, dry_run=dry_run)

            # Log to posts table for any outcome except hard errors / queue full
            if result["status"] in ("dry_run", "posted"):
                topic_id = self._db.log_topic(
                    topic=insight.get("topic", ""),
                    company=insight.get("company", ""),
                ) or ""
                self._db.log_post(
                    topic_id=topic_id,
                    linkedin_post=content,
                    critic_score=critic_result.get("total", 0),
                    decision="auto_post",
                )

            mode = "DRY RUN" if dry_run else "LIVE"
            print(
                f"[post_node] [{mode}] status={result['status']} "
                f"post_id={result['post_id']} score={critic_result.get('total', 0)}/25"
            )

            if result["status"] in ("dry_run", "posted"):
                return {"status": "success"}

            return {
                "errors": (state.get("errors") or []) + [
                    f"post_node: Buffer returned status={result['status']}"
                ],
                "status": "failed",
            }
        except Exception as exc:
            msg = f"post_node error: {exc}"
            logger.error(msg)
            return {
                "errors": (state.get("errors") or []) + [msg],
                "status": "failed",
            }

    def _alert_node(self, state: InsightPulseState) -> dict:
        """Send soft-approval or escalation alert."""
        try:
            critic_result = state.get("critic_result") or {}
            retry_count = state.get("retry_count", 0)
            decision = critic_result.get("decision", "auto_reject")

            if decision == "soft_approval":
                post_draft = state.get("post_draft") or {}
                self._critic.notify_if_needed(critic_result, post_draft)  # type: ignore[arg-type]
                print(
                    f"[alert_node] Soft-approval alert sent. "
                    f"Score={critic_result.get('total')}/25"
                )
                return {"status": "soft_approval"}

            # Escalation path
            print(
                f"[alert_node] ESCALATION: {retry_count} retries exhausted. "
                f"Score={critic_result.get('total', 0)}/25. Human review required."
            )
            return {"status": "escalated"}
        except Exception as exc:
            msg = f"alert_node error: {exc}"
            logger.error(msg)
            return {
                "errors": (state.get("errors") or []) + [msg],
                "status": "failed",
            }

    def _retry_node(self, state: InsightPulseState) -> dict:
        """Increment retry_count; select_topic_node will pick the next topic."""
        retry_count = (state.get("retry_count") or 0) + 1
        print(f"[retry_node] Auto-reject. retry_count -> {retry_count}. Picking next topic.")
        return {
            "retry_count": retry_count,
            "post_draft": {},
            "pm_brief": {},
            "critic_result": {},
            "writer_retry_count": 0,
        }

    def _end_node(self, state: InsightPulseState) -> dict:
        """Log final status; mark topic covered if successful."""
        try:
            current_status = state.get("status", "running")
            errors = state.get("errors") or []
            run_id = state.get("run_id", "")
            insight = state.get("insight") or {}
            critic_result = state.get("critic_result") or {}
            score = critic_result.get("total", 0)

            # Mark topic covered only on clean success
            if current_status == "success" and score > 0:
                topic_id = self._db.log_topic(
                    topic=insight.get("topic", ""),
                    company=insight.get("company", ""),
                ) or ""
                if topic_id:
                    self._db.mark_topic_covered(topic_id=topic_id, critic_score=score)

            final_status = current_status if current_status != "running" else "success"

            self._db.log_run(
                agent_name="orchestrator",
                status="success" if not errors else "failed",
                input_summary=f"run_id={run_id}",
                output_summary=(
                    f"pipeline_status={final_status} "
                    f"score={score} errors={len(errors)}"
                ),
            )
            print(f"[end_node] Done. status={final_status} errors={len(errors)}")
            return {"status": final_status}
        except Exception as exc:
            msg = f"end_node error: {exc}"
            logger.error(msg)
            return {
                "errors": (state.get("errors") or []) + [msg],
                "status": "failed",
            }
