# InsightPulse — Session Prompts Guide
# 
# HOW TO USE THIS FILE:
# These are TEMPLATES, not scripts. Fill in the [brackets] before pasting.
# Each prompt is designed to be self-contained — Claude Code gets full
# context in one message, not through follow-ups.
#
# RULE: One prompt per task. Batch everything into it upfront.
# If Claude gets it wrong → Edit & Regenerate, never follow up.

---

## UNIVERSAL OPENER (use at the start of every session)

```
Read CLAUDE.md and project-log.md. Confirm you've read both by listing:
(1) the last completed task from project-log.md
(2) the next task from project-log.md  
(3) any open questions you need answered before starting

Today's task: [FILL IN]
No code until you've confirmed understanding and I've answered questions.
```

---

## SESSION 1: Project Scaffold + Database Foundation

**Goal:** Create all project folders and files (empty), then build core/db.py using Supabase

**Before starting:** You need a Supabase project created at supabase.com.
Free account → New project → copy Project URL and anon key → paste into config.py

```
Read CLAUDE.md and project-log.md first.

Task: Create the InsightPulse project scaffold, then build core/db.py using Supabase.

PART 1 — Scaffold (do this first, confirm before part 2):
Create this exact folder and file structure. All files empty except
for a one-line docstring describing their purpose.

insightpulse/
├── agents/
│   ├── __init__.py
│   ├── orchestrator.py
│   ├── scout.py
│   ├── analyst.py
│   ├── writer.py
│   └── critic.py
├── core/
│   ├── __init__.py
│   ├── scraper.py
│   ├── embedder.py
│   ├── retriever.py
│   ├── llm_client.py
│   └── db.py
├── tools/
│   ├── __init__.py
│   ├── linkedin_poster.py
│   ├── notifier.py
│   └── pdf_generator.py
├── dashboard/
│   └── app.py
├── data/
│   └── .gitkeep
├── scripts/
│   ├── session-start.sh
│   └── handoff.sh
├── config.py
├── main.py
└── requirements.txt

PART 2 — config.py:
Create config.py with all environment variable reads. Never hardcode values.
Include:
- SUPABASE_URL, SUPABASE_KEY (from supabase.com project settings)
- ANTHROPIC_API_KEY
- REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
- SLACK_WEBHOOK_URL
- LINKEDIN_EMAIL, LINKEDIN_PASSWORD (for later)
- TARGET_SUBREDDITS list
- COMPANY_TAGS list (apple, google, spotify, notion, microsoft, netflix, amazon)
- CRITIC_THRESHOLDS dict: auto_post=18, soft_approval=12
Use python-dotenv. Create a .env.example file showing all required keys.
Add .env to .gitignore immediately.

PART 3 — core/db.py using Supabase:
Use the supabase-py library (pip install supabase -q).
Use the Supabase MCP to create the actual tables in our Supabase project
while building this file — run migrations directly.

Tables to create via Supabase MCP migration:

1. runs
   - id: uuid primary key default gen_random_uuid()
   - created_at: timestamptz default now()
   - agent_name: text
   - status: text (success | failed | skipped)
   - input_summary: text
   - output_summary: text
   - tokens_used: integer
   - duration_ms: integer
   - error: text nullable

2. topics
   - id: uuid primary key default gen_random_uuid()
   - topic: text
   - company: text
   - first_seen: timestamptz default now()
   - last_covered: timestamptz nullable
   - cover_count: integer default 0
   - avg_critic_score: float nullable

3. posts
   - id: uuid primary key default gen_random_uuid()
   - topic_id: uuid references topics(id)
   - linkedin_post: text
   - pm_brief_path: text nullable
   - critic_score: integer
   - decision: text (auto_post | soft_approval | auto_reject)
   - posted_at: timestamptz nullable
   - engagement_score: integer nullable

4. learning_log
   - id: uuid primary key default gen_random_uuid()
   - created_at: timestamptz default now()
   - lesson: text

5. embeddings (for pgvector RAG)
   - id: uuid primary key default gen_random_uuid()
   - content: text
   - embedding: vector(384)
   - source_url: text
   - subreddit: text nullable
   - company_tags: text[]
   - post_score: integer nullable
   - created_at: timestamptz default now()
   - content_hash: text unique (for deduplication)

Enable pgvector extension first: create extension if not exists vector;

Python db.py class (SupabaseClient):
- __init__: initialise supabase-py client from config
- log_run(agent_name, status, input_summary, output_summary, tokens_used, duration_ms, error=None)
- log_topic(topic, company) → upsert, return topic id
- log_post(topic_id, linkedin_post, critic_score, decision, pm_brief_path=None)
- update_engagement(post_id, engagement_score)
- get_recent_topics(days=30) → List of topics covered in last N days
- mark_topic_covered(topic_id, critic_score)
- store_embedding(content, embedding, metadata) → checks content_hash first, skips if exists
- similarity_search(query_embedding, match_count=15, company_filter=None) → List[dict]
- get_run_stats() → dict (total runs, success rate, avg tokens)

All methods: use try/except, log errors to console, never crash caller.
Return TypedDicts for all query results.

At the end: write a test function test_connection() that inserts and 
retrieves one row from each table, then cleans up. Run it to verify.
```

---

## SESSION 2: Scraper

**Goal:** Build the data ingestion layer

```
Read CLAUDE.md and project-log.md first.

Task: Build core/scraper.py — the data ingestion layer.

Requirements:
1. RedditScraper class using PRAW
   - Fetch top 50 posts + top 10 comments per post
   - From these subreddits: r/apple, r/google, r/spotify, r/notion, 
     r/microsoft, r/netflix, r/amazon, r/productivity, r/startups
   - Filter: last 7 days, score > 10
   - Return List[ScrapedPost] TypedDict: 
     {id, title, body, comments, subreddit, score, created_utc, url, company_tags}
   - company_tags: auto-detect company mentioned from a config list
   - Rate limit handling: exponential backoff, max 3 retries
   - Log each run to core/db.py via log_run()

2. HNScraper class using HN Firebase API (free, no key needed)
   - Endpoint: https://hacker-news.firebaseio.com/v0/
   - Fetch top 30 stories + comments
   - Filter for tech company/product discussions
   - Same TypedDict output format

3. RSScraper class using feedparser
   - Sources config list in config.py (TechCrunch, Verge, Hacker News RSS)
   - Parse last 7 days of articles
   - Extract: title, summary, url, published_date, source

4. Unified scrape_all() function:
   - Calls all three scrapers
   - Deduplicates by URL
   - Returns Dict[str, List[ScrapedPost]] keyed by source
   - Logs total counts to db

All scrapers: use -q pip installs, handle network errors gracefully,
never crash the whole pipeline on one source failing.

Put PRAW credentials in config.py as environment variable reads.
Show me config.py structure but do not hardcode any credentials.
```

---

## SESSION 3: Embedder + Supabase pgvector

**Goal:** Chunk and embed scraped content into Supabase pgvector

```
Read CLAUDE.md and project-log.md first.

Task: Build core/embedder.py — chunking and vector storage using Supabase pgvector.

Requirements:
1. Use sentence-transformers (all-MiniLM-L6-v2) for embeddings — local, free
   Output dimension: 384 (must match vector(384) column in Supabase)
2. Storage: Supabase pgvector via core/db.py store_embedding() — never call Supabase directly
3. Chunking strategy:
   - 300 token chunks, 50 token overlap
   - Use tiktoken for token counting (cl100k_base encoding)
   - Never split mid-sentence (use sentence boundary detection)
4. Metadata stored per chunk (maps to embeddings table columns):
   source_url, subreddit, company_tags, post_score, created_at
5. Deduplication via content_hash:
   - SHA-256 hash of raw content text
   - db.store_embedding() checks hash before inserting — skips duplicates silently
   - Log skipped count per batch
6. Embedder class with methods:
   - embed_batch(posts: List[ScrapedPost]) → EmbedResult TypedDict
     {embedded_count, skipped_count, error_count, duration_ms}
   - get_collection_stats() → dict (total_vectors, oldest, newest, by_company)
   - delete_old_content(days=30) → int (deleted count, via Supabase delete)
7. embed_batch: process in batches of 50 (Supabase insert limit awareness)
8. Log every embed_batch run to core/db.py via log_run()

Test at end: embed 5 fake ScrapedPost objects, verify they appear
in Supabase dashboard and similarity_search returns them correctly.
```

---

## SESSION 4: LLM Client + Retriever

**Goal:** Build the RAG query layer — the core intelligence

```
Read CLAUDE.md and project-log.md first.

Task: Build core/llm_client.py and core/retriever.py

PART 1 — core/llm_client.py (build first):
Centralized wrapper for ALL Claude API calls in the project.
- ClaudeClient class
- Uses anthropic Python SDK
- Model: claude-haiku-4-5-20251001 (always — never change without config flag)
- Methods:
    complete(system: str, user: str, max_tokens: int = 1000) → str
    complete_json(system: str, user: str, schema_hint: str) → dict
      (complete_json retries once if JSON parse fails)
- Logging: every call logs to db (tokens_used, model, duration_ms)
- Cost tracking: estimate cost per call ($0.00025/1k input, $0.00125/1k output), log to db
- Error handling: retry on rate limit (3x exponential backoff)
- Never import this in agents directly — agents call it via dependency injection

PART 2 — core/retriever.py:
RAG query logic using Supabase pgvector + LLM.
- Retriever class, takes SupabaseClient and ClaudeClient via __init__
- retrieve(topic: str, company: str, days: int = 7) → RetrievalResult TypedDict:
    {topic, company, chunks: List[str], metadata: List[dict], chunk_count: int}
  Steps:
    1. Embed the query using sentence-transformers (same model as embedder)
    2. Call db.similarity_search(query_embedding, match_count=15, company_filter=company)
    3. Generate 2 query variations via LLM (one call), repeat steps 1-2, merge + deduplicate
    4. Filter results: only chunks from last N days (use created_at metadata)
- generate_insight(retrieval: RetrievalResult) → InsightResult TypedDict
  Uses ANALYST_SYSTEM_PROMPT and ANALYST_USER_PROMPT from PROMPT_LIBRARY.md
  Validates JSON output matches schema before returning
  If validation fails: retry once with "Output only valid JSON" appended
- Self-check before returning:
  Verify pain_points >= 3 and chunk_count >= 5
  If not: log warning and set confidence = "low"

Import prompts as string constants, not hardcoded inline.
```

---

## SESSION 5: Writer + Critic Agents

**Goal:** Build the content generation and quality gate

```
Read CLAUDE.md and project-log.md first.

Task: Build agents/writer.py and agents/critic.py

PART 1 — agents/writer.py:
- WriterAgent class, takes ClaudeClient via __init__
- generate_linkedin_post(insight: InsightResult) → PostDraft TypedDict
  Uses WRITER_SYSTEM_PROMPT from PROMPT_LIBRARY.md
  Validates: character_count <= 1300, hashtags present, hook_line present
  If invalid: regenerate once with specific correction appended
- generate_pm_brief(insight: InsightResult) → PMBrief TypedDict
  Uses PM_BRIEF_SYSTEM_PROMPT from PROMPT_LIBRARY.md
  Validates JSON schema before returning
- generate_both(insight: InsightResult) → WriterOutput TypedDict
  Calls both above in one method, single entry point for Orchestrator
- Log every generation to db via log_run()

PART 2 — agents/critic.py:
- CriticAgent class, takes ClaudeClient via __init__
- evaluate(post_draft: PostDraft, insight: InsightResult) → CriticResult TypedDict
  Uses CRITIC_SYSTEM_PROMPT from PROMPT_LIBRARY.md
  Passes: the LinkedIn post text + the source insight JSON
  Validates: all 5 scores present, total matches sum, decision field valid
  Hallucination check: verify at least 2 claims in post appear in insight.pain_points
- Decision logic (in Python, not LLM):
  total >= 18 → "auto_post"
  total 12-17 → "soft_approval" (triggers notifier)
  total < 12 → "auto_reject" (triggers retry with different topic)
- Log result to db via log_post()
- notify_if_needed(result: CriticResult) → bool
  Calls tools/notifier.py for soft_approval cases

Run a dry-run test at the end with fake insight data.
Print the full output chain: insight → post → brief → critic scores.
```

---

## SESSION 6: Scout Agent

**Goal:** Build autonomous topic discovery

```
Read CLAUDE.md and project-log.md first.

Task: Build agents/scout.py — autonomous topic discovery.

Requirements:
- ScoutAgent class
- discover_topics() → List[TopicCandidate] TypedDict:
    {topic, company, post_count, avg_score, sentiment_shift, 
     last_covered, novelty_score, recommended}

Steps the agent must do internally:
1. Pull last 7 days from Supabase embeddings table — group by company_tags
2. For each company: count rows, calculate avg post_score, 
   calculate sentiment_shift (this week vs last week avg)
3. Check db.get_recent_topics(days=30) from Supabase — mark topics covered recently
4. Score each topic via SCOUT_SCORING_PROMPT (one LLM call, all topics together)
5. Return top 5 ranked by score, filter out last_covered < 30 days
6. Log run to db

Sentiment shift calculation (in Python, no LLM):
- Use a simple positive/negative word list for speed (no model call)
- shift = this_week_avg - last_week_avg on a -1 to 1 scale
- Ship a SENTIMENT_WORDS dict in config.py (50 positive, 50 negative words)

novelty_score formula (in Python):
- base: sentiment_shift * 0.4 + normalized_post_count * 0.3 + llm_score * 0.3
- penalty: -3 if last_covered within 30 days, -1 if within 60 days
- bonus: +1 if last_covered is None (never covered)

Include a test: run discover_topics() in dry-run mode with 
mock ChromaDB data. Print ranked topics table.
```

---

## SESSION 7: LangGraph Orchestrator

**Goal:** Wire all agents into an autonomous stateful graph

**Note:** This is the most complex session. Expect to do the handoff 
protocol 1-2 times within this session. Do not rush it.

```
Read CLAUDE.md and project-log.md first.

Task: Build agents/orchestrator.py using LangGraph.

This is the most complex file. Before writing any code, show me:
1. The complete LangGraph state schema (TypedDict)
2. All nodes (one per agent + decision nodes)
3. All edges including conditional edges
4. The retry logic design

Wait for my approval of the design before writing code.

Once approved, build:

STATE SCHEMA (InsightPulseState TypedDict):
- week_plan: List[dict]
- current_topic: dict
- scraped_data: dict
- retrieval_result: dict (from Analyst)
- insight: dict
- post_draft: dict  
- pm_brief: dict
- critic_result: dict
- retry_count: int (max 3)
- errors: List[str]
- run_id: str
- status: str

NODES:
1. plan_node — calls ScoutAgent, creates week_plan
2. select_topic_node — picks next topic from week_plan
3. retrieve_node — calls Retriever.generate_insight()
4. write_node — calls WriterAgent.generate_both()
5. critique_node — calls CriticAgent.evaluate()
6. post_node — calls linkedin_poster (dry_run flag)
7. alert_node — calls notifier for soft_approval
8. retry_node — increments retry_count, selects new topic
9. end_node — logs completion, updates db

CONDITIONAL EDGES from critique_node:
- "auto_post" → post_node
- "soft_approval" → alert_node → END (wait for human)
- "auto_reject" AND retry_count < 3 → retry_node → select_topic_node
- "auto_reject" AND retry_count >= 3 → alert_node (escalate) → END

ORCHESTRATOR CLASS:
- __init__: builds the graph, compiles it
- run_weekly(dry_run: bool = True) → RunResult TypedDict
- run_single(topic: str, company: str, dry_run: bool = True) → RunResult
- get_graph_image() → saves LangGraph visualization as PNG (portfolio artifact)

main.py: APScheduler cron — run_weekly() every Monday and Thursday at 9 AM.
Also expose a CLI: python main.py --dry-run --topic "Spotify pricing"
```

---

## SESSION 8: Distribution + Alerts

```
Read CLAUDE.md and project-log.md first.

Task: Build tools/linkedin_poster.py and tools/notifier.py

PART 1 — tools/linkedin_poster.py:
- LinkedInPoster class
- post(content: str, dry_run: bool = True) → PostResult TypedDict
- dry_run=True: log to db and print, do not actually post
- dry_run=False: post via LinkedIn unofficial API or Buffer API
- Rate limiting: max 1 post per 6 hours (enforce via db check)
- If rate limited: log and return PostResult with status="rate_limited"
- Log every attempt to db

Research and use whichever is currently working:
Option A: linkedin-api (unofficial Python library)
Option B: Buffer free tier API (more stable)
Tell me which you recommend and why before implementing.

PART 2 — tools/notifier.py:
- Notifier class, configured via config.py
- notify_soft_approval(post: PostDraft, score: CriticResult) → bool
  Sends Slack webhook message (use SLACK_WEBHOOK_URL from env)
  Message format: score breakdown + post preview + "Reply Y to approve, N to reject"
- notify_weekly_digest(stats: WeekStats) → bool
  Sends weekly summary every Sunday at 8 PM
  Include: posts sent, avg critic score, top performing topic, next week preview
- notify_error(agent: str, error: str) → bool
  Fires on 3 consecutive agent failures
- Fallback: if Slack not configured, send via Gmail API instead
```

---

## SESSION 9: Dashboard + PM Brief PDF

```
Read CLAUDE.md and project-log.md first.

Task: Build dashboard/app.py (Streamlit) and tools/pdf_generator.py

PART 1 — tools/pdf_generator.py:
Use ReportLab to generate PM brief PDFs.
- generate_brief(pm_brief: PMBrief, output_path: str) → str (file path)
- Professional layout: header with InsightPulse branding, sections for each 
  PMBrief field, clean typography, save to data/briefs/

PART 2 — dashboard/app.py:
Streamlit dashboard with 4 tabs:

Tab 1 — Live Status:
- Last run timestamp and status
- Current week's plan (topics + companies)
- Next scheduled run
- Quick stats: posts this week, avg score, total posts ever

Tab 2 — Post History:
- Table: date | topic | company | critic score | decision | engagement
- Click row to expand: show full LinkedIn post + PM brief download link
- Filter by: date range, decision type, company

Tab 3 — Topic Explorer:
- ChromaDB stats: total chunks, date range, sources breakdown
- Top companies by mention count (bar chart)
- Sentiment trend over time (line chart, by company)

Tab 4 — Run Logs:
- Table of all agent runs: timestamp | agent | status | tokens | error
- Error log with expandable details
- Token usage over time (line chart)

Keep it simple — functional over pretty. No external CSS.
```

---

## DEBUGGING PROMPTS (use when things break)

### LangGraph Error
```
Read CLAUDE.md. I have a LangGraph error. Here is the complete context:

State schema: [paste full TypedDict]
Graph definition: [paste full graph build code]
Error: [paste full traceback]

Do not suggest fixes without explaining the root cause first.
```

### ChromaDB Retrieval Quality Issue
```
Read CLAUDE.md. The retriever is returning low-quality chunks.
Here is the retrieval result: [paste JSON]
Here is what was expected: [describe]
The topic was: [topic], company: [company]

Diagnose: is this a chunking issue, embedding issue, or query issue?
Suggest one specific fix with code.
```

### Agent Output Validation Failure
```
Read CLAUDE.md. @agents/[agent].py is producing invalid JSON output.
The prompt used: [paste from PROMPT_LIBRARY.md]
The raw LLM output: [paste]
The validation error: [paste]

Fix the prompt in PROMPT_LIBRARY.md and the validation logic in the agent.
Show both changes.
```

---

## END-OF-SESSION CHECKLIST

Paste this at the end of every session:
```
Before we end this session:
1. Update CLAUDE.md Learning Log with any workarounds found today
2. List every file modified or created this session
3. Summarize this session's output for project-log.md (max 200 words)
4. What is the exact next task for the next session?
```
