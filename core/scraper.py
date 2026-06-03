"""Reddit (PRAW), HN Firebase API, and RSS feed ingestion.

All scrapers return List[ScrapedPost] and log each run to Supabase via
core/db.py. Never raises — failed sources are skipped and logged.
"""

import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import feedparser
import praw
import requests
from typing_extensions import TypedDict

import config
from core.db import SupabaseClient


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class ScrapedPost(TypedDict):
    """Unified content record returned by all scrapers."""
    id: str
    title: str
    body: str
    comments: list[str]
    subreddit: str
    score: int
    created_utc: float
    url: str
    company_tags: list[str]
    source_type: str   # "reddit" | "hn" | "rss"
    source_name: str   # "r/apple" | "hackernews" | "TechCrunch" etc.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_company_tags(text: str) -> list[str]:
    """Return COMPANY_TAGS entries that appear (case-insensitive) in text."""
    lower = text.lower()
    return [tag for tag in config.COMPANY_TAGS if tag in lower]


def _cutoff_utc(days: int = config.DAYS_LOOKBACK) -> float:
    """Unix timestamp for N days ago."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()


def _retry(fn, retries: int = 3, base_delay: float = 1.0):
    """Call fn(); on exception retry up to `retries` times with exponential backoff."""
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = base_delay * (2 ** attempt)
            print(f"[scraper] retry {attempt + 1}/{retries} after {wait:.1f}s: {exc}")
            time.sleep(wait)


# ---------------------------------------------------------------------------
# RedditScraper
# ---------------------------------------------------------------------------

class RedditScraper:
    """Fetches top posts + comments from TARGET_SUBREDDITS via PRAW."""

    AGENT_NAME = "scraper_reddit"
    MAX_POSTS = 50
    MAX_COMMENTS = 10
    MIN_SCORE = 10

    def __init__(self) -> None:
        """Initialise PRAW client from config credentials."""
        self._reddit = praw.Reddit(
            client_id=config.REDDIT_CLIENT_ID,
            client_secret=config.REDDIT_CLIENT_SECRET,
            user_agent=config.REDDIT_USER_AGENT,
        )
        self._db = SupabaseClient()

    def scrape(self) -> list[ScrapedPost]:
        """Fetch posts from all TARGET_SUBREDDITS; return deduplicated list."""
        start = time.time()
        posts: list[ScrapedPost] = []
        cutoff = _cutoff_utc()
        errors: list[str] = []

        for sub_name in config.TARGET_SUBREDDITS:
            try:
                fetched = _retry(lambda s=sub_name: self._scrape_subreddit(s, cutoff))
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"r/{sub_name}: {exc}")
                print(f"[reddit] skipping r/{sub_name}: {exc}")

        duration_ms = int((time.time() - start) * 1000)
        status = "failed" if len(errors) == len(config.TARGET_SUBREDDITS) else "success"

        self._db.log_run(
            agent_name=self.AGENT_NAME,
            status=status,
            input_summary=f"subreddits={len(config.TARGET_SUBREDDITS)}",
            output_summary=f"posts={len(posts)} errors={len(errors)}",
            duration_ms=duration_ms,
            error="; ".join(errors) if errors else None,
        )
        return posts

    def _scrape_subreddit(self, sub_name: str, cutoff: float) -> list[ScrapedPost]:
        """Fetch up to MAX_POSTS from one subreddit; filter by date and score."""
        sub = self._reddit.subreddit(sub_name)
        posts: list[ScrapedPost] = []

        for submission in sub.top(time_filter="week", limit=self.MAX_POSTS):
            if submission.created_utc < cutoff:
                continue
            if submission.score < self.MIN_SCORE:
                continue

            comments = self._fetch_comments(submission)
            full_text = f"{submission.title} {submission.selftext} {' '.join(comments)}"

            posts.append(ScrapedPost(
                id=f"reddit_{submission.id}",
                title=submission.title,
                body=submission.selftext,
                comments=comments,
                subreddit=sub_name,
                score=submission.score,
                created_utc=submission.created_utc,
                url=f"https://reddit.com{submission.permalink}",
                company_tags=_detect_company_tags(full_text),
                source_type="reddit",
                source_name=f"r/{sub_name}",
            ))

        return posts

    def _fetch_comments(self, submission) -> list[str]:
        """Return top MAX_COMMENTS comment bodies; skip MoreComments."""
        submission.comment_sort = "top"
        submission.comments.replace_more(limit=0)
        bodies: list[str] = []
        for comment in submission.comments.list()[:self.MAX_COMMENTS]:
            if hasattr(comment, "body") and comment.body not in ("[deleted]", "[removed]"):
                bodies.append(comment.body)
        return bodies


# ---------------------------------------------------------------------------
# HNScraper
# ---------------------------------------------------------------------------

class HNScraper:
    """Fetches top HN stories + comments via the Firebase API (no key needed)."""

    AGENT_NAME = "scraper_hn"
    BASE_URL = "https://hacker-news.firebaseio.com/v0"
    MAX_STORIES = 30
    MAX_COMMENTS = 10
    TIMEOUT = 10

    # Keywords that signal a tech company/product discussion
    _RELEVANCE_KEYWORDS = {
        "apple", "google", "spotify", "notion", "microsoft", "netflix",
        "amazon", "meta", "openai", "anthropic", "product", "launch",
        "startup", "saas", "ai", "llm", "app", "software", "api",
        "platform", "model", "tool",
    }

    def __init__(self) -> None:
        """Initialise DB client and HTTP session."""
        self._db = SupabaseClient()
        self._session = requests.Session()

    def scrape(self) -> list[ScrapedPost]:
        """Fetch top HN stories filtered for tech relevance."""
        start = time.time()
        posts: list[ScrapedPost] = []
        error_msg: Optional[str] = None
        cutoff = _cutoff_utc()

        try:
            story_ids = _retry(self._fetch_top_ids)
            for story_id in story_ids[:200]:  # oversample; filter below
                if len(posts) >= self.MAX_STORIES:
                    break
                try:
                    post = _retry(lambda sid=story_id: self._fetch_story(sid, cutoff))
                    if post:
                        posts.append(post)
                except Exception as exc:
                    print(f"[hn] skipping story {story_id}: {exc}")
        except Exception as exc:
            error_msg = str(exc)
            print(f"[hn] scrape failed: {exc}")

        duration_ms = int((time.time() - start) * 1000)
        self._db.log_run(
            agent_name=self.AGENT_NAME,
            status="failed" if error_msg else "success",
            input_summary="source=hackernews",
            output_summary=f"posts={len(posts)}",
            duration_ms=duration_ms,
            error=error_msg,
        )
        return posts

    def _fetch_top_ids(self) -> list[int]:
        """Return list of top story IDs from HN."""
        resp = self._session.get(f"{self.BASE_URL}/topstories.json", timeout=self.TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _fetch_story(self, story_id: int, cutoff: float) -> Optional[ScrapedPost]:
        """Fetch one story; return None if too old or not tech-relevant."""
        resp = self._session.get(
            f"{self.BASE_URL}/item/{story_id}.json", timeout=self.TIMEOUT
        )
        resp.raise_for_status()
        item = resp.json()

        if not item or item.get("type") != "story":
            return None
        if item.get("time", 0) < cutoff:
            return None

        title: str = item.get("title", "")
        if not self._is_relevant(title):
            return None

        comments = self._fetch_comments(item.get("kids", []))
        full_text = f"{title} {' '.join(comments)}"
        url: str = item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"

        return ScrapedPost(
            id=f"hn_{story_id}",
            title=title,
            body=item.get("text", ""),
            comments=comments,
            subreddit="",
            score=item.get("score", 0),
            created_utc=float(item.get("time", 0)),
            url=url,
            company_tags=_detect_company_tags(full_text),
            source_type="hn",
            source_name="hackernews",
        )

    def _fetch_comments(self, kid_ids: list[int]) -> list[str]:
        """Fetch up to MAX_COMMENTS top-level comment texts."""
        bodies: list[str] = []
        for kid_id in kid_ids[:self.MAX_COMMENTS]:
            try:
                resp = self._session.get(
                    f"{self.BASE_URL}/item/{kid_id}.json", timeout=self.TIMEOUT
                )
                resp.raise_for_status()
                item = resp.json()
                if item and item.get("type") == "comment" and item.get("text"):
                    bodies.append(item["text"])
            except Exception:
                pass
        return bodies

    def _is_relevant(self, title: str) -> bool:
        """Return True if title contains at least one relevance keyword."""
        lower = title.lower()
        return any(kw in lower for kw in self._RELEVANCE_KEYWORDS)


# ---------------------------------------------------------------------------
# RSScraper
# ---------------------------------------------------------------------------

class RSScraper:
    """Parses RSS feeds from RSS_FEEDS config; normalises into ScrapedPost."""

    AGENT_NAME = "scraper_rss"

    def __init__(self) -> None:
        """Initialise DB client."""
        self._db = SupabaseClient()

    def scrape(self) -> list[ScrapedPost]:
        """Parse all RSS_FEEDS; return posts from the last DAYS_LOOKBACK days."""
        start = time.time()
        posts: list[ScrapedPost] = []
        errors: list[str] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=config.DAYS_LOOKBACK)

        for feed_cfg in config.RSS_FEEDS:
            try:
                fetched = _retry(
                    lambda cfg=feed_cfg: self._parse_feed(cfg["url"], cfg["name"], cutoff)
                )
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"{feed_cfg['name']}: {exc}")
                print(f"[rss] skipping {feed_cfg['name']}: {exc}")

        duration_ms = int((time.time() - start) * 1000)
        status = "failed" if len(errors) == len(config.RSS_FEEDS) else "success"

        self._db.log_run(
            agent_name=self.AGENT_NAME,
            status=status,
            input_summary=f"feeds={len(config.RSS_FEEDS)}",
            output_summary=f"posts={len(posts)} errors={len(errors)}",
            duration_ms=duration_ms,
            error="; ".join(errors) if errors else None,
        )
        return posts

    def _parse_feed(
        self, url: str, source_name: str, cutoff: datetime
    ) -> list[ScrapedPost]:
        """Parse one RSS feed URL and filter by cutoff date."""
        feed = feedparser.parse(url)
        posts: list[ScrapedPost] = []

        for entry in feed.entries:
            published = self._parse_date(entry)
            if published and published < cutoff:
                continue

            title: str = entry.get("title", "")
            body: str = entry.get("summary", "")
            url_val: str = entry.get("link", "")
            created_utc = published.timestamp() if published else 0.0
            full_text = f"{title} {body}"
            entry_id = entry.get("id") or url_val or f"rss_{hash(title)}"

            posts.append(ScrapedPost(
                id=f"rss_{abs(hash(entry_id))}",
                title=title,
                body=body,
                comments=[],
                subreddit="",
                score=0,
                created_utc=created_utc,
                url=url_val,
                company_tags=_detect_company_tags(full_text),
                source_type="rss",
                source_name=source_name,
            ))

        return posts

    @staticmethod
    def _parse_date(entry) -> Optional[datetime]:
        """Extract published date from an RSS entry; return None if unparseable."""
        for attr in ("published_parsed", "updated_parsed"):
            val = getattr(entry, attr, None)
            if val:
                try:
                    return datetime(*val[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
        return None


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def scrape_all() -> dict[str, list[ScrapedPost]]:
    """Run all scrapers, deduplicate by URL, return results keyed by source.

    Never raises — failed scrapers return empty lists.
    Total counts are logged to Supabase.
    """
    db = SupabaseClient()
    start = time.time()

    results: dict[str, list[ScrapedPost]] = {
        "reddit": [],
        "hn": [],
        "rss": [],
    }

    for source, scraper_cls in [
        ("reddit", RedditScraper),
        ("hn", HNScraper),
        ("rss", RSScraper),
    ]:
        try:
            results[source] = scraper_cls().scrape()
        except Exception as exc:
            print(f"[scrape_all] {source} failed entirely: {exc}")

    # Deduplicate across sources by URL
    seen_urls: set[str] = set()
    for source in results:
        deduped: list[ScrapedPost] = []
        for post in results[source]:
            if post["url"] and post["url"] not in seen_urls:
                seen_urls.add(post["url"])
                deduped.append(post)
        results[source] = deduped

    total = sum(len(v) for v in results.values())
    duration_ms = int((time.time() - start) * 1000)

    db.log_run(
        agent_name="scraper_all",
        status="success",
        input_summary="sources=reddit,hn,rss",
        output_summary=(
            f"total={total} "
            f"reddit={len(results['reddit'])} "
            f"hn={len(results['hn'])} "
            f"rss={len(results['rss'])}"
        ),
        duration_ms=duration_ms,
    )
    return results


# ---------------------------------------------------------------------------
# Manual smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Running scrape_all() smoke test...\n")
    data = scrape_all()
    for source, posts in data.items():
        print(f"[{source}] {len(posts)} posts")
        for p in posts[:5]:
            print(f"  - [{p['score']:>5}] {p['title'][:80]}")
            print(f"           url={p['url'][:70]}")
            print(f"           tags={p['company_tags']}  comments={len(p['comments'])}")
        print()
