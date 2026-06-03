"""Auto-posts approved content to LinkedIn via Buffer API v1."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from typing_extensions import TypedDict

import config
from core.db import SupabaseClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypedDict
# ---------------------------------------------------------------------------

class PostResult(TypedDict):
    """Result returned by BufferPoster.post()."""

    success: bool
    post_id: Optional[str]
    status: str  # dry_run | posted | queue_full | not_configured | error
    queued_at: Optional[str]


# ---------------------------------------------------------------------------
# BufferPoster
# ---------------------------------------------------------------------------

class BufferPoster:
    """Posts approved LinkedIn content via Buffer API v1."""

    BUFFER_API_BASE = "https://api.bufferapp.com/1"
    QUEUE_LIMIT = 9  # Buffer free tier max is 10; halt at 9 for one slot of safety

    def __init__(self, db: SupabaseClient) -> None:
        """Inject SupabaseClient for run logging."""
        self._db = db
        self._token = config.BUFFER_ACCESS_TOKEN
        self._profile_id = config.BUFFER_LINKEDIN_PROFILE_ID

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def post(self, content: str, dry_run: bool = True) -> PostResult:
        """Queue content to Buffer LinkedIn profile.

        dry_run=True: log to Supabase, print content, return status='dry_run'.
        dry_run=False: check queue then POST to Buffer API.
        Returns PostResult — never raises.
        """
        if not self._token or not self._profile_id:
            self._db.log_run(
                agent_name="buffer_poster",
                status="skipped",
                input_summary="post() called",
                output_summary="BUFFER_ACCESS_TOKEN or BUFFER_LINKEDIN_PROFILE_ID not set",
            )
            logger.warning("BufferPoster: credentials not configured.")
            return PostResult(
                success=False, post_id=None,
                status="not_configured", queued_at=None,
            )

        if dry_run:
            self._db.log_run(
                agent_name="buffer_poster",
                status="success",
                input_summary=f"dry_run post len={len(content)}",
                output_summary="dry_run -- no Buffer API call made",
            )
            print(f"[buffer_poster] DRY RUN -- would post to LinkedIn ({len(content)} chars):")
            print(f"  {content[:300]}{'...' if len(content) > 300 else ''}")
            return PostResult(
                success=True, post_id=None,
                status="dry_run", queued_at=None,
            )

        # Live path: check queue before posting
        queue_count = self.check_queue_count()
        if queue_count >= self.QUEUE_LIMIT:
            msg = f"Buffer queue at {queue_count}/10 -- at limit, skipping post."
            logger.warning(msg)
            self._db.log_run(
                agent_name="buffer_poster",
                status="skipped",
                input_summary=f"post() called len={len(content)}",
                output_summary=msg,
            )
            return PostResult(
                success=False, post_id=None,
                status="queue_full", queued_at=None,
            )

        return self._call_buffer_api(content)

    def get_profiles(self) -> list[dict]:
        """Return all Buffer profiles -- helper to find BUFFER_LINKEDIN_PROFILE_ID."""
        if not self._token:
            logger.warning("BufferPoster.get_profiles: no access token configured.")
            return []
        try:
            url = f"{self.BUFFER_API_BASE}/profiles.json?access_token={self._token}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            logger.error("get_profiles failed: %s", exc)
            return []

    def check_queue_count(self) -> int:
        """Return number of posts currently pending in the Buffer queue."""
        if not self._token or not self._profile_id:
            return 0
        try:
            url = (
                f"{self.BUFFER_API_BASE}/profiles/{self._profile_id}"
                f"/updates/pending.json?access_token={self._token}"
            )
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                return int(data.get("total", len(data.get("updates", []))))
        except Exception as exc:
            logger.warning("check_queue_count failed (defaulting to 0): %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _call_buffer_api(self, content: str) -> PostResult:
        """POST content to Buffer API; return PostResult."""
        try:
            payload = urllib.parse.urlencode({
                "profile_ids[]": self._profile_id,
                "text": content,
                "access_token": self._token,
            }).encode()
            req = urllib.request.Request(
                f"{self.BUFFER_API_BASE}/updates/create.json",
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            updates = data.get("updates", [])
            if not updates:
                raise ValueError(f"Buffer API returned no updates: {data}")

            update = updates[0]
            post_id = update.get("id")
            raw_ts = update.get("created_at")
            queued_at = (
                datetime.fromtimestamp(raw_ts, tz=timezone.utc).isoformat()
                if raw_ts else None
            )

            self._db.log_run(
                agent_name="buffer_poster",
                status="success",
                input_summary=f"post len={len(content)}",
                output_summary=f"queued post_id={post_id} queued_at={queued_at}",
            )
            print(f"[buffer_poster] LIVE -- queued to Buffer. post_id={post_id}")
            return PostResult(
                success=True, post_id=post_id,
                status="posted", queued_at=queued_at,
            )
        except Exception as exc:
            msg = str(exc)
            logger.error("Buffer API call failed: %s", msg)
            self._db.log_run(
                agent_name="buffer_poster",
                status="failed",
                input_summary=f"post len={len(content)}",
                output_summary=f"error: {msg}",
                error=msg,
            )
            return PostResult(
                success=False, post_id=None,
                status="error", queued_at=None,
            )
