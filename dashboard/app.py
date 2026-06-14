"""InsightPulse monitoring dashboard — redesigned with Inter + design system."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
from supabase import create_client, Client  # noqa: E402


# ---------------------------------------------------------------------------
# Design system CSS
# ---------------------------------------------------------------------------

GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {
    --sidebar-bg: #1a1b1e;
    --sidebar-text: #ececed;
    --sidebar-hover: #2c2e33;
    --content-bg: #ffffff;
    --text-primary: #111827;
    --text-secondary: #6b7280;
    --primary: #E63946;
    --primary-hover: #C1121F;
    --success-bg: #dcfce7;
    --success-text: #166534;
    --warning-bg: #fef9c3;
    --warning-text: #854d0e;
    --error-bg: #fee2e2;
    --error-text: #991b1b;
    --space-4: 1rem;
    --radius-md: 8px;
    --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
    --border-color: #e5e7eb;
}

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif !important;
}

/* Hide Streamlit chrome */
#MainMenu { visibility: hidden; }
header { visibility: hidden; }
footer { visibility: hidden; }
[data-testid="stToolbar"] { display: none; }

/* Sidebar — dark background */
[data-testid="stSidebar"] {
    background-color: var(--sidebar-bg) !important;
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] div {
    color: var(--sidebar-text) !important;
}
[data-testid="stSidebar"] hr {
    border-color: #2c2e33 !important;
    margin: 10px 0 !important;
}

/* Sidebar radio as nav items */
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] {
    gap: 2px !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label {
    display: flex !important;
    align-items: center !important;
    padding: 9px 16px !important;
    border-radius: 6px !important;
    border-left: 3px solid transparent !important;
    font-size: 14px !important;
    font-weight: 500 !important;
    cursor: pointer !important;
    transition: background 0.1s !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: var(--sidebar-hover) !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
    background: rgba(230,57,70,0.12) !important;
    border-left-color: #E63946 !important;
}
/* Hide radio circles */
[data-testid="stSidebar"] [data-testid="stRadio"] input[type="radio"] {
    opacity: 0 !important;
    width: 0 !important;
    height: 0 !important;
    position: absolute !important;
}

/* Main content padding */
.main .block-container {
    padding: 2rem 2rem 2rem 2rem !important;
    max-width: 1100px;
}

/* Metric card */
.metric-card {
    background: #fff;
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-sm);
    border: 1px solid var(--border-color);
    border-left: 4px solid var(--primary);
    padding: 1.1rem 1.4rem;
    height: 100%;
}
.metric-card.green  { border-left-color: #16a34a; }
.metric-card.orange { border-left-color: #ea580c; }
.metric-card.red    { border-left-color: #dc2626; }
.metric-card .mc-label {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 6px;
}
.metric-card .mc-value {
    font-size: 1.75rem;
    font-weight: 700;
    color: var(--text-primary);
    line-height: 1.1;
}
.metric-card .mc-sub {
    font-size: 12px;
    color: var(--text-secondary);
    margin-top: 4px;
}

/* Status badges */
.status-badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 10px;
    font-size: 0.75rem;
    font-weight: 600;
    border-radius: 9999px;
    line-height: 1;
    white-space: nowrap;
}
.badge-success { background: #dcfce7; color: #166534; border: 1px solid rgba(22,101,52,0.2); }
.badge-warning { background: #fef9c3; color: #854d0e; border: 1px solid rgba(133,77,14,0.2); }
.badge-error   { background: #fee2e2; color: #991b1b; border: 1px solid rgba(153,27,27,0.2); }
.badge-neutral { background: #f3f4f6; color: #374151; border: 1px solid rgba(55,65,81,0.2); }

/* Section heading */
.section-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border-color);
    margin-bottom: 12px;
}

/* Empty state */
.empty-state {
    text-align: center;
    padding: 2.5rem 1rem;
    color: var(--text-secondary);
    font-size: 14px;
}

/* Last-run info row */
.run-info-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border: 1px solid var(--border-color);
    border-radius: var(--radius-md);
    background: #fafafa;
    font-size: 13px;
}
.run-info-row .agent-name {
    font-weight: 600;
    color: var(--text-primary);
}
.run-info-row .run-time {
    color: var(--text-secondary);
}
</style>
"""


# ---------------------------------------------------------------------------
# Supabase client (singleton)
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_client() -> Client:
    """Return a cached Supabase client."""
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def load_runs() -> list[dict]:
    """Fetch run records ordered newest-first."""
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
    """Fetch post records ordered newest-first."""
    try:
        r = (
            _get_client()
            .table("posts")
            .select("id, created_at, topic_id, linkedin_post, pm_brief_path, critic_score, decision, posted_at, engagement_score")
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=60)
def load_topics() -> list[dict]:
    """Fetch topic records ordered newest-first."""
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
# Helpers
# ---------------------------------------------------------------------------

def badge(text: str, badge_type: str = "neutral") -> str:
    """Return HTML span with the correct badge class."""
    return f'<span class="status-badge badge-{badge_type}">{text}</span>'


def _badge_type_for_status(status: str) -> str:
    return {"success": "success", "failed": "error", "skipped": "neutral"}.get(status, "neutral")


def _badge_type_for_decision(decision: str) -> str:
    return {"auto_post": "success", "soft_approval": "warning", "auto_reject": "error"}.get(decision, "neutral")


def _time_ago(iso: Optional[str]) -> str:
    """Return human-readable relative time string."""
    if not iso:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        if delta.days >= 7:
            return dt.strftime("%b %d")
        if delta.days >= 1:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        if hours >= 1:
            return f"{hours}h ago"
        minutes = delta.seconds // 60
        return f"{minutes}m ago"
    except Exception:
        return str(iso)[:10]


def _fmt_dt(iso: Optional[str]) -> str:
    """Parse ISO timestamp to readable string."""
    if not iso:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(iso)


def _fmt_duration(ms: Optional[int]) -> str:
    """Format milliseconds as '9.8s'."""
    if ms is None:
        return "—"
    return f"{ms / 1000:.1f}s"


def _parse_dt(iso: Optional[str]) -> datetime:
    """Parse ISO string to aware datetime; return epoch on failure."""
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _next_run_str() -> str:
    """Return the next Mon or Thu 09:00 UTC as a human-readable string."""
    now = datetime.now(timezone.utc)
    candidates = []
    for weekday in (0, 3):  # Monday=0, Thursday=3
        delta = (weekday - now.weekday()) % 7 or 7
        candidates.append((now + timedelta(days=delta)).replace(hour=9, minute=0, second=0, microsecond=0))
    nxt = min(candidates)
    return nxt.strftime("%A, %b %d at 09:00 UTC")


def _metric_card(label: str, value: str, color: str = "", sub: str = "") -> str:
    """Return an HTML metric card string."""
    cls = f"metric-card {color}".strip()
    sub_html = f'<div class="mc-sub">{sub}</div>' if sub else ""
    return f"""
<div class="{cls}">
  <div class="mc-label">{label}</div>
  <div class="mc-value">{value}</div>
  {sub_html}
</div>"""


def _empty(msg: str = "No data yet.", emoji: str = "📭") -> None:
    """Render a centered muted empty-state message."""
    st.markdown(
        f'<div class="empty-state">{emoji} {msg}</div>',
        unsafe_allow_html=True,
    )


def _bar_chart(labels: list, values: list, height: int = 280) -> go.Figure:
    """Return a styled Plotly bar chart with value labels."""
    fig = go.Figure(go.Bar(
        x=labels,
        y=values,
        marker_color="#E63946",
        text=[str(v) for v in values],
        textposition="outside",
        textfont=dict(size=11, color="#374151"),
    ))
    fig.update_layout(
        paper_bgcolor="white",
        plot_bgcolor="white",
        height=height,
        margin=dict(l=0, r=0, t=24, b=0),
        xaxis=dict(showgrid=False, tickfont=dict(size=11, color="#6b7280")),
        yaxis=dict(showgrid=False, showline=False, visible=False),
        font=dict(family="Inter, sans-serif"),
    )
    return fig


def _line_chart(x: list, y: list, height: int = 240) -> go.Figure:
    """Return a styled Plotly line chart with filled area."""
    fig = go.Figure(go.Scatter(
        x=x,
        y=y,
        mode="lines+markers",
        line=dict(color="#E63946", width=2),
        marker=dict(color="#E63946", size=6),
        fill="tozeroy",
        fillcolor="rgba(230, 57, 70, 0.08)",
    ))
    fig.update_layout(
        paper_bgcolor="white",
        plot_bgcolor="white",
        height=height,
        margin=dict(l=0, r=0, t=16, b=0),
        xaxis=dict(showgrid=False, tickfont=dict(size=11, color="#6b7280")),
        yaxis=dict(showgrid=True, gridcolor="#f3f4f6", tickfont=dict(size=11, color="#6b7280")),
        font=dict(family="Inter, sans-serif"),
    )
    return fig


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _render_sidebar(runs: list[dict], posts: list[dict]) -> str:
    """Render sidebar navigation; return selected page name."""
    with st.sidebar:
        st.markdown("""
<div style="padding:1.5rem 1rem 0.75rem;">
  <div style="font-size:22px;font-weight:700;color:#E63946;line-height:1.1;">
    InsightPulse
  </div>
  <div style="font-size:11px;color:#9ca3af;margin-top:4px;font-weight:500;letter-spacing:0.04em;">
    AUTONOMOUS PM INTELLIGENCE
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown("<hr>", unsafe_allow_html=True)

        page = st.radio(
            "nav",
            ["Live Status", "Post History", "Topic Explorer", "Run Logs"],
            label_visibility="collapsed",
        )

        st.markdown("<hr>", unsafe_allow_html=True)

        # Last run status
        if runs:
            last = runs[0]
            last_status = last.get("status", "")
            b_type = _badge_type_for_status(last_status)
            agent = last.get("agent_name", "—")
            when = _time_ago(last.get("created_at"))
            st.markdown(f"""
<div style="padding:0 1rem;font-size:12px;color:#9ca3af;">
  <div style="margin-bottom:4px;font-weight:500;color:#6b7280;">Last run</div>
  <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
    <span style="font-weight:600;color:#ececed;">{agent}</span>
    {badge(last_status, b_type)}
  </div>
  <div style="margin-top:4px;color:#6b7280;">{when}</div>
</div>""", unsafe_allow_html=True)

        # Soft approval badge
        soft_count = sum(1 for p in posts if p.get("decision") == "soft_approval")
        if soft_count > 0:
            st.markdown(f"""
<div style="padding:0.75rem 1rem 0;">
  {badge(f"{soft_count} pending review", "error")}
</div>""", unsafe_allow_html=True)

    return page


# ---------------------------------------------------------------------------
# Tab 1 — Live Status
# ---------------------------------------------------------------------------

def _tab_live_status(runs: list[dict], posts: list[dict], topics: list[dict]) -> None:
    """Render Live Status page."""
    st.markdown('<div class="section-title">Pipeline Overview</div>', unsafe_allow_html=True)

    # Metric cards
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    posts_this_week = sum(
        1 for p in posts
        if _parse_dt(p.get("created_at")) >= week_ago
    )
    total_posts = len(posts)
    scores = [p["critic_score"] for p in posts if p.get("critic_score") is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    score_color = ""
    if avg_score is not None:
        score_color = "green" if avg_score >= 18 else ("orange" if avg_score >= 12 else "red")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(_metric_card("Posts This Week", str(posts_this_week)), unsafe_allow_html=True)
    with c2:
        st.markdown(_metric_card("Total Posts", str(total_posts)), unsafe_allow_html=True)
    with c3:
        val = f"{avg_score}/25" if avg_score is not None else "—"
        st.markdown(_metric_card("Avg Critic Score", val, score_color), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Pipeline health badge
    st.markdown('<div class="section-title">Pipeline Health</div>', unsafe_allow_html=True)
    recent_errors = sum(1 for r in runs[:20] if r.get("status") == "failed")
    if recent_errors == 0:
        health_badge = badge("Healthy", "success")
        health_note = "No errors in recent 20 runs."
    elif recent_errors <= 3:
        health_badge = badge("Caution", "warning")
        health_note = f"{recent_errors} errors in recent 20 runs."
    else:
        health_badge = badge("Needs Attention", "error")
        health_note = f"{recent_errors} errors in recent 20 runs — check Run Logs."
    st.markdown(
        f'{health_badge} <span style="font-size:13px;color:#6b7280;margin-left:8px;">{health_note}</span>',
        unsafe_allow_html=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # Last run detail
    st.markdown('<div class="section-title">Last Run</div>', unsafe_allow_html=True)
    if runs:
        last = runs[0]
        b_type = _badge_type_for_status(last.get("status", ""))
        st.markdown(f"""
<div class="run-info-row">
  <span class="agent-name">{last.get('agent_name', '—')}</span>
  {badge(last.get('status', '—'), b_type)}
  <span class="run-time">{_time_ago(last.get('created_at'))}</span>
  <span style="color:#6b7280;font-size:12px;margin-left:auto;">{last.get('output_summary','')}</span>
</div>""", unsafe_allow_html=True)
        if last.get("error"):
            st.error(f"Error: {last['error']}")
    else:
        _empty("No runs recorded yet.", "📭")

    st.markdown("<br>", unsafe_allow_html=True)

    # Next scheduled run
    st.markdown('<div class="section-title">Next Scheduled Run</div>', unsafe_allow_html=True)
    st.markdown(
        f'<span style="font-size:14px;color:#111827;font-weight:500;">{_next_run_str()}</span>',
        unsafe_allow_html=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # Upcoming topics
    st.markdown('<div class="section-title">Upcoming Topics</div>', unsafe_allow_html=True)
    uncovered = [t for t in topics if not t.get("last_covered")][:5]
    if uncovered:
        rows = []
        for t in uncovered:
            rows.append({
                "Topic": t.get("topic", "—"),
                "Company": t.get("company", "—"),
                "First Seen": _time_ago(t.get("first_seen")),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        _empty("No upcoming topics queued — all logged topics have been covered.", "📋")


# ---------------------------------------------------------------------------
# Tab 2 — Post History
# ---------------------------------------------------------------------------

def _tab_post_history(posts: list[dict], topics: list[dict]) -> None:
    """Render Post History page."""
    topic_map = {t["id"]: t for t in topics}

    enriched = []
    for p in posts:
        t = topic_map.get(p.get("topic_id") or "", {})
        enriched.append({
            "id": p.get("id", ""),
            "Date": _time_ago(p.get("created_at") or p.get("posted_at")),
            "_date_iso": p.get("created_at") or p.get("posted_at") or "",
            "Topic": t.get("topic", "—"),
            "Company": t.get("company", "—"),
            "Score": p.get("critic_score") or 0,
            "Decision": p.get("decision", "—"),
            "Engagement": p.get("engagement_score"),
            "_post_text": p.get("linkedin_post", ""),
            "_pdf_path": p.get("pm_brief_path"),
        })

    if not enriched:
        _empty("No posts recorded yet — run the pipeline to get started.", "📭")
        return

    # Filters
    col_a, col_b, col_c = st.columns(3)
    companies = sorted({e["Company"] for e in enriched if e["Company"] != "—"})
    decisions = sorted({e["Decision"] for e in enriched if e["Decision"] != "—"})
    with col_a:
        company_filter = st.selectbox("Company", ["All"] + companies)
    with col_b:
        decision_filter = st.selectbox("Decision", ["All"] + decisions)
    with col_c:
        days_back = st.selectbox(
            "Date range", [7, 14, 30, 90, 0],
            format_func=lambda x: f"Last {x} days" if x else "All time",
        )

    filtered = enriched
    if company_filter != "All":
        filtered = [e for e in filtered if e["Company"] == company_filter]
    if decision_filter != "All":
        filtered = [e for e in filtered if e["Decision"] == decision_filter]
    if days_back:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        filtered = [e for e in filtered if _parse_dt(e["_date_iso"]) >= cutoff]

    if not filtered:
        _empty("No posts match the current filters.", "🔍")
        return

    # Table with progress bar for Score
    df = pd.DataFrame(filtered)[["Date", "Topic", "Company", "Score", "Decision", "Engagement"]]
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Score /25",
                min_value=0,
                max_value=25,
                format="%d",
                help="Critic score out of 25",
            ),
        },
    )

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="section-title">Post Details</div>', unsafe_allow_html=True)

    # Expand row
    labels = [
        f"{e['Date']} | {e['Topic']} ({e['Company']}) | {e['Score']}/25"
        for e in filtered
    ]
    idx = st.selectbox("Select post", range(len(labels)), format_func=lambda i: labels[i])
    sel = filtered[idx]

    # Decision badge + score
    d_type = _badge_type_for_decision(sel["Decision"])
    st.markdown(
        f'{badge(sel["Decision"], d_type)} '
        f'<span style="font-size:13px;color:#6b7280;margin-left:6px;">Score: {sel["Score"]}/25</span>',
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)
    st.text_area(
        "LinkedIn Post",
        value=sel["_post_text"],
        height=220,
        disabled=True,
    )

    pdf_path = sel.get("_pdf_path")
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


# ---------------------------------------------------------------------------
# Tab 3 — Topic Explorer
# ---------------------------------------------------------------------------

def _tab_topic_explorer(posts: list[dict], topics: list[dict]) -> None:
    """Render Topic Explorer page."""
    emb = load_embedding_stats()

    # Metric cards
    st.markdown('<div class="section-title">Vector Store</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(_metric_card("Total Chunks", str(emb["total_vectors"])), unsafe_allow_html=True)
    with c2:
        oldest = _fmt_dt(emb.get("oldest"))[:10] if emb.get("oldest") else "—"
        st.markdown(_metric_card("Oldest", oldest), unsafe_allow_html=True)
    with c3:
        newest = _fmt_dt(emb.get("newest"))[:10] if emb.get("newest") else "—"
        st.markdown(_metric_card("Newest", newest), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Company mention count
    st.markdown('<div class="section-title">Company Mention Count</div>', unsafe_allow_html=True)
    by_company = emb.get("by_company") or {}
    if by_company:
        sorted_items = sorted(by_company.items(), key=lambda x: x[1], reverse=True)
        labels, values = zip(*sorted_items)
        st.plotly_chart(_bar_chart(list(labels), list(values)), use_container_width=True)
    else:
        _empty("Not enough data for a chart yet.", "📊")

    # Avg quality score by company
    st.markdown('<div class="section-title">Avg Quality Score by Company</div>', unsafe_allow_html=True)
    scored = [t for t in topics if t.get("avg_critic_score") is not None and t.get("company")]
    if len(scored) >= 3:
        company_scores: dict[str, list[float]] = {}
        for t in scored:
            company_scores.setdefault(t["company"], []).append(float(t["avg_critic_score"]))
        avg_map = {c: round(sum(v) / len(v), 1) for c, v in company_scores.items()}
        sorted_avg = sorted(avg_map.items(), key=lambda x: x[1], reverse=True)
        lbl, val = zip(*sorted_avg)
        st.plotly_chart(_bar_chart(list(lbl), list(val)), use_container_width=True)
    else:
        _empty("Not enough data for a chart yet (need 3+ scored topics).", "📊")

    # Posts per week
    st.markdown('<div class="section-title">Posts Per Week</div>', unsafe_allow_html=True)
    dated_posts = [p for p in posts if p.get("created_at") or p.get("posted_at")]
    if len(dated_posts) >= 3:
        week_counts: dict[str, int] = {}
        for p in dated_posts:
            iso = p.get("created_at") or p.get("posted_at") or ""
            try:
                dt = _parse_dt(iso)
                week_label = dt.strftime("%Y-W%W")
                week_counts[week_label] = week_counts.get(week_label, 0) + 1
            except Exception:
                pass
        if len(week_counts) >= 1:
            sorted_weeks = sorted(week_counts.items())
            weeks, counts = zip(*sorted_weeks)
            st.plotly_chart(_line_chart(list(weeks), list(counts)), use_container_width=True)
        else:
            _empty("Not enough data for a chart yet.", "📊")
    else:
        _empty("Not enough data for a chart yet (need 3+ data points).", "📊")


# ---------------------------------------------------------------------------
# Tab 4 — Run Logs
# ---------------------------------------------------------------------------

def _tab_run_logs(runs: list[dict]) -> None:
    """Render Run Logs page."""
    if not runs:
        _empty("No run logs yet — start the pipeline to see data here.", "📭")
        return

    # Summary row
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    runs_today = sum(1 for r in runs if _parse_dt(r.get("created_at")) >= today_start)
    errors_today = sum(
        1 for r in runs
        if r.get("status") == "failed" and _parse_dt(r.get("created_at")) >= today_start
    )
    agents_active = len({r.get("agent_name") for r in runs if r.get("status") == "success"})
    st.markdown(
        f'<p style="font-size:13px;color:#6b7280;margin-bottom:16px;">'
        f'<strong style="color:#111827;">{runs_today}</strong> runs today &nbsp;·&nbsp; '
        f'<strong style="color:#991b1b;">{errors_today}</strong> errors &nbsp;·&nbsp; '
        f'<strong style="color:#111827;">{agents_active}</strong> agents active</p>',
        unsafe_allow_html=True,
    )

    # Filters
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
        _empty("No runs match the current filters.", "🔍")
        return

    # Table
    st.markdown('<div class="section-title">Agent Run Log</div>', unsafe_allow_html=True)
    display = []
    for r in filtered:
        display.append({
            "Timestamp": _time_ago(r.get("created_at")),
            "Agent": r.get("agent_name", "—"),
            "Status": r.get("status", "—"),
            "Duration": _fmt_duration(r.get("duration_ms")),
            "Error": r.get("error") or "",
        })
    st.dataframe(pd.DataFrame(display), use_container_width=True, hide_index=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Token usage line chart
    st.markdown('<div class="section-title">Token Usage Over Time</div>', unsafe_allow_html=True)
    token_rows = [r for r in runs if r.get("tokens_used") and r.get("created_at")]
    if len(token_rows) >= 3:
        sorted_rows = sorted(token_rows, key=lambda x: x["created_at"])
        xs, ys = [], []
        for r in sorted_rows:
            try:
                dt = _parse_dt(r["created_at"])
                xs.append(dt.strftime("%m-%d %H:%M"))
                ys.append(int(r["tokens_used"]))
            except Exception:
                pass
        if len(xs) >= 3:
            st.plotly_chart(_line_chart(xs, ys), use_container_width=True)
        else:
            _empty("Not enough data yet.", "📊")
    else:
        _empty("Not enough data yet (need 3+ runs with token counts).", "📊")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Render the InsightPulse monitoring dashboard."""
    st.set_page_config(
        page_title="InsightPulse",
        page_icon="IP",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

    # Load data before sidebar (sidebar uses last-run status)
    runs = load_runs()
    posts = load_posts()
    topics = load_topics()

    page = _render_sidebar(runs, posts)

    if page == "Live Status":
        _tab_live_status(runs, posts, topics)
    elif page == "Post History":
        _tab_post_history(posts, topics)
    elif page == "Topic Explorer":
        _tab_topic_explorer(posts, topics)
    elif page == "Run Logs":
        _tab_run_logs(runs)


if __name__ == "__main__":
    main()
