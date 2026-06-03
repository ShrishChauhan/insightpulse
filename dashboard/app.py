"""Streamlit monitoring dashboard: run history, critic scores, post log, topic trends."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure project root is on path when launched via `streamlit run dashboard/app.py`
sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402  (after sys.path patch)
from supabase import create_client, Client  # noqa: E402


# ---------------------------------------------------------------------------
# Supabase client (singleton)
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_client() -> Client:
    """Return a cached Supabase client."""
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


# ---------------------------------------------------------------------------
# Data loaders — all return plain Python types (cache_data serialisable)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def load_runs() -> list[dict]:
    """Fetch all run records ordered newest-first."""
    try:
        r = (
            _get_client()
            .table("runs")
            .select("id, created_at, agent_name, status, input_summary, output_summary, tokens_used, duration_ms, error")
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=60)
def load_posts() -> list[dict]:
    """Fetch all post records ordered newest-first."""
    try:
        r = (
            _get_client()
            .table("posts")
            .select("id, topic_id, linkedin_post, pm_brief_path, critic_score, decision, posted_at, engagement_score")
            .order("posted_at", desc=True)
            .limit(500)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=60)
def load_topics() -> list[dict]:
    """Fetch all topic records ordered newest-first."""
    try:
        r = (
            _get_client()
            .table("topics")
            .select("id, topic, company, first_seen, last_covered, cover_count, avg_critic_score")
            .order("first_seen", desc=True)
            .limit(200)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=120)
def load_embedding_stats() -> dict:
    """Return total chunk count, date range, and per-company breakdown."""
    try:
        r = (
            _get_client()
            .table("embeddings")
            .select("created_at, company_tags")
            .execute()
        )
        rows = r.data or []
        if not rows:
            return {"total_vectors": 0, "oldest": None, "newest": None, "by_company": {}}
        dates = sorted(row["created_at"] for row in rows)
        by_company: dict[str, int] = {}
        for row in rows:
            for tag in (row.get("company_tags") or []):
                by_company[tag] = by_company.get(tag, 0) + 1
        return {
            "total_vectors": len(rows),
            "oldest": dates[0],
            "newest": dates[-1],
            "by_company": by_company,
        }
    except Exception:
        return {"total_vectors": 0, "oldest": None, "newest": None, "by_company": {}}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fmt_dt(iso: str | None) -> str:
    """Parse ISO timestamp to human-readable string, or return 'Never'."""
    if not iso:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(iso)


def _topics_by_id(topics: list[dict]) -> dict[str, dict]:
    """Build a lookup map from topic_id to topic record."""
    return {t["id"]: t for t in topics}


def _empty(msg: str = "No data yet.") -> None:
    """Render a muted placeholder message."""
    st.info(msg)


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------

def _tab_live_status(runs: list[dict], posts: list[dict], topics: list[dict]) -> None:
    """Tab 1 — Live Status."""

    # ---- Last run
    st.subheader("Last Run")
    if runs:
        last = runs[0]
        col1, col2, col3 = st.columns(3)
        col1.metric("Agent", last.get("agent_name", "—"))
        col2.metric("Status", last.get("status", "—"))
        col3.metric("Time", _fmt_dt(last.get("created_at")))
        if last.get("output_summary"):
            st.caption(f"Output: {last['output_summary']}")
        if last.get("error"):
            st.error(f"Error: {last['error']}")
    else:
        _empty("No runs recorded yet.")

    st.divider()

    # ---- Quick stats
    st.subheader("Quick Stats")
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    posts_this_week = sum(
        1 for p in posts
        if p.get("posted_at") and
        datetime.fromisoformat(p["posted_at"].replace("Z", "+00:00")) >= week_ago
    )
    total_posts = len(posts)
    scores = [p["critic_score"] for p in posts if p.get("critic_score") is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    c1, c2, c3 = st.columns(3)
    c1.metric("Posts This Week", posts_this_week)
    c2.metric("Total Posts Ever", total_posts)
    c3.metric("Avg Critic Score", avg_score if avg_score is not None else "—")

    st.divider()

    # ---- Upcoming topics (uncovered, newest first, max 5)
    st.subheader("Upcoming Topics")
    uncovered = [
        t for t in topics if not t.get("last_covered")
    ][:5]

    if uncovered:
        df = pd.DataFrame(uncovered)[["topic", "company", "first_seen"]]
        df.columns = ["Topic", "Company", "First Seen"]
        df["First Seen"] = df["First Seen"].apply(_fmt_dt)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        _empty("No upcoming topics queued. All logged topics have been covered, or none exist yet.")


def _tab_post_history(posts: list[dict], topics: list[dict]) -> None:
    """Tab 2 — Post History."""

    topic_map = _topics_by_id(topics)

    # Enrich posts with topic/company name
    enriched = []
    for p in posts:
        t = topic_map.get(p.get("topic_id") or "", {})
        enriched.append({
            "id": p.get("id", ""),
            "Date": _fmt_dt(p.get("posted_at")),
            "Topic": t.get("topic", "—"),
            "Company": t.get("company", "—"),
            "Score": p.get("critic_score"),
            "Decision": p.get("decision", "—"),
            "Engagement": p.get("engagement_score"),
            "_post_text": p.get("linkedin_post", ""),
            "_pdf_path": p.get("pm_brief_path"),
        })

    if not enriched:
        _empty("No posts recorded yet.")
        return

    # ---- Filters
    col_a, col_b, col_c = st.columns(3)
    companies = sorted({e["Company"] for e in enriched if e["Company"] != "—"})
    decisions = sorted({e["Decision"] for e in enriched if e["Decision"] != "—"})

    with col_a:
        company_filter = st.selectbox("Company", ["All"] + companies)
    with col_b:
        decision_filter = st.selectbox("Decision", ["All"] + decisions)
    with col_c:
        days_back = st.selectbox("Date range", [7, 14, 30, 90, 365, 0], format_func=lambda x: f"Last {x} days" if x else "All time")

    filtered = enriched
    if company_filter != "All":
        filtered = [e for e in filtered if e["Company"] == company_filter]
    if decision_filter != "All":
        filtered = [e for e in filtered if e["Decision"] == decision_filter]
    if days_back:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        filtered = [
            e for e in filtered
            if e["Date"] == "Never" or _parse_dt(e["Date"]) >= cutoff
        ]

    if not filtered:
        _empty("No posts match the current filters.")
        return

    # ---- Summary table
    display_cols = ["Date", "Topic", "Company", "Score", "Decision", "Engagement"]
    df = pd.DataFrame(filtered)[display_cols]
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()

    # ---- Row expansion via selectbox
    labels = [
        f"{e['Date']} | {e['Topic']} | {e['Company']} | Score: {e['Score']}"
        for e in filtered
    ]
    selected_idx = st.selectbox("Expand post details", range(len(labels)), format_func=lambda i: labels[i])
    selected = filtered[selected_idx]

    st.markdown("**LinkedIn Post**")
    st.text_area("", value=selected["_post_text"], height=220, disabled=True, label_visibility="collapsed")

    pdf_path = selected.get("_pdf_path")
    if pdf_path and Path(pdf_path).is_file():
        with open(pdf_path, "rb") as fh:
            st.download_button(
                label="Download PM Brief (PDF)",
                data=fh.read(),
                file_name=Path(pdf_path).name,
                mime="application/pdf",
            )
    else:
        st.caption("PM brief PDF not available for this post.")


def _parse_dt(human_str: str) -> datetime:
    """Parse the formatted date string back to a timezone-aware datetime."""
    try:
        return datetime.strptime(human_str, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _tab_topic_explorer(posts: list[dict], topics: list[dict]) -> None:
    """Tab 3 — Topic Explorer."""

    # ---- Embedding stats
    st.subheader("Vector Store Stats")
    emb = load_embedding_stats()
    if emb["total_vectors"] == 0:
        _empty("No embeddings stored yet.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Chunks", emb["total_vectors"])
        c2.metric("Oldest", _fmt_dt(emb.get("oldest")))
        c3.metric("Newest", _fmt_dt(emb.get("newest")))

    st.divider()

    # ---- Company mention count bar chart
    st.subheader("Company Mention Count (Embeddings)")
    by_company = emb.get("by_company") or {}
    if len(by_company) >= 1:
        df_company = pd.DataFrame(
            sorted(by_company.items(), key=lambda x: x[1], reverse=True),
            columns=["Company", "Chunks"],
        ).set_index("Company")
        st.bar_chart(df_company)
    else:
        _empty("Not enough data yet.")

    st.divider()

    # ---- Avg quality score by company (from topics table)
    st.subheader("Avg Quality Score by Company")
    scored_topics = [
        t for t in topics
        if t.get("avg_critic_score") is not None and t.get("company")
    ]
    if len(scored_topics) >= 3:
        company_scores: dict[str, list[float]] = {}
        for t in scored_topics:
            c = t["company"]
            company_scores.setdefault(c, []).append(float(t["avg_critic_score"]))
        avg_by_company = {
            c: round(sum(v) / len(v), 1) for c, v in company_scores.items()
        }
        df_scores = pd.DataFrame(
            sorted(avg_by_company.items(), key=lambda x: x[1], reverse=True),
            columns=["Company", "Avg Score"],
        ).set_index("Company")
        st.bar_chart(df_scores)
    else:
        _empty("Not enough data yet (need at least 3 scored topics across companies).")

    st.divider()

    # ---- Posts per week bar chart
    st.subheader("Posts Per Week")
    posted = [
        p for p in posts if p.get("posted_at")
    ]
    if len(posted) >= 3:
        week_counts: dict[str, int] = {}
        for p in posted:
            try:
                dt = datetime.fromisoformat(p["posted_at"].replace("Z", "+00:00"))
                week_label = dt.strftime("%Y-W%W")
                week_counts[week_label] = week_counts.get(week_label, 0) + 1
            except Exception:
                pass
        if len(week_counts) >= 1:
            df_weeks = pd.DataFrame(
                sorted(week_counts.items()),
                columns=["Week", "Posts"],
            ).set_index("Week")
            st.bar_chart(df_weeks)
        else:
            _empty("Not enough data yet.")
    else:
        _empty("Not enough data yet (need at least 3 published posts).")


def _tab_run_logs(runs: list[dict]) -> None:
    """Tab 4 — Run Logs."""

    if not runs:
        _empty("No run logs yet.")
        return

    # ---- Filters
    col_a, col_b = st.columns(2)
    with col_a:
        errors_only = st.checkbox("Show errors only")
    with col_b:
        agents = sorted({r.get("agent_name", "") for r in runs if r.get("agent_name")})
        agent_filter = st.selectbox("Agent", ["All"] + agents)

    filtered = runs
    if errors_only:
        filtered = [r for r in filtered if r.get("status") in ("failed", "skipped") or r.get("error")]
    if agent_filter != "All":
        filtered = [r for r in filtered if r.get("agent_name") == agent_filter]

    if not filtered:
        _empty("No runs match the current filters.")
        return

    # ---- Run table
    st.subheader("Agent Run Log")
    display = []
    for r in filtered:
        display.append({
            "Timestamp": _fmt_dt(r.get("created_at")),
            "Agent": r.get("agent_name", "—"),
            "Status": r.get("status", "—"),
            "Tokens": r.get("tokens_used"),
            "Duration (ms)": r.get("duration_ms"),
            "Error": r.get("error") or "",
        })
    df = pd.DataFrame(display)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()

    # ---- Token usage over time
    st.subheader("Token Usage Over Time")
    token_rows = [
        r for r in runs if r.get("tokens_used") and r.get("created_at")
    ]
    if len(token_rows) >= 3:
        ts_data = []
        for r in sorted(token_rows, key=lambda x: x["created_at"]):
            try:
                dt = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
                ts_data.append({"Time": dt, "Tokens": int(r["tokens_used"])})
            except Exception:
                pass
        if len(ts_data) >= 3:
            df_tokens = pd.DataFrame(ts_data).set_index("Time")
            st.line_chart(df_tokens)
        else:
            _empty("Not enough data yet.")
    else:
        _empty("Not enough data yet (need at least 3 runs with token counts).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Render the InsightPulse monitoring dashboard."""
    st.set_page_config(
        page_title="InsightPulse",
        page_icon="[IP]",
        layout="wide",
    )
    st.title("InsightPulse")
    st.caption("Autonomous LinkedIn post & PM brief generator — monitoring dashboard")

    # Load all data once per tab render cycle
    runs = load_runs()
    posts = load_posts()
    topics = load_topics()

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Live Status", "Post History", "Topic Explorer", "Run Logs"]
    )

    with tab1:
        _tab_live_status(runs, posts, topics)

    with tab2:
        _tab_post_history(posts, topics)

    with tab3:
        _tab_topic_explorer(posts, topics)

    with tab4:
        _tab_run_logs(runs)


if __name__ == "__main__":
    main()
