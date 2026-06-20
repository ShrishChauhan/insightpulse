# InsightPulse — Build Journal

*How an autonomous PM-intelligence pipeline got built, what broke along the way, and why it isn't a no-code workflow.*

This document is the engineering counterpart to the README. The README explains *what* InsightPulse is. This explains *how it came to exist* — the decisions, the dead ends, and the recoveries. It is written for anyone (recruiters included) who wants to understand the product judgment and engineering work behind the system, not just the final feature list.

---

## 1. What InsightPulse Is, in One Paragraph

InsightPulse is a fully autonomous, multi-agent pipeline that monitors technology-product discourse across 15+ public sources, generates PM-grade insight posts, scores each post against a quality rubric using a dedicated Critic agent, and publishes the ones that pass to a LinkedIn company page — on a schedule, with no human in the loop. It runs on free infrastructure (GitHub Actions), uses a free LLM tier (Groq), and stores its knowledge in a vector database (Supabase pgvector). The interesting part is not any single component; it is that the components form a closed loop that produces, evaluates, and ships content without supervision.

---

## 2. The Build, Phase by Phase

The project was built in distinct stages, each gated by a working milestone before moving on. This staging was deliberate: it meant that at every point there was a *runnable* system, not a half-finished one.

**Foundation.** The first stage built the spine — a LangGraph orchestrator wiring together five agents (Orchestrator, Scout, Analyst, Writer, Critic), a Supabase vector store, an embedder using `all-MiniLM-L6-v2`, and a retrieval layer. The design principle was that every scraper returns the same `ScrapedPost` shape, so new sources could be added without touching the agents downstream. That decision paid off repeatedly later.

**Data ingestion.** The second stage built the scrapers and an embedding pipeline that de-duplicates by content hash before writing vectors. Sources were added incrementally, each conforming to the shared interface.

**Insight generation and quality control.** The third stage was where the product became more than a scraper. The Analyst retrieves relevant chunks and produces a structured insight; the Writer turns that into a LinkedIn-format post; the Critic scores it 0–25 against a rubric and either approves it, soft-approves it, or sends it back for regeneration. This feedback loop is the core of the system.

**Publishing.** The fourth stage connected the pipeline to LinkedIn. This turned out to be the single hardest part of the entire project (see §3.3).

**Automation.** The final stage moved everything off a local machine and onto GitHub Actions, so the pipeline runs on a cron schedule with no laptop involved.

---

## 3. Roadblocks and Recoveries

This is the heart of the document. Every one of these was a real wall, and every recovery taught something.

### 3.1 Reddit closed its doors

**The wall.** Reddit was meant to be a primary source of candid user pain — the kind of unfiltered "I hate this feature" discourse that makes for sharp PM insight. Partway through, Reddit's API access for new developers became effectively unavailable, and the public `.json` endpoint began returning `403 Forbidden` from the residential IP the project was developed on. Direct access was dead.

**The dead ends.** A long sequence of workarounds was tried and discarded, each for a concrete reason: PullPush (a Reddit archive mirror) worked but lagged by 25+ days, making it useless for current discourse; Redlib and Libreddit instances were either down or gated behind a JavaScript proof-of-work challenge that can't be automated; Teddit instances timed out; Jina.ai's reader returned Reddit's own block page because Reddit blocks Jina's servers too; Google's cache returned an interstitial, not content.

**The recovery.** The working solution was a two-stage chain. **Serper.dev** (a Google-search API with a free, no-credit-card tier) runs targeted queries like `site:reddit.com "Spotify" "I hate" OR "broken"` to *find* the relevant thread URLs. Then **Arctic Shift** (a community Reddit archive) is hit with each thread's post ID to *fetch the actual comment bodies* — and crucially, Arctic Shift returns fresh data, including threads only weeks old, and is reachable from the same IP that Reddit blocks. The post body comes from Serper's search snippet; the comments — where the real signal lives — come from Arctic Shift in full.

**The lesson.** When a primary source closes, don't look for one replacement; decompose what you actually needed from it (here: *discovery* + *content*) and solve each half with a different tool.

### 3.2 The vocabulary of failure: small, quiet bugs

Not every roadblock was dramatic. Several were one-line bugs that silently degraded quality:

- The Apple App Store RSS feed's first entry is app metadata, not a review — it has no rating field. Treating it as a review polluted the data until it was caught and skipped.
- Windows' default `cp1252` terminal encoding crashes on emoji, so any post-preview that printed an emoji threw an exception. Fixed by encoding to ASCII with replacement before printing.
- The Writer's character-limit validator was set to a stale 1,300-character cap, which silently forced the model to regenerate long, good posts into short, worse ones. Raising it to LinkedIn's real 3,000-character limit immediately improved output quality.

**The lesson.** In a generative pipeline, the dangerous bugs aren't the ones that crash — they're the ones that quietly lower quality while everything still "works." Post-build review caught these; they would never have surfaced as errors.

### 3.3 LinkedIn's walled garden

**The wall.** Posting to a LinkedIn *company page* via the API requires the `w_organization_social` scope, which is gated behind LinkedIn's Community Management API. That API is only granted to *registered legal businesses* — not individual developers or students. The application form is effectively a black hole. A student building a portfolio project simply cannot get it.

**The dead ends.** Creating a fake "InsightPulse" personal account to post from was rejected outright — it violates LinkedIn's terms and risks a ban that would take the real account down with it; that risk was not worth taking for a portfolio project. Posting to a personal profile with an attribution footer was viable but diluted the personal-brand separation that was the whole point. Manual copy-paste broke the "autonomous" claim.

**The recovery.** The solution was **Zernio**, a third-party social-API provider that *already holds* pre-approved `w_organization_social` access from LinkedIn. By connecting the InsightPulse company page through Zernio, the pipeline posts to the page through Zernio's unified endpoint — no LinkedIn business approval required. One subtlety cost an afternoon: when the connected Zernio account is *itself* typed as an organization, passing an `organizationUrn` in the payload returns a 400; the fix was to drop that field entirely and let the account's own type route the post.

**The lesson.** A platform's official gate is not always the only legitimate path through it. An intermediary that has already cleared the gate can be a clean, terms-compliant shortcut — and recognizing that is a product decision, not just an engineering one.

### 3.4 The cloud doesn't have your laptop's cache

**The wall.** Moving to GitHub Actions surfaced a class of bugs that only appear in a clean cloud environment. First: `ModuleNotFoundError: No module named 'bs4'` — a dependency that had been `pip install`-ed manually during development but never written into `requirements.txt`. Adding it didn't fix it, which led to the real cause: on the runner, the bare `pip` command resolved to a *different* Python than the one running the script, so packages installed into a site-packages directory the script never looked in. The fix was `python -m pip install`, which ties the install to the exact interpreter. Then a second wall: the embedding model download failed because Hugging Face blocks the IP ranges GitHub Actions runs on — fixed by adding an authenticated `HF_TOKEN`.

**The lesson.** "Works on my machine" is a real failure mode, not a joke. A clean-room environment is the only honest test of whether a project is actually reproducible — and reproducibility is what "autonomous" really means.

### 3.5 Bluesky's two-layer block

**The wall.** After Reddit closed, Bluesky was added as a social source. The `searchPosts` endpoint returned `403 Forbidden` from the development IP. The fix looked obvious: authenticate with an app-password to obtain a JWT, then attach it as a Bearer token. That didn't work either.

**The dead end.** The auth flow succeeded — a valid token was being obtained — but the authenticated requests were still being sent to `public.api.bsky.app`, the unauthenticated public mirror. The public mirror rejects non-residential IPs regardless of whether a token is present. A valid token sent to the wrong host still fails.

**The recovery.** The fix was routing authenticated requests to `bsky.social` instead of the public mirror. Once the session-creation POST and all subsequent search calls used `bsky.social` as the host, the endpoint returned results: 0 → 763 posts per run.

**The lesson.** Authentication and endpoint-host are two separate concerns. Fixing one and leaving the other untouched only moves the failure. The correct mental model: (1) obtain a valid credential; (2) confirm you are presenting it to the right server.

### 3.6 YouTube removed

**The decision.** YouTube was planned as a source for product-launch reactions and creator commentary. Google Cloud billing is blocked in India via UPI, which means the YouTube Data API v3 could never be provisioned. After confirming the block was not temporary, YouTube was removed from the codebase entirely rather than carried as a placeholder stub.

**The lesson.** A permanently blocked source is worth deleting, not indefinitely stubbing. Dead code is a maintenance cost — it clutters the codebase, inflates scraper counts in logs, and creates the false impression that more sources are active than actually are. When a source cannot be made functional, the right call is to acknowledge it and move on.

### 3.7 Prompt discipline and the fabrication problem

**The wall.** Early Analyst output occasionally contained plausible-sounding quotes attributed to "a Spotify user" or statistics like "67% of respondents" that appeared nowhere in the retrieved source chunks. The Writer, separately, defaulted to filler phrases ("transformative," "reimagine," "unprecedented") that read as generated rather than observed. Neither failure crashed the pipeline; both degraded the credibility of the output.

**The recovery.** Two layers of intervention. At the prompt level: the Analyst system prompt was extended with explicit rules — no synthetic quotes, no invented statistics, all claims must be traceable to a specific source chunk. The Writer prompt received a parallel ban on marketing buzzwords and a requirement that every sentence connect to a concrete observation from the insight data. At the code level: a Python confidence gate was added — if fewer than 6 chunks are retrieved, confidence is hard-overridden to "low" regardless of the LLM's self-assessment, and the scope of claims is narrowed to what the data directly supports.

**The Guardian addition.** Better grounding reduces the conditions that produce fabrication in the first place. The Guardian Open Platform was added as a journalism source — full-text articles via their free Open Platform API (5,000 calls/day), filtered to the same company set as the other scrapers.

**The honest gap.** These changes reduce fabrication but do not eliminate it. The Critic's hallucination check validates that pain-point keywords from the insight appear in the post; it does not verify that any numeric claim is present in the source chunks. A post that cites "14 sources" correctly but invents "40% of users" as a statistic will still pass the Critic. That gap is currently open.

---

## 4. Why This Isn't an n8n Workflow

A fair question: tools like n8n, Make, and Zapier can chain APIs and run on a schedule. Couldn't this whole thing be a few nodes on a canvas?

The honest answer has two parts.

**What a no-code orchestrator does well, and could do here.** n8n is genuinely good at the plumbing: trigger on a schedule, call an API, transform JSON, call another API, post the result. The *scheduling*, the *Zernio publish call*, and a basic *scrape-then-post* flow could absolutely be built in n8n. It would be foolish to claim otherwise.

**What doesn't fit a node-based model — and is most of InsightPulse's actual value.** The parts that make this system worth building are precisely the parts that resist a fixed node graph:

- **A stateful quality loop.** The Critic scores a post, and on failure the Writer *regenerates* against the critique, sometimes more than once. That is a cyclic, conditional control flow with memory of the previous attempt — not a linear pipeline. Node-based tools are built around directed acyclic flows; an iterative "generate → judge → regenerate until it passes" loop is awkward-to-impractical to express and maintain there.

- **Custom recovery engineering.** The Reddit workaround (§3.1) isn't an API call you can drag onto a canvas. It's a discovery-plus-fetch chain with per-thread fallback logic, ID extraction via regex, graceful handling of the archive's 422 responses, and a deliberate choice about which half of each thread comes from which source. That is bespoke code, not configuration.

- **Architecture-level decisions.** Content-hash de-duplication before embedding, RAG retrieval tuning, the shared `ScrapedPost` interface that lets sources be swapped without touching agents, prompt iteration to suppress fabricated quotes — none of these are nodes. They're engineering decisions that determine whether the output is good or garbage.

So the accurate framing is not "n8n *cannot* do this." It's that **n8n is a workflow orchestrator for predefined, mostly-linear steps, while InsightPulse is a stateful multi-agent system whose value lives in custom agent logic, an iterative quality-control loop, and engineering workarounds that don't map onto a node graph.** If you stripped InsightPulse down to only the parts n8n handles cleanly, you'd be left with the plumbing and would have thrown away the product.

---

## 5. What the Roadblocks Add Up To

Read end to end, the roadblocks share a pattern: the official, documented path was repeatedly unavailable — Reddit's API, LinkedIn's company-page scope, Hugging Face's open download — and each time the project advanced by finding a *terms-compliant alternative path* rather than stalling or faking it. That instinct — workaround-first, but never rule-breaking — is the through-line of the whole build, and it's the part that's hardest to fake and most worth showing.

---

## 6. Honest Limitations

For completeness, because a build journal that only lists wins isn't credible:

- **Post quality is good, not flawless.** The Analyst can still fabricate plausible-sounding claims on thin retrieval. Prompt-level rules (no synthetic quotes, no invented statistics) and the Python confidence gate (fewer than 6 chunks → force low confidence) reduce this substantially but do not eliminate it.
- **The Critic does not validate numeric claims against sources.** Its hallucination check confirms that pain-point keywords from the insight appear in the post; it does not verify that a cited percentage or count appears in any retrieved chunk. A statistic the LLM invented can pass the Critic undetected. Source-grounded numeric validation is an open item.
- **Bluesky and Guardian are credential-gated in the cloud.** Both scrapers are operational, but require their secrets (`BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD`, `GUARDIAN_API_KEY`) to be set in GitHub Actions Secrets for cloud runs to use them. If those secrets are absent, the scrapers skip gracefully — but silently contribute zero posts.
- **The ArcticShift broad-search scraper was removed.** It queried the Arctic Shift `/api/posts/search` endpoint by company keyword and returned HTTP 400 on every call. Reddit content still reaches the pipeline via GoldMiningScraper, which uses Serper to find thread URLs and Arctic Shift's comments endpoint to fetch content from specific thread IDs.
- **The "fully autonomous" claim is true mechanically** — it runs with no human — but a human still reviews and selectively amplifies the best posts to a personal network. That's a feature (product judgment as a final gate), but it should be described accurately, not as "zero human involvement ever."
