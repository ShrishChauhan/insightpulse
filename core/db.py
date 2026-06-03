"""Supabase PostgreSQL client: run logging, topic history, post records, embeddings."""

import hashlib
import time
from typing import Optional

from supabase import create_client, Client
from typing_extensions import TypedDict

import config


# ---------------------------------------------------------------------------
# TypedDicts for all query return types
# ---------------------------------------------------------------------------

class RunRecord(TypedDict):
    id: str
    created_at: str
    agent_name: str
    status: str
    input_summary: Optional[str]
    output_summary: Optional[str]
    tokens_used: Optional[int]
    duration_ms: Optional[int]
    error: Optional[str]


class TopicRecord(TypedDict):
    id: str
    topic: str
    company: str
    first_seen: str
    last_covered: Optional[str]
    cover_count: int
    avg_critic_score: Optional[float]


class PostRecord(TypedDict):
    id: str
    topic_id: str
    linkedin_post: str
    pm_brief_path: Optional[str]
    critic_score: int
    decision: str
    posted_at: Optional[str]
    engagement_score: Optional[int]


class EmbeddingRecord(TypedDict):
    id: str
    content: str
    source_url: Optional[str]
    subreddit: Optional[str]
    company_tags: Optional[list]
    post_score: Optional[int]
    created_at: str
    content_hash: str


class RunStats(TypedDict):
    total_runs: int
    success_count: int
    failed_count: int
    skipped_count: int
    success_rate: float
    avg_tokens: Optional[float]


# ---------------------------------------------------------------------------
# SupabaseClient
# ---------------------------------------------------------------------------

class SupabaseClient:
    """Wraps all Supabase interactions for InsightPulse."""

    def __init__(self) -> None:
        """Initialise supabase-py client from config."""
        self._client: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

    # ------------------------------------------------------------------
    # Run logging
    # ------------------------------------------------------------------

    def log_run(
        self,
        agent_name: str,
        status: str,
        input_summary: str = "",
        output_summary: str = "",
        tokens_used: int = 0,
        duration_ms: int = 0,
        error: Optional[str] = None,
    ) -> Optional[str]:
        """Insert a run record; returns the new row id or None on failure."""
        try:
            row = {
                "agent_name": agent_name,
                "status": status,
                "input_summary": input_summary,
                "output_summary": output_summary,
                "tokens_used": tokens_used,
                "duration_ms": duration_ms,
                "error": error,
            }
            result = self._client.table("runs").insert(row).execute()
            return result.data[0]["id"] if result.data else None
        except Exception as exc:
            print(f"[db] log_run failed: {exc}")
            return None

    def get_run_stats(self) -> RunStats:
        """Return aggregate stats across all runs."""
        try:
            result = self._client.table("runs").select("status, tokens_used").execute()
            rows = result.data or []
            total = len(rows)
            success = sum(1 for r in rows if r["status"] == "success")
            failed = sum(1 for r in rows if r["status"] == "failed")
            skipped = sum(1 for r in rows if r["status"] == "skipped")
            tokens = [r["tokens_used"] for r in rows if r["tokens_used"] is not None]
            return RunStats(
                total_runs=total,
                success_count=success,
                failed_count=failed,
                skipped_count=skipped,
                success_rate=round(success / total, 3) if total else 0.0,
                avg_tokens=round(sum(tokens) / len(tokens), 1) if tokens else None,
            )
        except Exception as exc:
            print(f"[db] get_run_stats failed: {exc}")
            return RunStats(
                total_runs=0, success_count=0, failed_count=0,
                skipped_count=0, success_rate=0.0, avg_tokens=None,
            )

    # ------------------------------------------------------------------
    # Topic management
    # ------------------------------------------------------------------

    def log_topic(self, topic: str, company: str) -> Optional[str]:
        """Upsert a topic (match on topic + company); return topic id."""
        try:
            # Check if it already exists
            result = (
                self._client.table("topics")
                .select("id")
                .eq("topic", topic)
                .eq("company", company)
                .limit(1)
                .execute()
            )
            if result.data:
                return result.data[0]["id"]
            # Insert new
            insert = self._client.table("topics").insert(
                {"topic": topic, "company": company}
            ).execute()
            return insert.data[0]["id"] if insert.data else None
        except Exception as exc:
            print(f"[db] log_topic failed: {exc}")
            return None

    def get_recent_topics(self, days: int = 30) -> list[TopicRecord]:
        """Return topics covered in the last N days."""
        from datetime import datetime, timezone, timedelta
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            result = (
                self._client.table("topics")
                .select("*")
                .gt("last_covered", cutoff)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            print(f"[db] get_recent_topics failed: {exc}")
            return []

    def mark_topic_covered(self, topic_id: str, critic_score: int) -> None:
        """Update last_covered, increment cover_count, recalculate avg_critic_score."""
        try:
            # Fetch current values first
            result = (
                self._client.table("topics")
                .select("cover_count, avg_critic_score")
                .eq("id", topic_id)
                .single()
                .execute()
            )
            if not result.data:
                return
            current = result.data
            count = current["cover_count"] + 1
            prev_avg = current["avg_critic_score"] or 0.0
            new_avg = round(((prev_avg * (count - 1)) + critic_score) / count, 2)

            self._client.table("topics").update({
                "last_covered": "now()",
                "cover_count": count,
                "avg_critic_score": new_avg,
            }).eq("id", topic_id).execute()
        except Exception as exc:
            print(f"[db] mark_topic_covered failed: {exc}")

    # ------------------------------------------------------------------
    # Post logging
    # ------------------------------------------------------------------

    def log_post(
        self,
        topic_id: str,
        linkedin_post: str,
        critic_score: int,
        decision: str,
        pm_brief_path: Optional[str] = None,
    ) -> Optional[str]:
        """Insert a post record; return the new row id."""
        try:
            row = {
                "topic_id": topic_id,
                "linkedin_post": linkedin_post,
                "critic_score": critic_score,
                "decision": decision,
                "pm_brief_path": pm_brief_path,
            }
            result = self._client.table("posts").insert(row).execute()
            return result.data[0]["id"] if result.data else None
        except Exception as exc:
            print(f"[db] log_post failed: {exc}")
            return None

    def update_engagement(self, post_id: str, engagement_score: int) -> None:
        """Update engagement_score on an existing post."""
        try:
            self._client.table("posts").update(
                {"engagement_score": engagement_score}
            ).eq("id", post_id).execute()
        except Exception as exc:
            print(f"[db] update_engagement failed: {exc}")

    # ------------------------------------------------------------------
    # Embeddings / RAG
    # ------------------------------------------------------------------

    def store_embedding(
        self,
        content: str,
        embedding: list[float],
        metadata: dict,
    ) -> Optional[str]:
        """Insert embedding if content_hash not already present; return id or None if skipped."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        try:
            # Deduplication check
            existing = (
                self._client.table("embeddings")
                .select("id")
                .eq("content_hash", content_hash)
                .limit(1)
                .execute()
            )
            if existing.data:
                return None  # already embedded

            row = {
                "content": content,
                "embedding": embedding,
                "content_hash": content_hash,
                "source_url": metadata.get("source_url"),
                "subreddit": metadata.get("subreddit"),
                "company_tags": metadata.get("company_tags", []),
                "post_score": metadata.get("post_score"),
            }
            result = self._client.table("embeddings").insert(row).execute()
            return result.data[0]["id"] if result.data else None
        except Exception as exc:
            print(f"[db] store_embedding failed: {exc}")
            return None

    def bulk_store_embeddings(
        self,
        items: list[dict],
    ) -> tuple[int, int]:
        """Upsert embeddings in bulk, ignoring duplicates by content_hash.

        Each item: {"content": str, "embedding": list[float], "metadata": dict}.
        Uses DB-level upsert (ON CONFLICT DO NOTHING) — no pre-check needed.
        Returns (inserted_count, skipped_count).
        """
        if not items:
            return 0, 0

        rows = []
        for item in items:
            h = hashlib.sha256(item["content"].encode()).hexdigest()
            meta = item["metadata"]
            rows.append({
                "content": item["content"],
                "embedding": item["embedding"],
                "content_hash": h,
                "source_url": meta.get("source_url"),
                "subreddit": meta.get("subreddit"),
                "company_tags": meta.get("company_tags", []),
                "post_score": meta.get("post_score"),
            })

        inserted = 0
        try:
            for i in range(0, len(rows), 50):
                batch = rows[i : i + 50]
                result = (
                    self._client.table("embeddings")
                    .upsert(batch, on_conflict="content_hash", ignore_duplicates=True)
                    .execute()
                )
                inserted += len(result.data or [])
        except Exception as exc:
            print(f"[db] bulk_store_embeddings upsert failed: {exc}")

        skipped = len(rows) - inserted
        return inserted, skipped

    def get_embedding_stats(self) -> dict:
        """Return total count, oldest/newest timestamps, and per-company breakdown."""
        try:
            result = (
                self._client.table("embeddings")
                .select("created_at, company_tags")
                .execute()
            )
            rows = result.data or []
            if not rows:
                return {"total_vectors": 0, "oldest": None, "newest": None, "by_company": {}}
            dates = sorted(r["created_at"] for r in rows)
            by_company: dict[str, int] = {}
            for r in rows:
                for tag in (r.get("company_tags") or []):
                    by_company[tag] = by_company.get(tag, 0) + 1
            return {
                "total_vectors": len(rows),
                "oldest": dates[0],
                "newest": dates[-1],
                "by_company": by_company,
            }
        except Exception as exc:
            print(f"[db] get_embedding_stats failed: {exc}")
            return {"total_vectors": 0, "oldest": None, "newest": None, "by_company": {}}

    def delete_old_embeddings(self, days: int = 30) -> int:
        """Delete embeddings older than N days; return deleted count."""
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            result = (
                self._client.table("embeddings")
                .delete()
                .lt("created_at", cutoff)
                .execute()
            )
            return len(result.data or [])
        except Exception as exc:
            print(f"[db] delete_old_embeddings failed: {exc}")
            return 0

    def similarity_search(
        self,
        query_embedding: list[float],
        match_count: int = 15,
        company_filter: Optional[str] = None,
    ) -> list[dict]:
        """Cosine similarity search; optionally filter by company tag."""
        try:
            params: dict = {
                "query_embedding": str(query_embedding),  # pgvector text literal: "[f1, f2, ...]"
                "match_count": match_count,
            }
            if company_filter:
                params["company_filter"] = company_filter

            fn = "match_embeddings_filtered" if company_filter else "match_embeddings"
            result = self._client.rpc(fn, params).execute()
            return result.data or []
        except Exception as exc:
            print(f"[db] similarity_search failed: {exc}")
            return []


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------

def test_connection() -> None:
    """Insert and retrieve one row from each table, then clean up."""
    db = SupabaseClient()
    print("Testing Supabase connection...\n")

    # 1. runs
    run_id = db.log_run(
        agent_name="test_agent",
        status="success",
        input_summary="test input",
        output_summary="test output",
        tokens_used=42,
        duration_ms=100,
    )
    assert run_id, "runs insert failed"
    print(f"  runs       OK  id={run_id}")

    # 2. topics
    topic_id = db.log_topic("AI product launches", "apple")
    assert topic_id, "topics insert failed"
    print(f"  topics     OK  id={topic_id}")

    # 3. posts
    post_id = db.log_post(
        topic_id=topic_id,
        linkedin_post="Test post content.",
        critic_score=20,
        decision="auto_post",
    )
    assert post_id, "posts insert failed"
    print(f"  posts      OK  id={post_id}")

    # 4. learning_log
    from supabase import create_client
    client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    log_result = client.table("learning_log").insert(
        {"lesson": "test_connection smoke test"}
    ).execute()
    log_id = log_result.data[0]["id"]
    assert log_id, "learning_log insert failed"
    print(f"  learning   OK  id={log_id}")

    # 5. embeddings (dummy 384-dim vector)
    dummy_embedding = [0.01] * 384
    emb_id = db.store_embedding(
        content="Test content for embedding deduplication check.",
        embedding=dummy_embedding,
        metadata={"source_url": "https://example.com", "company_tags": ["apple"]},
    )
    assert emb_id, "embeddings insert failed"
    print(f"  embeddings OK  id={emb_id}")

    # Cleanup
    client.table("runs").delete().eq("id", run_id).execute()
    client.table("posts").delete().eq("id", post_id).execute()
    client.table("topics").delete().eq("id", topic_id).execute()
    client.table("learning_log").delete().eq("id", log_id).execute()
    client.table("embeddings").delete().eq("id", emb_id).execute()
    print("\nCleanup done. All tables verified. Connection healthy.")


if __name__ == "__main__":
    test_connection()
