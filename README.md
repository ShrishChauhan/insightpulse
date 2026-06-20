# InsightPulse

> Autonomous AI agent that monitors tech product discourse, extracts PM-grade insights, and publishes to LinkedIn — fully automated with a quality-gated critic agent.

## What It Does

InsightPulse scrapes Reddit, Hacker News, and RSS tech feeds daily, embeds the content into a Supabase pgvector store, and runs a multi-agent pipeline to surface trending product pain points. A Scout agent identifies the highest-novelty topics; a Retriever builds RAG context from the vector store; a Writer generates a LinkedIn post and a structured PM brief; a Critic scores the output 1–25 and either auto-posts, escalates for human review, or retries with a new topic — all without manual intervention.

## Architecture

```
Scrape → Embed → Scout → Retrieve → Analyze → Write → Critique → Post
```

### Agent Roster

| Agent | File | Role |
|---|---|---|
| Orchestrator | `agents/orchestrator.py` | Sequences all agents via LangGraph; handles retries and failures |
| Scout | `agents/scout.py` | Discovers trending topics; scores novelty against coverage history |
| Analyst | `agents/analyst.py` | RAG retrieval + structured insight generation from source chunks |
| Writer | `agents/writer.py` | Generates LinkedIn post and PM brief from analyst insights |
| Critic | `agents/critic.py` | Scores posts 1–25 on a rubric; gates auto-post vs. human review |

### Core Modules

| Module | File | Role |
|---|---|---|
| Scraper | `core/scraper.py` | Reddit (PRAW), HN Firebase API, RSS ingestion |
| Embedder | `core/embedder.py` | Sentence-boundary chunking + Supabase pgvector storage |
| Retriever | `core/retriever.py` | Multi-query RAG search + insight synthesis |
| LLM Client | `core/llm_client.py` | Single gateway for all LLM calls (Groq / Gemini / Claude) |
| DB | `core/db.py` | All Supabase reads and writes — runs, topics, posts, embeddings |

## Tech Stack

| Technology | Role |
|---|---|
| LangGraph | Stateful multi-agent orchestration with conditional edges |
| APScheduler | Cron-based autonomous scheduling (Mon + Thu, 09:00 UTC) |
| Supabase pgvector | Vector embeddings store + PostgreSQL for all run logging |
| sentence-transformers | Local embedding model (all-MiniLM-L6-v2, 384-dim, free) |
| Groq Llama 3.3 70B | LLM inference in dev (free tier, 14,400 req/day) |
| Claude Haiku | LLM inference in prod (swap via `PROVIDER=anthropic`) |
| PRAW | Reddit API client for top posts + comments |
| feedparser | RSS feed parsing (TechCrunch, The Verge, Ars Technica, Wired) |
| Streamlit | Real-time monitoring dashboard |
| ReportLab | PM brief PDF generation |
| Buffer API v1 | LinkedIn post scheduling (free tier) |

## Project Structure

```
insightpulse/
├── agents/
│   ├── orchestrator.py   # LangGraph graph: plan→select→retrieve→write→critique→post
│   ├── scout.py          # Topic discovery, novelty scoring, coverage dedup
│   ├── analyst.py        # RAG retrieval wrapper (thin — logic lives in retriever)
│   ├── writer.py         # LinkedIn post + PM brief generation with validation
│   └── critic.py         # 1-25 scoring rubric, hallucination gate, post/alert/retry
├── core/
│   ├── scraper.py        # Reddit, HN, RSS scrapers; unified ScrapedPost TypedDict
│   ├── embedder.py       # Chunker, sentence-transformers, bulk upsert to Supabase
│   ├── retriever.py      # Similarity search, query variations, insight generation
│   ├── llm_client.py     # Provider-agnostic LLM client; all agents call this only
│   └── db.py             # SupabaseClient: log_run, log_post, log_topic, similarity_search
├── tools/
│   ├── linkedin_poster.py  # Buffer API v1 wrapper with queue guard and dry-run mode
│   ├── notifier.py         # Slack webhook alerts: soft-approval, weekly digest, errors
│   └── pdf_generator.py    # ReportLab PM brief PDF with branded header and 7 sections
├── dashboard/
│   └── app.py            # Streamlit: Live Status, Post History, Topic Explorer, Run Logs
├── prompts.py            # All LLM prompt constants — never hardcode prompts in agents
├── config.py             # All env vars loaded here — modules import config, not os.environ
├── main.py               # Entry point: APScheduler cron + CLI flags
├── .env.example          # Template — copy to .env, never commit .env
└── project-log.md        # Session-by-session build log and design decisions
```

## Setup

### Prerequisites

- Python 3.11+
- Supabase account (free tier)
- Groq API key (free tier, no credit card required)

### Installation

```bash
git clone https://github.com/ShrishChauhan/insightpulse.git
cd insightpulse
pip install -r requirements.txt
cp .env.example .env
# Fill in your credentials in .env
```

### Running

```bash
# Dry run — full pipeline, no live posting
python main.py --dry-run

# Single topic test
python main.py --dry-run --topic "Spotify playlist discovery" --company "spotify"

# Start autonomous scheduler (Mon + Thu 09:00 UTC)
python main.py --schedule

# Launch monitoring dashboard
python -m streamlit run dashboard/app.py
```

## Key Features

- **Multi-agent LangGraph orchestration** with automatic retry logic — topic retries (up to 3) and writer retries (up to 1) before escalating to human review
- **Quality-gated posting** — critic scores every post 1–25 across five dimensions; auto-posts only on 18+, soft-approval alert on 12–17, rejects below 12
- **Hallucination check** — Python keyword gate verifies that 2+ pain-point title terms from source chunks appear in the final post before it can be published
- **Provider-agnostic LLM client** — swap Groq, Gemini, or Claude Haiku via a single `PROVIDER` env var; no agent code changes required
- **Full observability** — every agent run, LLM call, topic selection, and post decision is logged to Supabase with duration, token count, and status
- **Embedding deduplication** — content hash upsert ensures existing chunks are never re-embedded; cache-first by default

## Status

**Pipeline operational.** End-to-end dry-run passed 19/25 (auto_post threshold). HN + RSS sources active (247 chunks in Supabase). LinkedIn Consumer API integrated. Reddit API credentials pending — all other sources live.

## Automation

InsightPulse runs fully autonomously via GitHub Actions:
- Daily 6AM UTC: scrape all sources + embed into Supabase
- Monday/Thursday 9AM UTC: generate and publish LinkedIn post
- Sunday 7AM UTC: Gold Mining — targeted Reddit pain thread extraction

No local machine required. Workflow runs are visible at
github.com/ShrishChauhan/insightpulse/actions

## Built By

Shrish Chauhan — Engineering student targeting APM/RPM roles.  
[LinkedIn](https://www.linkedin.com/in/shrish-chauhan)

See BUILD_JOURNAL.md for the full engineering narrative — roadblocks, recoveries, and design decisions.
