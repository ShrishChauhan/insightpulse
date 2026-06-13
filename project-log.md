# InsightPulse — Project Log
# Updated at the end of every Claude Code session via handoff prompt.
# This file is the bridge between sessions. Keep it current.

---

## CURRENT STATUS
Phase: 2 — Writer + Critic agents complete
Last updated: 2026-05-28
Last session: Session 5 — agents/writer.py, agents/critic.py, tools/notifier.py

---

## WHAT EXISTS
Full project scaffold (22 files, all with docstrings). Config, gitignore,
.env.example all complete. Supabase schema live with 5 tables + pgvector.
core/db.py fully implemented and tested against real Supabase project.
core/scraper.py fully implemented: RedditScraper (PRAW, top-of-week,
score>10, 10 comments/post), HNScraper (Firebase API, relevance-filtered),
RSScraper (feedparser, 5 feeds). Unified scrape_all() deduplicates by URL
across all sources. All scrapers log to Supabase runs table. ScrapedPost
TypedDict is the single unified type (RSS normalized in, not separate).
config.py has RSS_FEEDS list (TechCrunch, TheVerge, HackerNews, ArsTechnica, Wired).

---

## BUILD ORDER (check off as completed)

### Week 1-2: Core Pipeline (Thin Vertical Slice)
- [x] Project scaffold (folders + empty files)
- [x] core/db.py — Supabase schema + logging helpers
- [x] core/scraper.py — Reddit (PRAW) + HN + RSS ingestion
- [x] core/embedder.py — Chunking + Supabase pgvector storage
- [x] core/llm_client.py — Centralized LLM call wrapper (Groq/Gemini/Anthropic)
- [x] core/retriever.py — RAG query + multi-query variation + insight generation
- [ ] End-to-end test: scrape → embed → retrieve → insight in terminal

### Week 3: Critic + Writer Agents
- [ ] agents/critic.py — Scoring rubric (1-25), JSON output
- [ ] agents/writer.py — LinkedIn post + PM brief generation
- [ ] Style calibration: feed 5 writing samples, verify voice match
- [ ] End-to-end test: topic → insight → post → critic score in terminal

### Week 4: Scout Agent
- [x] agents/scout.py — Topic discovery + novelty filter
- [x] Supabase topic history (prevent repeating topics < 30 days)
- [x] Scoring: volume + sentiment shift magnitude
- [ ] End-to-end test: scout returns 5 ranked topics

### Week 5: Orchestrator + LangGraph Wiring
- [ ] Define LangGraph state schema (TypedDict)
- [ ] Wire all agents as LangGraph nodes
- [ ] Add conditional edges: critic score → auto-post vs alert vs retry
- [ ] agents/orchestrator.py — Full graph, failure handling
- [ ] End-to-end test: full autonomous run, dry-run mode

### Week 6: Distribution + Alerts
- [x] tools/linkedin_poster.py — Buffer API v1, queue guard, dry_run mode
- [x] tools/notifier.py — Slack webhook + console fallback, 3 alert types
- [ ] Soft-approval flow: borderline posts (12-17/25) trigger alert
- [ ] Weekly digest email: posts sent, engagement, next topics

### Week 7: Dashboard + PM Brief PDF
- [ ] tools/pdf_generator.py — PM brief as formatted PDF
- [ ] dashboard/app.py — Streamlit: runs, scores, post history, topics
- [ ] main.py — APScheduler cron wiring

### Week 8-10: Polish + Portfolio Artifacts
- [ ] Calibration run: monitor 4 posts end-to-end
- [ ] Architecture diagram (for portfolio)
- [ ] 1-page PRD document
- [ ] README.md (clean, portfolio-grade)
- [ ] Loom walkthrough video
- [ ] LinkedIn post about the project itself

---

## KEY DESIGN DECISIONS
- Supabase everywhere: replaces ChromaDB + SQLite entirely. pgvector for
  embeddings, PostgreSQL for all run/topic/post logging.
- service_role key used for backend DB access (no RLS needed — private system).
- CLAUDE_MODEL constant in config.py is single source of truth for model name.
- CHUNK_SIZE=200 tokens (cl100k_base, tiktoken), CHUNK_OVERLAP=30 tokens — stays under all-MiniLM-L6-v2's 256-token limit.
- Startup validation in config.py raises ValueError immediately if required
  env vars are missing — fail loudly at import, not deep in the call stack.

---

## WORKAROUNDS FOUND
- Windows cp1252 terminal rejects Unicode — use ASCII in print statements.

---

## INFRASTRUCTURE DECISIONS & DEBUGGING NOTES

### IVFFLAT Index — Supabase pgvector
- Status: DROPPED for development
- Why dropped: lists=100 configured on 5 rows caused all ANN probes to miss
  silently — similarity_search returned empty with no error
- Fix applied: dropped index entirely for dev phase
- When to re-add: once embeddings table reaches 1000+ rows
- Formula: lists = ceil(row_count / 1000)
- Command to re-add (paste into Supabase SQL editor when ready):
  CREATE INDEX ON embeddings USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = [calculated_value]);
- Related workarounds:
  * match_embeddings uses text parameter (not vector) due to PostgREST
    JSON array coercion limitation — cast to vector happens inside plpgsql
  * bulk_store_embeddings uses upsert with ignore_duplicates=True
    instead of pre-check+insert (pre-check via .in_() was unreliable)

---

## OPEN QUESTIONS
- Which subreddits to target initially? (defaulted to company subs + productivity/startups)
- LinkedIn posting: unofficial API or Buffer free tier?
- Slack vs Gmail for critic alerts?
- PM brief format: PDF or Notion page?

---

## WHAT EXISTS (updated)
All of Session 2 above, plus:
core/embedder.py fully implemented: Embedder class with embed_batch(),
get_collection_stats(), delete_old_content(). Sentence-boundary chunker
(200 token chunks, 30 overlap, tiktoken cl100k_base). Content format:
[TITLE]+[BODY]+top-5 [COMMENTS]. bulk_store_embeddings() added to db.py
(upsert-based dedup). match_embeddings and match_embeddings_filtered SQL
functions deployed to Supabase (text param, plpgsql, cast to vector inside).
IVFFLAT index dropped — too few rows for lists=100. Smoke test: 5 fake
posts embedded, similarity search returns top-3 hits with correct scores.
config.py updated: CHUNK_SIZE=200 tokens, CHUNK_OVERLAP=30, EMBED_BATCH_SIZE=50.

---

## SESSION 1 — HANDOFF SUMMARY

**What was built:**
Full project scaffold — 22 files created with docstrings, folder structure for
all agents and core modules. Supabase project created with 5 tables (runs,
embeddings, posts, topics, engagement) + pgvector extension enabled. core/db.py
fully implemented: SupabaseClient class, all logging helpers (log_run, log_post,
log_topic), similarity_search() RPC wrapper, tested against live Supabase project.

**Files created/modified (session 1):**
- All scaffold files (22) — agents/, core/, tools/, dashboard/, tests/ stubs
- `config.py` — initial version, startup validation
- `.env`, `.env.example`, `.gitignore`
- `core/db.py` — full implementation

**Key design decisions made:**
- Supabase everywhere: replaces ChromaDB + SQLite entirely
- service_role key for backend (no RLS)
- Startup validation raises ValueError at import time on missing env vars

**Next task at end of session:** core/scraper.py

---

## SESSION 2 — HANDOFF SUMMARY

**What was built:**
core/scraper.py fully implemented. RedditScraper via PRAW (top-of-week posts,
score>10, top 10 comments/post). HNScraper via Firebase API (relevance-filtered).
RSScraper via feedparser (5 feeds: TechCrunch, TheVerge, HackerNews, ArsTechnica,
Wired). Unified scrape_all() deduplicates by URL across all sources. ScrapedPost
TypedDict is the single unified type — RSS normalized in, not a separate type.
All scrapers log runs to Supabase. RSS_FEEDS list added to config.py.

**Files created/modified (session 2):**
- `core/scraper.py` — full implementation
- `config.py` — RSS_FEEDS list added

**Key design decisions made:**
- Single ScrapedPost TypedDict across all sources (no source-specific types)
- Dedup by URL at scrape_all() level, not per-scraper

**Next task at end of session:** core/embedder.py

---

## SESSION 3 — HANDOFF SUMMARY

**What was built:**
core/embedder.py fully implemented: Embedder class with embed_batch(),
get_collection_stats(), delete_old_content(). Sentence-boundary chunker
(200 token chunks, 30 overlap, tiktoken cl100k_base). Content format:
[TITLE]+[BODY]+top-5 [COMMENTS]. bulk_store_embeddings() added to db.py
(upsert-based dedup). match_embeddings and match_embeddings_filtered SQL
functions deployed to Supabase. IVFFLAT index dropped (lists=100 on 5 rows
caused silent empty returns). Smoke test: 5 fake posts embedded, similarity
search returns top-3 hits with correct scores.

**Files created/modified (session 3):**
- `core/embedder.py` — full implementation
- `core/db.py` — bulk_store_embeddings() added
- Supabase SQL — match_embeddings, match_embeddings_filtered RPC functions deployed

**Workarounds found (3):**
1. PostgREST can't coerce JSON array to vector — RPC params must be `text`, cast
   to `vector` inside plpgsql body; pass `str(list)` from Python
2. IVFFLAT with lists > row_count silently returns empty — drop index for dev,
   re-add at 1000+ rows with lists = ceil(row_count / 1000)
3. supabase-py `.in_()` pre-check unreliable — use upsert with
   ignore_duplicates=True instead

**Next task at end of session:** core/llm_client.py + core/retriever.py

---

## SESSION 4 — HANDOFF SUMMARY

**Learning Log additions (1 new entry):**
- `runs` table has a CHECK constraint on status — only schema-defined values accepted; arbitrary strings raise 23514 silently

**Files modified/created this session (7):**
1. `CLAUDE.md` — tech stack, cost constraint, Learning Log x4
2. `config.py` — provider constants + conditional validation
3. `.env` — PROVIDER=groq, GROQ_API_KEY
4. `.env.example` — PROVIDER, GROQ_API_KEY, GEMINI_API_KEY
5. `core/llm_client.py` — full implementation
6. `core/retriever.py` — full implementation
7. `prompts.py` — created (importable prompt constants)

**Next task (Session 5):**
End-to-end pipeline test — scrape 1-2 subreddits, embed results, call
Retriever.retrieve() + generate_insight() with a real topic, verify InsightResult
returns with pain_points, confidence, source_count. Then agents/critic.py.

---

## SESSION 4 — WHAT WAS BUILT
LLM provider abstraction: config.py now has PROVIDER env var selecting between
groq (dev, free, 14400 req/day), gemini, or anthropic. core/llm_client.py fully
implemented — LLMClient class with complete(), complete_json() (JSON fence
stripping + one retry on parse failure), 3x exponential backoff on rate limits,
cost logging to Supabase runs table. Groq provider uses Llama 3.3 70B via
groq SDK. core/retriever.py fully implemented — Retriever class with retrieve()
(primary embed + 2 LLM-generated query variations, merged + deduplicated, date-
filtered) and generate_insight() (fills ANALYST_USER_PROMPT, validates schema,
sets confidence=low if pain_points<3 or chunk_count<5). prompts.py created as
importable Python constants for all agent prompts. Smoke test passed: Groq
returns valid parsed JSON dict. Workaround: runs table status is CHECK-
constrained — use "success"/"error" only, never arbitrary strings.

---

## SESSION 5 — HANDOFF SUMMARY

**Files created/modified (4):**
1. `agents/writer.py` — WriterAgent with PostDraft/PMBrief/WriterOutput TypedDicts;
   generate_linkedin_post (validate chars<=1300, hashtags, hook_line; 1 retry),
   generate_pm_brief (validate required fields; 1 retry), generate_both as entry point.
   Two separate log_run() calls for attributable cost/timing per output type.
2. `agents/critic.py` — CriticAgent with CriticResult TypedDict; evaluate() calls LLM
   then Python overrides decision from CRITIC_THRESHOLDS; Python keyword hallucination
   gate (2+ pain_point title keywords in post); notify_if_needed() for soft_approval.
3. `tools/notifier.py` — send_soft_approval_alert(): Slack webhook if configured,
   console fallback otherwise. Full webhook implementation deferred to Session 6.
4. `tests/test_writer_critic_dryrun.py` — fake InsightResult (Spotify/playlist),
   full chain dry-run: insight -> post -> brief -> critic scores -> notify check.

**Key design decisions:**
- LLM outputs decision field; Python ignores it and recomputes from CRITIC_THRESHOLDS.
  Threshold adjustable via config without touching prompts.
- Hallucination check: Python keyword gate (words >4 chars from pain_point titles),
  LLM's hallucination_detail used as explanation only. Python is the gate.
- total recomputed by Python summing all 5 scores; LLM self-reported sum discarded.

**Dry-run observations:**
- Post came in at 225 chars (well under 1300); validation passed first attempt — no retry triggered.
- notify_if_needed returned False correctly: score was 22 (auto_post range, not soft_approval).
- Hallucination check passed: "repetitive", "playlist", "discovery" matched pain_point title keywords.

**Build order updated:**
- [x] agents/writer.py
- [x] agents/critic.py

---

## SESSION 5 — WHAT WAS BUILT
agents/writer.py fully implemented: WriterAgent class with PostDraft, PMBrief, WriterOutput
TypedDicts. generate_linkedin_post() calls LLM with WRITER_SYSTEM_PROMPT, validates
char_count<=1300 + hashtags present + hook_line present, regenerates once with inline
correction if invalid, recomputes character_count in Python (ignores LLM's self-report).
generate_pm_brief() calls LLM with PM_BRIEF_SYSTEM_PROMPT, validates all 8 required fields,
retries once listing missing fields explicitly. generate_both() is the single Orchestrator
entry point; logs two separate log_run() calls for cost/timing attribution per output type.
agents/critic.py fully implemented: CriticAgent class with CriticResult TypedDict. evaluate()
calls LLM with CRITIC_SYSTEM_PROMPT, validates all 5 score fields present, recomputes total
by summing in Python (LLM sum discarded), overrides decision using CRITIC_THRESHOLDS from
config (>=18 auto_post, 12-17 soft_approval, <12 auto_reject). Python hallucination gate:
extracts words >4 chars from pain_point titles, checks 2+ appear in post text as substrings;
LLM's hallucination_detail kept as explanation only. notify_if_needed() calls notifier for
soft_approval decisions. tools/notifier.py: send_soft_approval_alert() posts to Slack webhook
if SLACK_WEBHOOK_URL configured, falls back to console print. Dry-run passed: Spotify/playlist
fake insight produced 22/25 score -> auto_post, hallucination check passed, notifier
correctly skipped.

---

## SESSION 6 — HANDOFF SUMMARY

**Files created/modified (6):**
1. `config.py` — SENTIMENT_WORDS dict added (50 positive, 50 negative words)
2. `core/db.py` — fixed get_recent_topics(): SQL string literal -> Python ISO timestamp
3. `agents/scout.py` — full implementation + 2 bug fixes (see below)
4. `CLAUDE.md` — 3 Learning Log entries added

**What was built:**
ScoutAgent with TopicCandidate TypedDict (8 fields). discover_topics() fetches 14 days
from embeddings, groups by company_tags[0] in Python, computes post_count/avg_score/
sentiment_shift (SENTIMENT_WORDS word-list, no LLM), checks get_recent_topics(days=30)
for coverage penalties, one LLM call with SCOUT_SCORING_PROMPT for all topics, novelty_score
in Python (abs(sentiment_shift)*0.4 + norm_count*0.3 + norm_llm*0.3, +1 never covered,
-3/-1 covered <30/<60 days), top 5 ranked. _mock_rows injection for dry-run. Dry-run
passed: 5 real Supabase rows (apple), novelty=1.826, run logged.

**Bug fixes:**
- complete_json() called with wrong kwarg system_prompt= (should be system=)
- log_run() called with status="error" (constraint allows only success/failed/skipped)

**Learning Log additions (3):**
- supabase-py .gt() — always use Python ISO timestamps, never SQL expressions
- complete_json() parameter is 'system' not 'system_prompt'
- runs table status: only success, failed, skipped — never 'error'

---

## SESSION 7 — HANDOFF SUMMARY

**Files created/modified (3):**
1. `agents/orchestrator.py` — full LangGraph implementation. OrchestratorAgent with
   InsightPulseState TypedDict (12 fields), 9 nodes, conditional entry router
   (plan path vs run_single bypass), critique_router with two retry counters
   (writer_retry_count < 1 for format failures, retry_count < 3 for topic retries).
   RunResult TypedDict. run_weekly() (Scout + 2 topics), run_single() (direct topic
   bypass), get_graph_image() (PNG portfolio artifact).
2. `main.py` — APScheduler cron (Monday + Thursday 09:00 UTC), CLI with --dry-run,
   --topic, --company, --schedule flags. Startup log to Supabase on every invocation.
3. `core/retriever.py` — bugfix line 71: Embedder(self.db) -> Embedder() (Embedder
   takes no constructor args).

**Dry-run result:**
python main.py --dry-run --topic "Spotify pricing" --company "spotify"
-> retrieve_node: 0 chunks (empty Supabase), write_node: 226 chars,
   critique_node: 15/25 -> soft_approval (correct: no embeddings = low confidence).
   errors=0, data/graph_viz.png created (40KB, portfolio artifact).

**Build order updated:**
- [x] agents/orchestrator.py
- [x] main.py (APScheduler + CLI)

**Learning Log addition:**
- Embedder.__init__() takes no args — Retriever lazy-loads as Embedder(), not Embedder(self.db).

## SESSION 8 — HANDOFF SUMMARY

**Files created/modified (6):**
1. `config.py` — LINKEDIN_EMAIL/PASSWORD replaced with BUFFER_ACCESS_TOKEN/PROFILE_ID
2. `.env.example` — same, with Buffer setup instructions
3. `tools/linkedin_poster.py` — full BufferPoster: post(), get_profiles(),
   check_queue_count(), _call_buffer_api(). Queue guard at 9/10. Never crashes
   if credentials missing — returns status="not_configured".
4. `tools/notifier.py` — full Notifier class overwriting Session 5 stub.
   notify_soft_approval() (5-dimension score breakdown + preview + Slack),
   notify_weekly_digest(), notify_error(). Console-only if no Slack webhook.
5. `agents/critic.py` — notify_if_needed() updated: now accepts post_draft param,
   imports Notifier class (not old send_soft_approval_alert). Optional added.
6. `agents/orchestrator.py` — dry_run: bool added to InsightPulseState; _dry_run
   hack removed from both invoke() calls; BufferPoster imported + self._poster
   instantiated; _post_node fully wired (no TODO); _alert_node passes post_draft
   to notify_if_needed.

**Smoke tests passed:**
- BufferPoster dry_run -> status='dry_run', Supabase runs row confirmed
- Notifier.notify_soft_approval() -> all 5 scores formatted, returned True

**New workarounds:** None.

**Build order updated:**
- [x] tools/linkedin_poster.py
- [x] tools/notifier.py

## SESSION 9 — HANDOFF SUMMARY

**Files created/modified (2):**
1. `tools/pdf_generator.py` — full ReportLab implementation. generate_brief(pm_brief, output_path) -> str.
   InsightPulse-branded header, 7 sections (exec summary, problem, user evidence, proposed feature
   2-column table, success metrics, risks, competitive context). All fields None-safe via _safe()
   fallback to "Not available". Creates data/briefs/ directory automatically. Smoke test passed.
2. `dashboard/app.py` — full Streamlit dashboard. 4 tabs: Live Status (last run + quick stats +
   upcoming topics from topics table uncovered rows), Post History (3 filters + selectbox row
   expansion + PDF download button), Topic Explorer (embedding stats + company chunks bar chart +
   avg quality score bar chart + posts-per-week bar chart, all with 3-point threshold guards),
   Run Logs (agent filter + errors-only checkbox + token usage line chart). All charts and tables
   handle empty Supabase data with placeholder messages. HTTP 200 confirmed on startup.

**Packages installed:** reportlab, streamlit, pandas (pip install -q).

**Learning Log additions (2):**
- st.cache_resource for Supabase Client singleton; st.cache_data for data fetchers — never pass Client as arg.
- Use python -m streamlit run (streamlit binary not on PATH in Windows bash).

**Build order updated:**
- [x] tools/pdf_generator.py
- [x] dashboard/app.py

---

## SESSION 9 — CLOSING SUMMARY (Git push + housekeeping)

**Files created/modified (5):**
1. `.gitignore` — added `.handoff-log`, `*.db`, `*.sqlite`, `.DS_Store`, `Thumbs.db`,
   `data/briefs/`, `.claude/` (local Claude Code config must never be committed)
2. `.env.example` — full rewrite: all config.py keys covered, forward-looking
   `LINKEDIN_ACCESS_TOKEN` + `LINKEDIN_PERSON_URN` added for future direct API migration
3. `README.md` — created: professional portfolio-grade README with architecture table,
   agent roster, tech stack, project structure tree, setup instructions, CLI flags
4. `requirements.txt` — added missing: `groq`, `google-genai`, `tiktoken`, `pandas`
5. `CLAUDE.md` — Learning Log entry added: verify .env not staged before push; add .claude/ to .gitignore

**Git state:**
- Repo initialized, 31 files committed (b5f8ee0), pushed to main
- Remote: https://github.com/ShrishChauhan/insightpulse.git
- .env, .venv/, data/, .claude/ all correctly excluded

**API gap research completed (Session 9 plan):**
- Reddit API (PRAW): credentials pending, code complete
- Buffer API: credentials pending, code complete, free tier sufficient (10 posts/month)
- Gemini 2.0 Flash: implemented, just needs API key (aistudio.google.com) as Groq fallback
- Slack webhook: optional, zero code changes needed

---

## NEXT SESSION TASK
Session 10: Full end-to-end pipeline test with real data.
1. Run core/scraper.py scrape_all() against live HN + RSS (skip Reddit) — print post count.
2. Run core/embedder.py embed_batch() on scraped posts — confirm Supabase embeddings row count > 0.
3. Run main.py --dry-run --topic [real topic from scrape] --company [matching company].
   Target: retrieval returns >5 chunks, critic score >= 18, post_node reaches BufferPoster.post(dry_run=True),
   Supabase posts row created, PDF written to data/briefs/.
4. Open dashboard/app.py and verify all 4 tabs render real data (not empty placeholders).
