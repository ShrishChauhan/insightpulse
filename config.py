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

# Reddit
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "InsightPulse/1.0")

# Alerts
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

# Buffer (LinkedIn posting via Buffer API v1 — kept for reference)
BUFFER_ACCESS_TOKEN = os.getenv("BUFFER_ACCESS_TOKEN")
BUFFER_LINKEDIN_PROFILE_ID = os.getenv("BUFFER_LINKEDIN_PROFILE_ID")

# LinkedIn Consumer API (direct posting via ugcPosts)
LINKEDIN_ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_PERSON_URN = os.getenv("LINKEDIN_PERSON_URN")
LINKEDIN_TOKEN_CREATED = os.getenv("LINKEDIN_TOKEN_CREATED", "2026-06-13")

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
TARGET_SUBREDDITS = [
    "apple", "google", "spotify", "notion",
    "microsoft", "netflix", "amazon",
    "productivity", "startups"
]

COMPANY_TAGS = [
    "apple", "google", "spotify", "notion",
    "microsoft", "netflix", "amazon", "meta"
]

CRITIC_THRESHOLDS = {
    "auto_post": 18,
    "soft_approval": 12
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
    {"name": "TechCrunch",   "url": "https://techcrunch.com/feed/"},
    {"name": "TheVerge",     "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "HackerNews",   "url": "https://hnrss.org/frontpage"},
    {"name": "ArsTechnica",  "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "Wired",        "url": "https://www.wired.com/feed/rss"},
]