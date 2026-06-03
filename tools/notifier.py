"""Slack webhook and console notifications for InsightPulse pipeline events."""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)


class Notifier:
    """Sends console + optional Slack notifications for pipeline events.

    Works without Slack configured -- all methods fall back to console-only.
    Never raises; all errors are logged as warnings.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_soft_approval(self, post_draft: dict, critic_result: dict) -> bool:
        """Alert that a post needs human review (score in soft_approval range).

        Always prints formatted alert to console.
        Also POSTs to Slack if SLACK_WEBHOOK_URL is set.
        Returns True if alert was delivered without error.
        """
        scores = critic_result.get("scores", {})
        total = critic_result.get("total", 0)
        decision = critic_result.get("decision", "soft_approval")
        weakness = critic_result.get("primary_weakness", "N/A")
        post_text = post_draft.get("linkedin_post", "")
        preview = post_text[:200]

        lines = [
            "[InsightPulse] SOFT APPROVAL REQUIRED",
            f"Score: {total}/25 | Decision: {decision}",
            f"  factual_grounding   : {scores.get('factual_grounding', '?')}/5",
            f"  insight_novelty     : {scores.get('insight_novelty', '?')}/5",
            f"  writing_quality     : {scores.get('writing_quality', '?')}/5",
            f"  pm_relevance        : {scores.get('pm_relevance', '?')}/5",
            f"  engagement_potential: {scores.get('engagement_potential', '?')}/5",
            f"Primary weakness: {weakness}",
            f"Post preview: {preview}{'...' if len(post_text) > 200 else ''}",
            "Approve? Check dashboard or reply Y/N in Slack",
        ]
        message = "\n".join(lines)
        print(f"\n{message}\n")

        if config.SLACK_WEBHOOK_URL:
            return self._send_slack(message)
        return True

    def notify_weekly_digest(self, stats: dict) -> bool:
        """Print weekly summary to console; also send to Slack if configured.

        Expected stats keys: posts_sent, avg_critic_score, top_topic, next_run_time.
        Returns True if delivered without error.
        """
        posts_sent = stats.get("posts_sent", 0)
        avg_score = stats.get("avg_critic_score", 0)
        top_topic = stats.get("top_topic", "N/A")
        next_run = stats.get("next_run_time", "N/A")

        lines = [
            "[InsightPulse] Weekly Digest",
            f"  Posts sent this week  : {posts_sent}",
            f"  Avg critic score      : {avg_score}/25",
            f"  Top topic             : {top_topic}",
            f"  Next run              : {next_run}",
        ]
        message = "\n".join(lines)
        print(f"\n{message}\n")

        if config.SLACK_WEBHOOK_URL:
            return self._send_slack(message)
        return True

    def notify_error(self, agent: str, error: str) -> bool:
        """Fire on orchestrator escalation (retry_count >= 3).

        Always prints to console. Also sends to Slack if configured.
        Returns True if delivered without error.
        """
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        message = (
            f"[InsightPulse] ERROR ESCALATION\n"
            f"Agent : {agent}\n"
            f"Time  : {ts}\n"
            f"Error : {error}"
        )
        print(f"\n{message}\n")

        if config.SLACK_WEBHOOK_URL:
            return self._send_slack(message)
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send_slack(self, message: str) -> bool:
        """POST plain-text message to Slack webhook URL."""
        try:
            payload = json.dumps({"text": message}).encode()
            req = urllib.request.Request(
                config.SLACK_WEBHOOK_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception as exc:
            logger.warning("Slack webhook failed: %s", exc)
            return False
