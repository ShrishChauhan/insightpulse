"""Posts approved content to LinkedIn via the official LinkedIn Consumer API (ugcPosts)."""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import date, datetime, timezone
from typing import Optional

from typing_extensions import TypedDict

import config
from core.db import SupabaseClient

logger = logging.getLogger(__name__)

LINKEDIN_API_URL = "https://api.linkedin.com/v2/ugcPosts"
RATE_LIMIT_HOURS = 6
TOKEN_WARN_DAYS = 53  # LinkedIn tokens expire at 60 days; warn 7 days before


class PostResult(TypedDict):
    """Result returned by LinkedInPoster.post()."""

    success: bool
    post_id: Optional[str]
    status: str  # dry_run | posted | rate_limited | not_configured | error
    queued_at: Optional[str]


class LinkedInPoster:
    """Posts approved LinkedIn content via the official LinkedIn Consumer API."""

    def __init__(self, db: SupabaseClient) -> None:
        """Inject SupabaseClient for rate-limit checks and run logging."""
        self._db = db
        self._token = config.LINKEDIN_ACCESS_TOKEN
        self._person_urn = config.LINKEDIN_PERSON_URN
        self._org_urn = config.LINKEDIN_ORGANIZATION_URN
        self._token_created = config.LINKEDIN_TOKEN_CREATED

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def post(self, content: str, dry_run: bool = True) -> PostResult:
        """Post content to LinkedIn.

        dry_run=True: log to db, print content, return status='dry_run'.
        dry_run=False: POST to LinkedIn ugcPosts API if rate limit not exceeded.
        Returns PostResult -- never raises.
        """
        self._check_token_expiry()

        author_urn = self._org_urn or self._person_urn
        author_label = "InsightPulse (organization)" if self._org_urn else "personal profile"

        if not self._token or not author_urn:
            self._db.log_run(
                agent_name="linkedin_poster",
                status="skipped",
                input_summary="post() called",
                output_summary="LINKEDIN_ACCESS_TOKEN or author URN not set",
            )
            logger.warning("LinkedInPoster: credentials not configured.")
            return PostResult(
                success=False, post_id=None,
                status="not_configured", queued_at=None,
            )

        print(f"[linkedin_poster] Posting as: {author_label}")

        if dry_run:
            self._db.log_run(
                agent_name="linkedin_poster",
                status="success",
                input_summary=f"dry_run post len={len(content)}",
                output_summary="dry_run -- no LinkedIn API call made",
            )
            print(f"[linkedin_poster] DRY RUN -- would post to LinkedIn ({len(content)} chars):")
            # Encode safely for Windows terminal (cp1252 rejects emoji)
            safe_preview = content[:300].encode('ascii', errors='replace').decode('ascii')
            print(f"  {safe_preview}{'...' if len(content) > 300 else ''}")
            return PostResult(
                success=True, post_id=None,
                status="dry_run", queued_at=None,
            )

        # Live path: enforce rate limit
        if self._db.has_recent_post(hours=RATE_LIMIT_HOURS):
            msg = f"Rate limit: a post was already made in the last {RATE_LIMIT_HOURS} hours."
            logger.warning(msg)
            self._db.log_run(
                agent_name="linkedin_poster",
                status="skipped",
                input_summary=f"post() called len={len(content)}",
                output_summary=msg,
            )
            return PostResult(
                success=False, post_id=None,
                status="rate_limited", queued_at=None,
            )

        return self._call_linkedin_api(content, author_urn)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_token_expiry(self) -> None:
        """Print warning if token was issued >= 53 days ago (expires at 60 days)."""
        try:
            issued = date.fromisoformat(self._token_created)
            age_days = (date.today() - issued).days
            if age_days >= TOKEN_WARN_DAYS:
                remaining = 60 - age_days
                print(
                    f"[linkedin_poster] WARNING: LinkedIn token expires in ~{remaining} days"
                    f" -- re-authenticate soon."
                )
        except (ValueError, TypeError):
            pass

    def _call_linkedin_api(self, content: str, author_urn: str) -> PostResult:
        """POST content to LinkedIn ugcPosts endpoint; return PostResult."""
        try:
            body = {
                "author": author_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": content},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                },
            }
            payload = json.dumps(body).encode()
            req = urllib.request.Request(
                LINKEDIN_API_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                post_id = resp.headers.get("X-RestLi-Id") or resp.headers.get("x-restli-id")
                queued_at = datetime.now(timezone.utc).isoformat()

            self._db.log_run(
                agent_name="linkedin_poster",
                status="success",
                input_summary=f"post len={len(content)}",
                output_summary=f"posted post_id={post_id} at={queued_at}",
            )
            print(f"[linkedin_poster] LIVE -- posted to LinkedIn. post_id={post_id}")
            return PostResult(
                success=True, post_id=post_id,
                status="posted", queued_at=queued_at,
            )
        except Exception as exc:
            msg = str(exc)
            logger.error("LinkedIn API call failed: %s", msg)
            self._db.log_run(
                agent_name="linkedin_poster",
                status="failed",
                input_summary=f"post len={len(content)}",
                output_summary=f"error: {msg}",
                error=msg,
            )
            return PostResult(
                success=False, post_id=None,
                status="error", queued_at=None,
            )
