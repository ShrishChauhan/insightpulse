"""Finds trending topics across Reddit/HN/RSS and scores them for novelty."""

import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from typing_extensions import TypedDict

import config
from core.db import SupabaseClient
from core.llm_client import LLMClient
from prompts import SCOUT_SCORING_PROMPT


# ---------------------------------------------------------------------------
# Output TypedDict
# ---------------------------------------------------------------------------

class TopicCandidate(TypedDict):
    """Single scored topic candidate returned by ScoutAgent."""
    topic: str
    company: str
    post_count: int
    avg_score: float
    sentiment_shift: float
    last_covered: Optional[str]
    novelty_score: float
    recommended: bool


# ---------------------------------------------------------------------------
# Sentiment helpers
# ---------------------------------------------------------------------------

_POS = set(config.SENTIMENT_WORDS["positive"])
_NEG = set(config.SENTIMENT_WORDS["negative"])


def _sentiment_score(text: str) -> float:
    """Return a -1 to 1 sentiment score for a single text string.

    Counts positive and negative word hits (case-insensitive, word-boundary
    aware via simple split). Returns 0.0 if no sentiment words found.
    """
    words = text.lower().split()
    pos = sum(1 for w in words if w.strip(".,!?;:\"'()[]") in _POS)
    neg = sum(1 for w in words if w.strip(".,!?;:\"'()[]") in _NEG)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 4)


# ---------------------------------------------------------------------------
# ScoutAgent
# ---------------------------------------------------------------------------

class ScoutAgent:
    """Discovers and scores trending topics from the embeddings table."""

    def __init__(self, db: Optional[SupabaseClient] = None, llm: Optional[LLMClient] = None) -> None:
        """Initialise with optional injected db/llm for testing."""
        self._db = db or SupabaseClient()
        self._llm = llm or LLMClient()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def discover_topics(
        self,
        top_n: int = 5,
        _mock_rows: Optional[list[dict]] = None,
    ) -> list[TopicCandidate]:
        """Discover and rank trending topics.

        Steps:
        1. Pull last 14 days from embeddings, group by company_tags[0].
        2. Compute post_count, avg_score, sentiment_shift per company.
        3. Check recent topic history to apply coverage penalties.
        4. Score all topics in one LLM call (SCOUT_SCORING_PROMPT).
        5. Compute novelty_score in Python; return top_n ranked.

        Args:
            top_n: Maximum candidates to return (default 5).
            _mock_rows: Inject fake embedding rows for dry-run testing.
        """
        t0 = time.time()
        try:
            rows = _mock_rows if _mock_rows is not None else self._fetch_embeddings(days=14)
            aggregated = self._aggregate_by_company(rows)

            if not aggregated:
                self._db.log_run(
                    agent_name="scout",
                    status="skipped",
                    input_summary="0 embedding rows in last 14 days",
                    output_summary="no topics found",
                    duration_ms=int((time.time() - t0) * 1000),
                )
                return []

            recent_topics = self._db.get_recent_topics(days=config.TOPIC_REPEAT_DAYS)
            coverage_map = self._build_coverage_map(recent_topics)

            llm_scores = self._score_with_llm(aggregated, coverage_map)
            candidates = self._build_candidates(aggregated, coverage_map, llm_scores)
            candidates.sort(key=lambda c: c["novelty_score"], reverse=True)
            top = candidates[:top_n]

            self._db.log_run(
                agent_name="scout",
                status="success",
                input_summary=f"{len(rows)} embedding rows, {len(aggregated)} companies",
                output_summary=f"top {len(top)} topics: {[c['topic'] for c in top]}",
                duration_ms=int((time.time() - t0) * 1000),
            )
            return top

        except Exception as exc:
            self._db.log_run(
                agent_name="scout",
                status="failed",
                error=str(exc),
                duration_ms=int((time.time() - t0) * 1000),
            )
            raise

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_embeddings(self, days: int = 14) -> list[dict]:
        """Fetch all embedding rows created in the last N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            result = (
                self._db._client.table("embeddings")
                .select("content, company_tags, post_score, created_at")
                .gt("created_at", cutoff)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            print(f"[scout] _fetch_embeddings failed: {exc}")
            return []

    def _aggregate_by_company(self, rows: list[dict]) -> dict[str, dict]:
        """Group embedding rows by company_tags[0]; compute counts and scores.

        Returns dict keyed by company name:
        {company: {topic, post_count, scores_this_week, scores_last_week,
                   texts_this_week, texts_last_week}}
        """
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        aggregated: dict[str, dict] = {}
        for row in rows:
            tags = row.get("company_tags") or []
            if not tags:
                continue
            company = tags[0].lower().strip()
            if company not in aggregated:
                aggregated[company] = {
                    "topic": f"{company.capitalize()} user sentiment",
                    "company": company,
                    "scores_all": [],
                    "scores_this_week": [],
                    "scores_last_week": [],
                    "texts_this_week": [],
                    "texts_last_week": [],
                }
            entry = aggregated[company]
            score = row.get("post_score")
            if score is not None:
                entry["scores_all"].append(score)

            # Parse created_at for sentiment window split
            try:
                created = datetime.fromisoformat(
                    row["created_at"].replace("Z", "+00:00")
                )
            except (KeyError, ValueError):
                created = now  # fallback: treat as current week

            text = row.get("content", "")
            if created >= week_ago:
                entry["texts_this_week"].append(text)
                if score is not None:
                    entry["scores_this_week"].append(score)
            else:
                entry["texts_last_week"].append(text)
                if score is not None:
                    entry["scores_last_week"].append(score)

        return aggregated

    def _calc_sentiment_shift(self, entry: dict) -> float:
        """Compute sentiment_shift = this_week_avg - last_week_avg on -1..1 scale."""
        def avg_sentiment(texts: list[str]) -> float:
            if not texts:
                return 0.0
            scores = [_sentiment_score(t) for t in texts]
            return sum(scores) / len(scores)

        this_week = avg_sentiment(entry["texts_this_week"])
        last_week = avg_sentiment(entry["texts_last_week"])
        return round(this_week - last_week, 4)

    def _build_coverage_map(self, recent_topics: list) -> dict[str, Optional[str]]:
        """Map company name -> last_covered ISO string (or None if not recently covered)."""
        coverage: dict[str, Optional[str]] = {}
        for t in recent_topics:
            company = (t.get("company") or "").lower().strip()
            last = t.get("last_covered")
            if company and last:
                # Keep the most recent coverage date if duplicates
                if company not in coverage or last > coverage[company]:
                    coverage[company] = last
        return coverage

    def _score_with_llm(
        self,
        aggregated: dict[str, dict],
        coverage_map: dict[str, Optional[str]],
    ) -> dict[str, float]:
        """One LLM call scores all topics; returns {company: llm_score}."""
        topics_payload = []
        for company, entry in aggregated.items():
            scores = entry["scores_all"]
            avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
            sentiment_shift = self._calc_sentiment_shift(entry)
            last_covered = coverage_map.get(company)
            topics_payload.append({
                "topic": entry["topic"],
                "company": company,
                "post_count": len(entry["texts_this_week"]) + len(entry["texts_last_week"]),
                "avg_score": avg_score,
                "sentiment_shift": sentiment_shift,
                "last_covered": last_covered or "never",
            })

        user_msg = f"Topics to score:\n{topics_payload}"
        result = self._llm.complete_json(
            system=SCOUT_SCORING_PROMPT,
            user=user_msg,
        )

        llm_scores: dict[str, float] = {}
        for item in result.get("ranked_topics", []):
            company = (item.get("company") or "").lower().strip()
            llm_scores[company] = float(item.get("score", 0.0))
        return llm_scores

    def _build_candidates(
        self,
        aggregated: dict[str, dict],
        coverage_map: dict[str, Optional[str]],
        llm_scores: dict[str, float],
    ) -> list[TopicCandidate]:
        """Compute novelty_score for each company and build TopicCandidate list."""
        all_counts = [
            len(e["texts_this_week"]) + len(e["texts_last_week"])
            for e in aggregated.values()
        ]
        max_count = max(all_counts) if all_counts else 1

        candidates: list[TopicCandidate] = []
        now = datetime.now(timezone.utc)

        for company, entry in aggregated.items():
            scores = entry["scores_all"]
            avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
            post_count = len(entry["texts_this_week"]) + len(entry["texts_last_week"])
            sentiment_shift = self._calc_sentiment_shift(entry)
            last_covered = coverage_map.get(company)
            llm_score = llm_scores.get(company, 0.0)

            # Normalize post_count to 0..1
            norm_count = post_count / max_count if max_count > 0 else 0.0

            # Clamp llm_score to 0..1 (LLM may return 0-10 or 0-1 scale)
            norm_llm = min(llm_score / 10.0, 1.0) if llm_score > 1.0 else llm_score

            # Base novelty score
            novelty = (
                abs(sentiment_shift) * 0.4
                + norm_count * 0.3
                + norm_llm * 0.3
            )

            # Coverage penalties/bonuses
            days_since_covered: Optional[float] = None
            if last_covered:
                try:
                    lc_dt = datetime.fromisoformat(last_covered.replace("Z", "+00:00"))
                    days_since_covered = (now - lc_dt).total_seconds() / 86400
                except ValueError:
                    pass

            if days_since_covered is None:
                novelty += 1.0   # never covered bonus
            elif days_since_covered < 30:
                novelty -= 3.0   # covered recently penalty
            elif days_since_covered < 60:
                novelty -= 1.0   # covered somewhat recently penalty

            candidates.append(TopicCandidate(
                topic=entry["topic"],
                company=company,
                post_count=post_count,
                avg_score=avg_score,
                sentiment_shift=sentiment_shift,
                last_covered=last_covered,
                novelty_score=round(novelty, 4),
                recommended=novelty >= 0.5,
            ))

        return candidates


# ---------------------------------------------------------------------------
# Dry-run test
# ---------------------------------------------------------------------------

_MOCK_ROWS: list[dict] = [
    {
        "content": "Spotify playlist recommendations are amazing and fast, love the new UI",
        "company_tags": ["spotify"],
        "post_score": 142,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    },
    {
        "content": "Spotify shuffle is broken and buggy, terrible experience, hate the crashes",
        "company_tags": ["spotify"],
        "post_score": 87,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    },
    {
        "content": "Apple Maps is confusing and slow compared to Google Maps, frustrating glitch",
        "company_tags": ["apple"],
        "post_score": 310,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    },
    {
        "content": "Apple Vision Pro is outstanding and innovative, brilliant device launch",
        "company_tags": ["apple"],
        "post_score": 275,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
    },
    {
        "content": "Notion AI is slow and laggy on large pages, delay is painful and frustrating",
        "company_tags": ["notion"],
        "post_score": 95,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(),
    },
    {
        "content": "Notion templates are excellent and useful, great for productivity",
        "company_tags": ["notion"],
        "post_score": 120,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
    },
    {
        "content": "Google search results getting worse, spam and misleading ads everywhere",
        "company_tags": ["google"],
        "post_score": 430,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    },
    {
        "content": "Netflix removed downloads feature, worst decision, useless for offline",
        "company_tags": ["netflix"],
        "post_score": 210,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    },
]


def _print_table(candidates: list[TopicCandidate]) -> None:
    """Print ranked TopicCandidate list as ASCII table."""
    header = f"{'#':<3} {'Company':<12} {'Posts':<7} {'AvgScore':<10} {'SentShift':<11} {'Novelty':<9} {'Rec':<5} {'LastCovered'}"
    print(header)
    print("-" * len(header))
    for i, c in enumerate(candidates, 1):
        lc = (c["last_covered"] or "never")[:10]
        rec = "YES" if c["recommended"] else "no"
        print(
            f"{i:<3} {c['company']:<12} {c['post_count']:<7} "
            f"{c['avg_score']:<10.2f} {c['sentiment_shift']:<11.4f} "
            f"{c['novelty_score']:<9.4f} {rec:<5} {lc}"
        )


if __name__ == "__main__":
    print("Scout dry-run — using mock rows + real Supabase topic history\n")
    agent = ScoutAgent()

    # Supplement with real Supabase rows if any exist; use mocks as baseline
    real_rows: list[dict] = []
    try:
        real_rows = agent._fetch_embeddings(days=14)
        print(f"  Supabase returned {len(real_rows)} real rows (last 14 days)")
    except Exception as e:
        print(f"  Supabase fetch failed: {e}")

    combined = real_rows if len(real_rows) >= 5 else _MOCK_ROWS + real_rows
    print(f"  Using {len(combined)} rows total ({len(real_rows)} real, {len(_MOCK_ROWS) if len(real_rows) < 5 else 0} mock)\n")

    results = agent.discover_topics(top_n=5, _mock_rows=combined)

    print(f"Top {len(results)} topics ranked by novelty_score:\n")
    _print_table(results)
    print(f"\nRun logged to Supabase.")
