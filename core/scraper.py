"""Bluesky, YouTube, ProductHunt, App Store, HN, and RSS feed ingestion.

All scrapers return List[ScrapedPost] and log each run to Supabase via
core/db.py. Never raises — failed sources are skipped and logged.
"""

import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import feedparser
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
    source_type: str   # "bluesky" | "youtube" | "producthunt" | "app_store" | "hn" | "rss"
    source_name: str   # human-readable source label


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


def _parse_iso(ts: str) -> float:
    """Parse ISO 8601 string (with Z or +00:00) to unix timestamp."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------------------
# BlueSkyScraper
# ---------------------------------------------------------------------------

class BlueSkyScraper:
    """Fetches recent posts from Bluesky public API — no credentials needed."""

    AGENT_NAME = "scraper_bluesky"
    BASE_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
    LIMIT = 100
    MIN_BODY_LEN = 30

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "InsightPulse/1.0"})

    def scrape(self) -> list[ScrapedPost]:
        """Search Bluesky for each COMPANY_TAG; return posts from last DAYS_LOOKBACK days."""
        start = time.time()
        posts: list[ScrapedPost] = []
        cutoff = _cutoff_utc()
        errors: list[str] = []

        for company in config.COMPANY_TAGS:
            try:
                fetched = self._search_company(company, cutoff)
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"{company}: {exc}")
                print(f"[bluesky] skipping {company}: {exc}")
            time.sleep(1)

        duration_ms = int((time.time() - start) * 1000)
        all_failed = len(errors) == len(config.COMPANY_TAGS)
        self._db.log_run(
            agent_name=self.AGENT_NAME,
            status="failed" if all_failed else "success",
            input_summary=f"companies={len(config.COMPANY_TAGS)}",
            output_summary=f"posts={len(posts)} errors={len(errors)}",
            duration_ms=duration_ms,
            error="; ".join(errors) if errors else None,
        )
        return posts

    def _search_company(self, company: str, cutoff: float) -> list[ScrapedPost]:
        """Fetch up to LIMIT posts for one company keyword."""
        resp = self._session.get(
            self.BASE_URL,
            params={"q": company, "limit": self.LIMIT, "sort": "latest"},
            timeout=15,
        )
        resp.raise_for_status()
        raw_posts = resp.json().get("posts", [])

        posts: list[ScrapedPost] = []
        for post in raw_posts:
            record = post.get("record", {})
            body: str = record.get("text", "")
            if len(body) < self.MIN_BODY_LEN:
                continue

            try:
                created_utc = _parse_iso(record.get("createdAt", ""))
            except Exception:
                continue
            if created_utc < cutoff:
                continue

            handle = post.get("author", {}).get("handle", "unknown")
            uri = post.get("uri", "")
            rkey = uri.split("/")[-1] if uri else ""
            url = f"https://bsky.app/profile/{handle}/post/{rkey}"
            post_id = uri.replace(":", "_").replace("/", "_")

            posts.append(ScrapedPost(
                id=f"bluesky_{post_id}",
                title=body[:120].split("\n")[0],
                body=body,
                comments=[],
                subreddit="bluesky",
                score=(post.get("likeCount") or 0) + (post.get("repostCount") or 0),
                created_utc=created_utc,
                url=url,
                company_tags=_detect_company_tags(body),
                source_type="bluesky",
                source_name="bluesky",
            ))
        return posts


# ---------------------------------------------------------------------------
# YouTubeScraper
# ---------------------------------------------------------------------------

class YouTubeScraper:
    """Fetches video comments from YouTube Data API v3 — requires YOUTUBE_API_KEY."""

    AGENT_NAME = "scraper_youtube"
    SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
    COMMENTS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"
    MAX_VIDEOS = 5
    MAX_COMMENTS = 20
    MIN_BODY_LEN = 50
    SEARCH_COST = 100  # quota units per search call
    COMMENT_COST = 1   # quota units per commentThreads call

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()
        self._quota_used = 0

    def scrape(self) -> list[ScrapedPost]:
        """Search YouTube for each COMPANY_TAG; return comment posts."""
        if not config.YOUTUBE_API_KEY:
            print("[youtube] skipping youtube: credentials not configured")
            return []

        start = time.time()
        posts: list[ScrapedPost] = []
        errors: list[str] = []
        published_after = (
            datetime.now(timezone.utc) - timedelta(days=config.DAYS_LOOKBACK)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        for company in config.COMPANY_TAGS:
            if self._quota_used >= config.YOUTUBE_DAILY_QUOTA_LIMIT:
                print(f"[youtube] daily quota limit reached ({self._quota_used} units used)")
                break
            try:
                fetched = self._scrape_company(company, published_after)
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"{company}: {exc}")
                print(f"[youtube] skipping {company}: {exc}")
            time.sleep(0.5)

        duration_ms = int((time.time() - start) * 1000)
        status = "failed" if errors and not posts else "success"
        self._db.log_run(
            agent_name=self.AGENT_NAME,
            status=status,
            input_summary=f"companies={len(config.COMPANY_TAGS)} quota_used={self._quota_used}",
            output_summary=f"posts={len(posts)} errors={len(errors)}",
            duration_ms=duration_ms,
            error="; ".join(errors) if errors else None,
        )
        return posts

    def _scrape_company(self, company: str, published_after: str) -> list[ScrapedPost]:
        """Search videos for one company, then fetch top comments for each video."""
        video_ids = self._search_videos(company, published_after)
        posts: list[ScrapedPost] = []
        for video_id, video_title in video_ids:
            if self._quota_used >= config.YOUTUBE_DAILY_QUOTA_LIMIT:
                break
            for comment in self._fetch_comments(video_id):
                if len(comment["body"]) < self.MIN_BODY_LEN:
                    continue
                full_text = f"{comment['body']} {video_title}"
                posts.append(ScrapedPost(
                    id=f"youtube_{video_id}_{comment['id']}",
                    title=f"YouTube comment on: {video_title[:80]}",
                    body=comment["body"],
                    comments=[],
                    subreddit="youtube",
                    score=comment["like_count"],
                    created_utc=comment["created_utc"],
                    url=f"https://youtube.com/watch?v={video_id}",
                    company_tags=_detect_company_tags(full_text),
                    source_type="youtube",
                    source_name="youtube",
                ))
        return posts

    def _search_videos(self, company: str, published_after: str) -> list[tuple[str, str]]:
        """Return list of (video_id, title) for a company query."""
        resp = self._session.get(
            self.SEARCH_URL,
            params={
                "q": f"{company} review OR product OR features 2026",
                "type": "video",
                "order": "relevance",
                "publishedAfter": published_after,
                "maxResults": self.MAX_VIDEOS,
                "key": config.YOUTUBE_API_KEY,
                "part": "snippet",
            },
            timeout=15,
        )
        resp.raise_for_status()
        self._quota_used += self.SEARCH_COST
        return [
            (item["id"]["videoId"], item["snippet"]["title"])
            for item in resp.json().get("items", [])
            if item.get("id", {}).get("videoId")
        ]

    def _fetch_comments(self, video_id: str) -> list[dict]:
        """Return top comment dicts for a video."""
        try:
            resp = self._session.get(
                self.COMMENTS_URL,
                params={
                    "videoId": video_id,
                    "maxResults": self.MAX_COMMENTS,
                    "order": "relevance",
                    "key": config.YOUTUBE_API_KEY,
                    "part": "snippet",
                },
                timeout=15,
            )
            resp.raise_for_status()
            self._quota_used += self.COMMENT_COST
            results = []
            for item in resp.json().get("items", []):
                snippet = item["snippet"]["topLevelComment"]["snippet"]
                try:
                    created_utc = _parse_iso(snippet.get("publishedAt", ""))
                except Exception:
                    created_utc = 0.0
                results.append({
                    "id": item["id"],
                    "body": snippet.get("textDisplay", ""),
                    "like_count": snippet.get("likeCount", 0),
                    "created_utc": created_utc,
                })
            return results
        except Exception as exc:
            print(f"[youtube] comments failed for {video_id}: {exc}")
            return []


# ---------------------------------------------------------------------------
# ProductHuntScraper
# ---------------------------------------------------------------------------

class ProductHuntScraper:
    """Fetches recent launches from ProductHunt GraphQL API — requires PRODUCTHUNT_TOKEN."""

    AGENT_NAME = "scraper_producthunt"
    GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"

    _QUERY = """
    query($topic: String!, $after: DateTime!) {
      posts(first: 20, topic: $topic, postedAfter: $after) {
        edges {
          node {
            name tagline description votesCount commentsCount url createdAt
            comments(first: 10) {
              edges { node { body createdAt } }
            }
          }
        }
      }
    }
    """

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()

    def scrape(self) -> list[ScrapedPost]:
        """Fetch ProductHunt launches for each COMPANY_TAG."""
        if not config.PRODUCTHUNT_TOKEN:
            print("[producthunt] skipping producthunt: credentials not configured")
            return []

        self._session.headers.update({
            "Authorization": f"Bearer {config.PRODUCTHUNT_TOKEN}",
            "Content-Type": "application/json",
        })

        start = time.time()
        posts: list[ScrapedPost] = []
        errors: list[str] = []
        after_iso = (
            datetime.now(timezone.utc) - timedelta(days=config.DAYS_LOOKBACK)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        for company in config.COMPANY_TAGS:
            try:
                fetched = self._fetch_posts(company, after_iso)
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"{company}: {exc}")
                print(f"[producthunt] skipping {company}: {exc}")
            time.sleep(0.5)

        duration_ms = int((time.time() - start) * 1000)
        status = "failed" if errors and not posts else "success"
        self._db.log_run(
            agent_name=self.AGENT_NAME,
            status=status,
            input_summary=f"companies={len(config.COMPANY_TAGS)}",
            output_summary=f"posts={len(posts)} errors={len(errors)}",
            duration_ms=duration_ms,
            error="; ".join(errors) if errors else None,
        )
        return posts

    def _fetch_posts(self, company: str, after_iso: str) -> list[ScrapedPost]:
        """Query ProductHunt GraphQL for one company/topic."""
        resp = self._session.post(
            self.GRAPHQL_URL,
            json={"query": self._QUERY, "variables": {"topic": company, "after": after_iso}},
            timeout=15,
        )
        resp.raise_for_status()
        edges = resp.json().get("data", {}).get("posts", {}).get("edges", [])

        posts: list[ScrapedPost] = []
        for edge in edges:
            node = edge.get("node", {})
            name = node.get("name", "")
            tagline = node.get("tagline", "")
            description = node.get("description") or ""
            comment_bodies = [
                c["node"]["body"]
                for c in node.get("comments", {}).get("edges", [])
                if c.get("node", {}).get("body")
            ]
            title = f"{name}: {tagline}"
            body = f"{description} {' '.join(comment_bodies)}".strip()

            try:
                created_utc = _parse_iso(node.get("createdAt", ""))
            except Exception:
                created_utc = 0.0

            posts.append(ScrapedPost(
                id=f"ph_{abs(hash(node.get('url', title)))}",
                title=title,
                body=body,
                comments=comment_bodies,
                subreddit="producthunt",
                score=node.get("votesCount", 0),
                created_utc=created_utc,
                url=node.get("url", ""),
                company_tags=_detect_company_tags(f"{title} {body}"),
                source_type="producthunt",
                source_name="producthunt",
            ))
        return posts


# ---------------------------------------------------------------------------
# AppStoreScraper
# ---------------------------------------------------------------------------

class AppStoreScraper:
    """Fetches App Store reviews from Apple public RSS feeds — no credentials needed."""

    AGENT_NAME = "scraper_app_store"
    RSS_BASE = "https://itunes.apple.com/us/rss/customerreviews/id={app_id}/sortBy=mostRecent/json"
    LOOKBACK_DAYS = 30
    MIN_BODY_LEN = 50

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "InsightPulse/1.0"})

    def scrape(self) -> list[ScrapedPost]:
        """Fetch reviews for all apps in APP_STORE_IDS."""
        start = time.time()
        posts: list[ScrapedPost] = []
        errors: list[str] = []
        cutoff = _cutoff_utc(days=self.LOOKBACK_DAYS)

        for company, app_id in config.APP_STORE_IDS.items():
            try:
                fetched = self._fetch_reviews(company, app_id, cutoff)
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"{company}: {exc}")
                print(f"[app_store] skipping {company}: {exc}")
            time.sleep(0.5)

        duration_ms = int((time.time() - start) * 1000)
        all_failed = len(errors) == len(config.APP_STORE_IDS)
        self._db.log_run(
            agent_name=self.AGENT_NAME,
            status="failed" if all_failed else "success",
            input_summary=f"apps={len(config.APP_STORE_IDS)}",
            output_summary=f"reviews={len(posts)} errors={len(errors)}",
            duration_ms=duration_ms,
            error="; ".join(errors) if errors else None,
        )
        return posts

    def _fetch_reviews(self, company: str, app_id: str, cutoff: float) -> list[ScrapedPost]:
        """Fetch and parse the iTunes RSS JSON feed for one app."""
        url = self.RSS_BASE.format(app_id=app_id)
        resp = self._session.get(url, timeout=15)
        resp.raise_for_status()

        entries = resp.json().get("feed", {}).get("entry", [])
        posts: list[ScrapedPost] = []

        for entry in entries:
            # Apple includes the app metadata as the first entry — skip entries with no rating
            if "im:rating" not in entry:
                continue
            body: str = entry.get("content", {}).get("label", "")
            if len(body) < self.MIN_BODY_LEN:
                continue

            updated = entry.get("updated", {}).get("label", "")
            try:
                created_utc = _parse_iso(updated)
            except Exception:
                created_utc = 0.0
            if created_utc and created_utc < cutoff:
                continue

            title: str = entry.get("title", {}).get("label", "")
            link: str = entry.get("link", {}).get("attributes", {}).get("href", "")
            rating = int(entry.get("im:rating", {}).get("label", "0") or "0")
            entry_id = entry.get("id", {}).get("label", link or title)

            posts.append(ScrapedPost(
                id=f"app_store_{abs(hash(entry_id))}",
                title=title,
                body=body,
                comments=[],
                subreddit="app_store",
                score=rating,
                created_utc=created_utc,
                url=link,
                company_tags=[company],
                source_type="app_store",
                source_name="app_store",
            ))
        return posts


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

    _RELEVANCE_KEYWORDS = {
        "apple", "google", "spotify", "notion", "microsoft", "netflix",
        "amazon", "meta", "openai", "anthropic", "product", "launch",
        "startup", "saas", "ai", "llm", "app", "software", "api",
        "platform", "model", "tool",
    }

    def __init__(self) -> None:
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
        resp = self._session.get(f"{self.BASE_URL}/topstories.json", timeout=self.TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _fetch_story(self, story_id: int, cutoff: float) -> Optional[ScrapedPost]:
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
        lower = title.lower()
        return any(kw in lower for kw in self._RELEVANCE_KEYWORDS)


# ---------------------------------------------------------------------------
# RSScraper
# ---------------------------------------------------------------------------

class RSScraper:
    """Parses RSS feeds from RSS_FEEDS config; normalises into ScrapedPost."""

    AGENT_NAME = "scraper_rss"

    def __init__(self) -> None:
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
                company_tags=_detect_company_tags(f"{title} {body}"),
                source_type="rss",
                source_name=source_name,
            ))

        return posts

    @staticmethod
    def _parse_date(entry) -> Optional[datetime]:
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
    Credential-gated scrapers log a skip message if key is absent.
    """
    db = SupabaseClient()
    start = time.time()

    results: dict[str, list[ScrapedPost]] = {
        "bluesky": [],
        "hn": [],
        "rss": [],
        "app_store": [],
        "youtube": [],
        "producthunt": [],
    }

    for source, scraper_cls in [
        ("bluesky", BlueSkyScraper),
        ("hn", HNScraper),
        ("rss", RSScraper),
        ("app_store", AppStoreScraper),
        ("youtube", YouTubeScraper),
        ("producthunt", ProductHuntScraper),
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
    source_summary = " ".join(f"{s}={len(results[s])}" for s in results)

    db.log_run(
        agent_name="scraper_all",
        status="success",
        input_summary="sources=bluesky,hn,rss,app_store,youtube,producthunt",
        output_summary=f"total={total} {source_summary}",
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
        for p in posts[:3]:
            print(f"  - [{p['score']:>5}] {p['title'][:80]}")
            print(f"           url={p['url'][:70]}")
            print(f"           tags={p['company_tags']}")
        print()
