# InsightPulse — Project Constitution
# Claude Code reads this file automatically every session.
# Keep under 200 lines. This is an index, not a data dump.

---

## WHAT THIS PROJECT IS
Autonomous multi-agent system that scrapes Reddit/HN/RSS, runs RAG 
analysis, generates LinkedIn posts + PM briefs, and posts autonomously 
with a quality-gated critic agent. Built to demonstrate AI/ML + product 
thinking for APM/RPM recruiting.

---

## BEHAVIOR RULES (follow automatically, no reminders needed)

### Before writing any code
- Read the target file fully before editing it.
- State your plan in max 3 bullets. Ask questions until 95% confident.
- Never generate code on an ambiguous spec. Ask first.
- Never edit more than one file per response unless explicitly instructed.

### Communication style
- No preamble. No "Great question!" No summaries after code blocks.
- Output code first. Explain only if asked.
- Never re-explain something already established in this conversation.
- If correcting a mistake: say "CORRECTION:" and fix it inline.

### Shell command discipline (critical for token hygiene)
- Always use -q or --quiet to suppress verbose output.
- NEVER run these without flags:
    git log              → use: git log --oneline -10
    git status           → use: git status -s  
    git diff             → use: git diff --stat
    pip install          → use: pip install -q
    npm install          → use: npm install --silent
    ls -R                → use: ls (bounded)
- If a command might produce >20 lines of output, truncate or summarize.

### File referencing
- Always use @filename syntax when referencing other project files.
- Never read the entire repo to find something. Ask me where it is.

### Error handling
- When a workaround is found, tell me: "Add this to Learning Log."
- Never send corrective follow-ups. Tell me to Edit & Regenerate instead.

### Context self-monitoring (critical)
- At 15 exchanges: output exactly this line:
  "⚠️ CONTEXT WARNING: We're at 15 exchanges. Consider handoff soon."
- At 20 exchanges: output exactly this, then stop and wait:
  "🛑 HANDOFF NOW: Paste this into Claude Code →
   'Summarize InsightPulse state for fresh session: files built,
   design decisions, workarounds, next step. Max 300 words.'"

---

## PROJECT ARCHITECTURE

### Agent roster
| Agent | File | Role |
|---|---|---|
| Orchestrator | agents/orchestrator.py | Sequences all agents, handles failures |
| Scout | agents/scout.py | Finds trending topics, scores novelty |
| Analyst | agents/analyst.py | RAG retrieval + structured insight generation |
| Writer | agents/writer.py | LinkedIn post + PM brief generation |
| Critic | agents/critic.py | Quality scoring 1-25, auto-post or alert |

### Core modules
| Module | File | Role |
|---|---|---|
| Scraper | core/scraper.py | Reddit/HN/RSS ingestion |
| Embedder | core/embedder.py | Chunking + Supabase pgvector storage |
| Retriever | core/retriever.py | RAG query logic |
| LLM Client | core/llm_client.py | ALL LLM calls go here. Never call API directly in agents. |
| DB | core/db.py | Supabase PostgreSQL logging for all agents |

### Entry points
- main.py — scheduler + orchestrator trigger
- dashboard/app.py — Streamlit monitoring dashboard

---

## TECH STACK
- Orchestration: LangGraph (stateful multi-agent)
- Scheduling: APScheduler -- daily ingest 6AM UTC, pipeline Mon+Thu 9AM UTC, digest Sun 8PM UTC
- Vector DB: Supabase pgvector (cloud, free tier)
- Relational DB: Supabase PostgreSQL (replaces SQLite entirely)
- Embeddings: sentence-transformers (all-MiniLM-L6-v2, local, free)
- LLM: Groq Llama 3.3 70B (dev, free) / Claude Haiku (prod, swap via PROVIDER)
- Data: PRAW (Reddit), feedparser (RSS), HN Firebase API (free)
- Alerts: Slack webhook or Gmail API
- Dashboard: Streamlit
- PDF: ReportLab (PM briefs)
- MCP: Supabase MCP (Claude Code uses this to manage DB during development)

---

## CODING CONVENTIONS
- All agent outputs use TypedDict (never raw dicts)
- All LLM calls go through core/llm_client.py only
- All prompts are constants imported from PROMPT_LIBRARY.md pattern
- All agents log every run to Supabase PostgreSQL via core/db.py
- Use dataclasses for config objects
- Type hints on every function signature
- Docstring on every class and public method (one line is fine)

---

## COST CONSTRAINTS
- Target: under $12/month total
- Embeddings: sentence-transformers local (free)
- LLM calls: Gemini 2.0 Flash in dev (free tier), Claude Haiku in prod ($0.00025/1k input)
- Batch all LLM calls where possible — never call per-chunk
- Supabase: free tier (500MB storage, 2GB bandwidth) — never exceed
- Cache embeddings: check Supabase before embedding — never re-embed existing content

---

## LEARNING LOG
<!-- 
Claude appends lessons here automatically when workarounds are found.
Format: one line, under 15 words, no explanation. Never remove entries.
-->
- Windows cp1252 terminal rejects Unicode — use ASCII in print statements.
- IVFFLAT index must have roughly 1 list per 1000 rows; building with lists > row_count causes ANN search to return empty silently — drop index for dev, add back at scale.
- Use provider abstraction in llm_client.py — swap LLM via PROVIDER config, never by editing agent code.
- Use google.genai SDK not google.generativeai — latter is deprecated and will stop receiving updates.
- Groq free tier: no credit card, 14400 req/day — use for all dev; swap to Claude Haiku for prod via PROVIDER=anthropic in .env.
- runs table has a CHECK constraint on status — only schema-defined values accepted (e.g. "success", "error"); arbitrary strings raise 23514 and return None silently.
- supabase-py .gt() treats arguments as plain strings — never pass SQL expressions; always use Python-computed ISO timestamps.
- complete_json() parameter is 'system' not 'system_prompt' — check LLMClient signature before calling from any new agent.
- runs table status constraint allows only: success, failed, skipped — never pass 'error' as status value.
- Embedder.__init__() takes no args — Retriever lazy-loads it as Embedder(), not Embedder(self.db).
- Streamlit + Supabase caching: use @st.cache_resource for the Client singleton; use @st.cache_data(ttl=60) for data fetchers that return plain lists/dicts — never pass the Client as an argument to cache_data functions (not serialisable); call the resource inside instead.
- `streamlit` binary not on PATH in Windows bash shell — use `python -m streamlit run` instead.
- Always verify .env not staged before git push — use git status to confirm. Also add .claude/ to .gitignore to keep local Claude Code config out of the repo.
- Groq/Llama returns literal control characters in JSON strings — _parse_json() uses json.loads(strict=False) as fallback when strict parse fails; Python's strict=True is the default and rejects these.
- Supabase free tier pauses after ~7 days of inactivity — restore via MCP mcp__supabase__restore_project before any session that follows a gap.
- Reddit permanently closed all API access May 30 2026. Replaced with Bluesky (social), YouTube (consumer video comments), ProductHunt (launch discourse), App Store RSS (direct app reviews), HN, RSS. No reddit code remains in the project.
- Bluesky public.api.bsky.app/xrpc/app.bsky.feed.searchPosts returns 403 from non-residential IPs (same pattern as Reddit). Works on a home IP; from a cloud/VPN IP add auth via BLUESKY_APP_PASSWORD if needed.
- Apple App Store RSS feed: first entry in feed.entry[] is app metadata (no im:rating key) — skip it; only entries with im:rating are user reviews.
