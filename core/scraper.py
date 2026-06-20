"""Bluesky, ProductHunt, App Store, HN, RSS, and Guardian feed ingestion.

All scrapers return List[ScrapedPost] and log each run to Supabase via
core/db.py. Never raises — failed sources are skipped and logged.
"""

import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup
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
    source_type: str   # "bluesky" | "producthunt" | "app_store" | "hn" | "rss" | "reddit_gold" | "guardian"
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
    """Fetches recent posts from Bluesky; authenticates with app-password to bypass IP 403."""

    AGENT_NAME = "scraper_bluesky"
    AUTH_URL = "https://bsky.social/xrpc/com.atproto.server.createSession"
    AUTH_SEARCH_URL = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"
    PUBLIC_SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
    LIMIT = 100
    MIN_BODY_LEN = 30

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "InsightPulse/1.0"})
        self._search_url = self.PUBLIC_SEARCH_URL
        self._authenticate()

    def _authenticate(self) -> None:
        """Obtain an access token via app-password and inject it into session headers."""
        if not (config.BLUESKY_HANDLE and config.BLUESKY_APP_PASSWORD):
            print("[bluesky] no credentials set, using public (unauthenticated) endpoint")
            return
        try:
            resp = requests.post(
                self.AUTH_URL,
                json={"identifier": config.BLUESKY_HANDLE, "password": config.BLUESKY_APP_PASSWORD},
                timeout=10,
            )
            resp.raise_for_status()
            token = resp.json().get("accessJwt")
            if token:
                self._session.headers["Authorization"] = f"Bearer {token}"
                self._search_url = self.AUTH_SEARCH_URL
                print(f"[bluesky] authenticated as {config.BLUESKY_HANDLE}")
        except Exception as exc:
            print(f"[bluesky] auth failed, continuing unauthenticated: {exc}")

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
            self._search_url,
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
    FULL_CONTENT_SOURCES = {"TheVerge_Reviews", "Engadget", "CNET", "ArsTechnica"}

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "InsightPulse/1.0"})

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
            url_val: str = entry.get("link", "")
            body: str = entry.get("summary", "")

            if source_name in self.FULL_CONTENT_SOURCES and url_val:
                full = self._fetch_full_content(url_val)
                if full:
                    body = full
                time.sleep(1)

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

    def _fetch_full_content(self, url: str) -> Optional[str]:
        """Fetch full article text via HTTP; return None on failure or if too short."""
        try:
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for selector in [
                "article",
                ".article-body",
                ".entry-content",
                ".post-content",
                "[itemprop='articleBody']",
            ]:
                content = soup.select_one(selector)
                if content and len(content.get_text()) > 500:
                    return content.get_text(separator=" ", strip=True)
        except Exception:
            pass
        return None

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
# ArcticShiftScraper
# ---------------------------------------------------------------------------

class ArcticShiftScraper:
    """Fetches Reddit posts from ArcticShift archive API -- no credentials needed."""

    AGENT_NAME = "scraper_arctic_shift"
    BASE_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
    LIMIT = 100
    MIN_SCORE = 3
    MIN_BODY_LEN = 30
    SKIP_SUBREDDITS = {"test", "pics", "funny", "gaming", "news", "worldnews"}

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "InsightPulse/1.0 (research)"})

    def scrape(self) -> list[ScrapedPost]:
        """Search ArcticShift archive for each COMPANY_TAG; return posts from last DAYS_LOOKBACK days."""
        start = time.time()
        posts: list[ScrapedPost] = []
        errors: list[str] = []
        seven_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=config.DAYS_LOOKBACK)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        for company in config.COMPANY_TAGS:
            try:
                fetched = self._search_company(company, seven_days_ago)
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"{company}: {exc}")
                print(f"[arctic_shift] skipping {company}: {exc}")
            time.sleep(2)

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

    def _search_company(self, company: str, after: str) -> list[ScrapedPost]:
        """Fetch up to LIMIT posts for one company keyword."""
        resp = self._session.get(
            self.BASE_URL,
            params={"q": company, "limit": self.LIMIT, "after": after},
            timeout=20,
        )
        resp.raise_for_status()

        posts: list[ScrapedPost] = []
        for post in resp.json().get("data", []):
            subreddit = post.get("subreddit", "")
            if subreddit.lower() in self.SKIP_SUBREDDITS:
                continue
            score = post.get("score", 0)
            if score < self.MIN_SCORE:
                continue
            body: str = post.get("selftext", "") or ""
            if len(body) < self.MIN_BODY_LEN:
                continue
            title: str = post.get("title", "")
            permalink = post.get("permalink", "")
            url = f"https://reddit.com{permalink}"
            created_utc = float(post.get("created_utc", 0))

            posts.append(ScrapedPost(
                id=f"arctic_shift_{abs(hash(permalink or title))}",
                title=title,
                body=body,
                comments=[],
                subreddit=subreddit,
                score=score,
                created_utc=created_utc,
                url=url,
                company_tags=_detect_company_tags(f"{title} {body}"),
                source_type="arctic_shift",
                source_name=f"r/{subreddit}",
            ))
        return posts


# ---------------------------------------------------------------------------
# HNAlgoliaScraper
# ---------------------------------------------------------------------------

class HNAlgoliaScraper:
    """Fetches HN stories by date from HN Algolia search API -- no credentials needed."""

    AGENT_NAME = "scraper_hn_algolia"
    BASE_URL = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()

    def scrape(self) -> list[ScrapedPost]:
        """Search HN Algolia for each COMPANY_TAG; return stories from last DAYS_LOOKBACK days."""
        start = time.time()
        posts: list[ScrapedPost] = []
        errors: list[str] = []
        after_unix = int(_cutoff_utc())

        for company in config.COMPANY_TAGS:
            try:
                fetched = self._search_company(company, after_unix)
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"{company}: {exc}")
                print(f"[hn_algolia] skipping {company}: {exc}")
            time.sleep(0.5)

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

    def _search_company(self, company: str, after_unix: int) -> list[ScrapedPost]:
        """Fetch stories mentioning company from HN Algolia."""
        resp = self._session.get(
            self.BASE_URL,
            params={
                "query": company,
                "tags": "story",
                "numericFilters": f"created_at_i>{after_unix}",
            },
            timeout=15,
        )
        resp.raise_for_status()

        posts: list[ScrapedPost] = []
        for hit in resp.json().get("hits", []):
            story_id = hit.get("objectID", "")
            title: str = hit.get("title") or ""
            body: str = hit.get("story_text") or ""
            url: str = hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            score = hit.get("points") or 0
            created_utc = float(hit.get("created_at_i") or 0)

            posts.append(ScrapedPost(
                id=f"hn_algolia_{story_id}",
                title=title,
                body=body,
                comments=[],
                subreddit="",
                score=score,
                created_utc=created_utc,
                url=url,
                company_tags=_detect_company_tags(f"{title} {body}"),
                source_type="hn_algolia",
                source_name="hn_algolia",
            ))
        return posts


# ---------------------------------------------------------------------------
# GoldMiningScraper
# ---------------------------------------------------------------------------

class GoldMiningScraper:
    """Searches Google for Reddit pain-point threads via Serper.dev, then fetches
    comments via Arctic Shift archive API (bypasses Reddit's IP-level block). Requires SERPER_API_KEY."""

    AGENT_NAME = "scraper_gold_mining"
    SERPER_URL = "https://google.serper.dev/search"
    ARCTIC_SHIFT_URL = "https://arctic-shift.photon-reddit.com/api/comments/search"
    ARCTIC_THROTTLE_S = 1
    MIN_BODY_LEN = 30
    MAX_COMMENTS = 20

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()
        # Reddit blocks the generic InsightPulse UA — use a browser UA for .json fetches
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        })

    def scrape(self) -> list[ScrapedPost]:
        """Mine all companies defined in config.GOLD_MINING_QUERIES."""
        if not config.SERPER_API_KEY:
            print("[gold_mining] skipping: SERPER_API_KEY not configured")
            return []

        start = time.time()
        posts: list[ScrapedPost] = []
        errors: list[str] = []

        for company in config.GOLD_MINING_QUERIES:
            try:
                fetched = self.mine(company)
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"{company}: {exc}")
                print(f"[gold_mining] skipping {company}: {exc}")

        duration_ms = int((time.time() - start) * 1000)
        all_failed = bool(errors) and len(errors) == len(config.GOLD_MINING_QUERIES)
        self._db.log_run(
            agent_name=self.AGENT_NAME,
            status="failed" if all_failed else "success",
            input_summary=f"companies={len(config.GOLD_MINING_QUERIES)}",
            output_summary=f"posts={len(posts)} errors={len(errors)}",
            duration_ms=duration_ms,
            error="; ".join(errors) if errors else None,
        )
        return posts

    def mine(self, company: str, query_overrides: Optional[list[str]] = None) -> list[ScrapedPost]:
        """Mine one company. Called by scrape() and directly by the CLI."""
        queries = query_overrides or config.GOLD_MINING_QUERIES.get(company, [])
        results = self._search(company, queries)
        posts: list[ScrapedPost] = []
        for result in results:
            post = self._fetch_thread(result, company)
            if post:
                posts.append(post)
            time.sleep(self.ARCTIC_THROTTLE_S)
        return posts

    def _search(self, company: str, queries: list[str]) -> list[dict]:
        """POST each query to Serper, extract Reddit thread results, return deduped list."""
        seen: dict[str, dict] = {}
        headers = {
            "X-API-KEY": config.SERPER_API_KEY,
            "Content-Type": "application/json",
        }
        for q in queries:
            try:
                resp = self._session.post(
                    self.SERPER_URL,
                    headers=headers,
                    json={"q": f"site:reddit.com {q}", "num": config.GOLD_MINING_MAX_RESULTS},
                    timeout=15,
                )
                resp.raise_for_status()
                for result in resp.json().get("organic", []):
                    link: str = result.get("link", "")
                    if "reddit.com/r/" in link and "/comments/" in link and link not in seen:
                        seen[link] = {
                            "link": link,
                            "title": result.get("title", ""),
                            "snippet": result.get("snippet", ""),
                        }
                time.sleep(1)
            except Exception as exc:
                print(f"[gold_mining] serper query failed ({q!r}): {exc}")
        return list(seen.values())

    def _fetch_thread(self, result: dict, company: str) -> Optional[ScrapedPost]:
        """Fetch comments for a Reddit thread via Arctic Shift archive API."""
        url: str = result["link"]
        title: str = result.get("title", "")
        snippet: str = result.get("snippet", "")

        m = re.search(r"/comments/([a-z0-9]+)", url)
        if not m:
            return None
        post_id = m.group(1)

        subreddit_m = re.search(r"/r/([^/]+)/", url)
        subreddit = subreddit_m.group(1) if subreddit_m else ""

        try:
            resp = self._session.get(
                self.ARCTIC_SHIFT_URL,
                params={"link_id": f"t3_{post_id}", "limit": self.MAX_COMMENTS},
                timeout=15,
            )
            resp.raise_for_status()
            comment_data = resp.json().get("data") or []

            comments: list[str] = [
                c["body"]
                for c in comment_data
                if c.get("body") not in ("[removed]", "[deleted]", "")
                and len(c.get("body", "")) >= self.MIN_BODY_LEN
            ][:self.MAX_COMMENTS]

            body = snippet if len(snippet) >= self.MIN_BODY_LEN else title
            full_text = f"{title} {body} {' '.join(comments)}"

            return ScrapedPost(
                id=f"reddit_gold_{post_id}",
                title=title,
                body=body,
                comments=comments,
                subreddit=f"r/{subreddit}",
                score=0,
                created_utc=0.0,
                url=url,
                company_tags=_detect_company_tags(full_text) or [company],
                source_type="reddit_gold",
                source_name=f"r/{subreddit}",
            )
        except Exception as exc:
            print(f"[gold_mining] failed to fetch {url}: {exc}")
            return None


# ---------------------------------------------------------------------------
# GuardianScraper
# ---------------------------------------------------------------------------

class GuardianScraper:
    """Fetches full-text journalism from The Guardian Open Platform — requires GUARDIAN_API_KEY."""

    AGENT_NAME = "scraper_guardian"
    BASE_URL = "https://content.guardianapis.com/search"
    PAGE_SIZE = 10
    MIN_BODY_LEN = 100

    def __init__(self) -> None:
        self._db = SupabaseClient()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "InsightPulse/1.0"})

    def scrape(self) -> list[ScrapedPost]:
        """Search The Guardian for each GUARDIAN_QUERIES company; return full-text articles."""
        if not config.GUARDIAN_API_KEY:
            print("[guardian] skipping guardian: GUARDIAN_API_KEY not configured")
            return []

        start = time.time()
        posts: list[ScrapedPost] = []
        errors: list[str] = []

        for company in config.GUARDIAN_QUERIES:
            try:
                fetched = self._search_company(company)
                posts.extend(fetched)
            except Exception as exc:
                errors.append(f"{company}: {exc}")
                print(f"[guardian] skipping {company}: {exc}")
            time.sleep(0.5)

        duration_ms = int((time.time() - start) * 1000)
        all_failed = bool(errors) and len(errors) == len(config.GUARDIAN_QUERIES)
        self._db.log_run(
            agent_name=self.AGENT_NAME,
            status="failed" if all_failed else "success",
            input_summary=f"companies={len(config.GUARDIAN_QUERIES)}",
            output_summary=f"posts={len(posts)} errors={len(errors)}",
            duration_ms=duration_ms,
            error="; ".join(errors) if errors else None,
        )
        return posts

    def _search_company(self, company: str) -> list[ScrapedPost]:
        """Fetch up to PAGE_SIZE Guardian articles for one company query."""
        resp = self._session.get(
            self.BASE_URL,
            params={
                "q": company,
                "api-key": config.GUARDIAN_API_KEY,
                "show-fields": "bodyText",
                "order-by": "newest",
                "page-size": self.PAGE_SIZE,
            },
            timeout=15,
        )
        resp.raise_for_status()

        posts: list[ScrapedPost] = []
        for result in resp.json().get("response", {}).get("results", []):
            body: str = result.get("fields", {}).get("bodyText", "") or ""
            if len(body) < self.MIN_BODY_LEN:
                continue

            title: str = result.get("webTitle", "")
            url: str = result.get("webUrl", "")
            published = result.get("webPublicationDate", "")
            try:
                created_utc = _parse_iso(published)
            except Exception:
                created_utc = 0.0

            posts.append(ScrapedPost(
                id=f"guardian_{abs(hash(url or title))}",
                title=title,
                body=body,
                comments=[],
                subreddit="",
                score=0,
                created_utc=created_utc,
                url=url,
                company_tags=_detect_company_tags(f"{title} {body}"),
                source_type="guardian",
                source_name="The Guardian",
            ))
        return posts


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
        "hn_algolia": [],
        "rss": [],
        "app_store": [],
        "producthunt": [],
        "arctic_shift": [],
        "gold_mining": [],
        "guardian": [],
    }

    for source, scraper_cls in [
        ("bluesky", BlueSkyScraper),
        ("hn", HNScraper),
        ("hn_algolia", HNAlgoliaScraper),
        ("rss", RSScraper),
        ("app_store", AppStoreScraper),
        ("producthunt", ProductHuntScraper),
        ("arctic_shift", ArcticShiftScraper),
        ("gold_mining", GoldMiningScraper),
        ("guardian", GuardianScraper),
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
        input_summary="sources=bluesky,hn,hn_algolia,rss,app_store,producthunt,arctic_shift,gold_mining,guardian",
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
