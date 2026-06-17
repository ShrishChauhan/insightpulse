"""Central config — all environment variables loaded here.
Every module imports from config, never from os.environ directly."""

import os
from dotenv import load_dotenv

load_dotenv()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# LLM provider selection — set PROVIDER=anthropic in .env to use Claude Haiku in prod
PROVIDER = os.getenv("PROVIDER", "gemini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.0-flash"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# YouTube Data API v3 (free key from Google Cloud Console)
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_DAILY_QUOTA_LIMIT = 8000  # stay under free tier daily cap of 10,000 units

# ProductHunt (Developer Token from producthunt.com/v2/oauth/applications)
PRODUCTHUNT_TOKEN = os.getenv("PRODUCTHUNT_TOKEN")

# Alerts
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

# Buffer (LinkedIn posting via Buffer API v1 — kept for reference)
BUFFER_ACCESS_TOKEN = os.getenv("BUFFER_ACCESS_TOKEN")
BUFFER_LINKEDIN_PROFILE_ID = os.getenv("BUFFER_LINKEDIN_PROFILE_ID")

# LinkedIn Consumer API (direct posting via ugcPosts)
LINKEDIN_ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_PERSON_URN = os.getenv("LINKEDIN_PERSON_URN")
LINKEDIN_ORGANIZATION_URN = os.getenv("LINKEDIN_ORGANIZATION_URN")
LINKEDIN_TOKEN_CREATED = os.getenv("LINKEDIN_TOKEN_CREATED", "2026-06-13")

# Serper.dev (Google SERP API — free 2500 lifetime queries, no CC required)
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

# Startup validation — fail loudly at import time, not deep in the call stack
_REQUIRED: dict[str, str | None] = {
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_KEY": SUPABASE_KEY,
}
if PROVIDER == "gemini":
    _REQUIRED["GEMINI_API_KEY"] = GEMINI_API_KEY
elif PROVIDER == "groq":
    _REQUIRED["GROQ_API_KEY"] = GROQ_API_KEY
elif PROVIDER == "anthropic":
    _REQUIRED["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY

for _name, _value in _REQUIRED.items():
    if not _value:
        raise ValueError(
            f"Missing required environment variable: {_name}. "
            f"Add it to your .env file and restart."
        )

# App config (no secrets — hardcoded is fine)
COMPANY_TAGS = [
    "apple", "google", "spotify", "notion",
    "microsoft", "netflix", "amazon", "meta"
]

GOLD_MINING_MAX_RESULTS = 10  # Serper results per query

GOLD_MINING_QUERIES: dict[str, list[str]] = {
    "spotify": [
        '"Spotify" "I hate" OR "broken" OR "should fix"',
        '"Spotify" "why does" OR "anyone else" OR "frustrating"',
        '"Spotify" "feature request" OR "wish they would"',
    ],
    "apple": [
        '"Apple" "I hate" OR "broken" OR "should fix"',
        '"Apple" "why does" OR "anyone else" OR "frustrating"',
        '"Apple" "feature request" OR "wish they would"',
    ],
    "google": [
        '"Google" "I hate" OR "broken" OR "should fix"',
        '"Google" "why does" OR "anyone else" OR "frustrating"',
        '"Google" "feature request" OR "wish they would"',
    ],
    "notion": [
        '"Notion" "I hate" OR "broken" OR "should fix"',
        '"Notion" "why does" OR "anyone else" OR "frustrating"',
        '"Notion" "feature request" OR "wish they would"',
    ],
    "microsoft": [
        '"Microsoft" "I hate" OR "broken" OR "should fix"',
        '"Microsoft" "why does" OR "anyone else" OR "frustrating"',
        '"Microsoft" "feature request" OR "wish they would"',
    ],
    "netflix": [
        '"Netflix" "I hate" OR "broken" OR "should fix"',
        '"Netflix" "why does" OR "anyone else" OR "frustrating"',
        '"Netflix" "feature request" OR "wish they would"',
    ],
    "amazon": [
        '"Amazon" "I hate" OR "broken" OR "should fix"',
        '"Amazon" "why does" OR "anyone else" OR "frustrating"',
        '"Amazon" "feature request" OR "wish they would"',
    ],
    "meta": [
        '"Meta" OR "Facebook" "I hate" OR "broken" OR "should fix"',
        '"Meta" OR "Facebook" "why does" OR "anyone else" OR "frustrating"',
        '"Meta" OR "Facebook" "feature request" OR "wish they would"',
    ],
}

CRITIC_THRESHOLDS = {
    "auto_post": 18,
    "soft_approval": 12
}

# App Store app IDs — Apple public RSS feeds, no credentials needed
APP_STORE_IDS = {
    "spotify": "324684580",
    "notion": "1232780281",
    "microsoft": "586003813",   # Microsoft 365
    "google": "544007664",      # Google app
    "amazon": "297606951",
    "netflix": "363590051",
}

# LLM — kept for backwards compat; prefer GEMINI_MODEL / ANTHROPIC_MODEL via LLMClient
CLAUDE_MODEL = ANTHROPIC_MODEL

# Embeddings — all-MiniLM-L6-v2 has a 256-token max; 200 tokens stays safely under
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384
CHUNK_SIZE = 200     # tokens (cl100k_base) — hard ceiling from model's 256-token limit
CHUNK_OVERLAP = 30   # tokens
EMBED_BATCH_SIZE = 50
MAX_RETRIEVAL_CHUNKS = 15
DAYS_LOOKBACK = 7
TOPIC_REPEAT_DAYS = 30

SENTIMENT_WORDS = {
    "positive": [
        "amazing", "awesome", "brilliant", "celebrate", "champion",
        "clean", "creative", "delight", "elegant", "excellent",
        "excited", "fantastic", "fast", "fixed", "flexible",
        "friendly", "gain", "great", "grow", "helpful",
        "improve", "innovative", "intuitive", "launch", "love",
        "optimized", "outstanding", "perfect", "polished", "powerful",
        "productive", "profitable", "proud", "reliable", "seamless",
        "smart", "smooth", "solid", "streamlined", "strong",
        "success", "superb", "superior", "thrilled", "transparent",
        "trust", "useful", "valuable", "wins", "wonderful",
    ],
    "negative": [
        "awful", "broken", "bug", "buggy", "churn",
        "clunky", "confusing", "crash", "delay", "deprecated",
        "disappoint", "disaster", "dropped", "error", "fail",
        "failure", "flawed", "freeze", "frustrate", "glitch",
        "hate", "horrible", "ignored", "inconsistent", "laggy",
        "leak", "limited", "loss", "messy", "misleading",
        "missing", "outage", "overpriced", "painful", "poor",
        "problem", "regression", "removed", "slow", "spam",
        "terrible", "ugly", "unavailable", "unintuitive", "unreliable",
        "unusable", "useless", "waste", "worst", "wrong",
    ],
}

RSS_FEEDS = [
    {"name": "TechCrunch",        "url": "https://techcrunch.com/feed/"},
    {"name": "TheVerge",          "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "HackerNews",        "url": "https://hnrss.org/frontpage"},
    {"name": "ArsTechnica",       "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "ArsTechnicaLab",    "url": "https://feeds.arstechnica.com/arstechnica/technology-lab"},
    {"name": "Wired",             "url": "https://www.wired.com/feed/rss"},
    {"name": "VentureBeat",       "url": "https://venturebeat.com/feed/"},
    {"name": "AndroidAuthority",  "url": "https://www.androidauthority.com/feed/"},
    {"name": "9to5Mac",           "url": "https://9to5mac.com/feed/"},
    {"name": "AppleInsider",      "url": "https://appleinsider.com/rss/news/"},
    {"name": "MacRumors",         "url": "https://www.macrumors.com/macrumors.xml"},
    # Google News RSS — per-company topic feeds, no auth needed
    {"name": "GoogleNews_Apple",     "url": "https://news.google.com/rss/search?q=apple+product+features&hl=en-US&gl=US&ceid=US:en"},
    {"name": "GoogleNews_Google",    "url": "https://news.google.com/rss/search?q=google+product+launch&hl=en-US&gl=US&ceid=US:en"},
    {"name": "GoogleNews_Spotify",   "url": "https://news.google.com/rss/search?q=spotify+features+users&hl=en-US&gl=US&ceid=US:en"},
    {"name": "GoogleNews_Microsoft", "url": "https://news.google.com/rss/search?q=microsoft+product+update&hl=en-US&gl=US&ceid=US:en"},
    {"name": "GoogleNews_Meta",      "url": "https://news.google.com/rss/search?q=meta+facebook+product&hl=en-US&gl=US&ceid=US:en"},
    {"name": "GoogleNews_Netflix",   "url": "https://news.google.com/rss/search?q=netflix+features+subscribers&hl=en-US&gl=US&ceid=US:en"},
    {"name": "GoogleNews_Amazon",    "url": "https://news.google.com/rss/search?q=amazon+product+users&hl=en-US&gl=US&ceid=US:en"},
    {"name": "GoogleNews_Notion",    "url": "https://news.google.com/rss/search?q=notion+app+features&hl=en-US&gl=US&ceid=US:en"},
    {"name": "TheVerge_Reviews",     "url": "https://www.theverge.com/rss/reviews/index.xml"},
    {"name": "Engadget",             "url": "https://www.engadget.com/rss.xml"},
    {"name": "CNET",                 "url": "https://www.cnet.com/rss/news/"},
    {"name": "9to5Google",           "url": "https://9to5google.com/feed/"},
    {"name": "GSMArena",             "url": "https://www.gsmarena.com/rss-news-reviews.php3"},
    {"name": "AndroidPolice",        "url": "https://www.androidpolice.com/feed/"},
]