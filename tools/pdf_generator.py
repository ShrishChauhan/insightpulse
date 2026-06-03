"""Generates formatted PM brief PDFs using ReportLab."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

if TYPE_CHECKING:
    from agents.writer import PMBrief

# ---------------------------------------------------------------------------
# Brand constants
# ---------------------------------------------------------------------------

_BRAND_BLUE = colors.HexColor("#1B4F72")
_BRAND_LIGHT = colors.HexColor("#D6EAF8")
_GREY = colors.HexColor("#566573")
_ALT_ROW = colors.HexColor("#F4F6F7")
_RULE_COLOR = colors.HexColor("#BDC3C7")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(value: object, fallback: str = "Not available") -> str:
    """Return str(value) stripped, or fallback if None/empty."""
    if value is None:
        return fallback
    s = str(value).strip()
    return s if s else fallback


def _build_styles() -> dict[str, ParagraphStyle]:
    """Return all custom paragraph styles keyed by name."""
    base = getSampleStyleSheet()
    return {
        "brand_title": ParagraphStyle(
            "BrandTitle",
            parent=base["Heading1"],
            fontSize=22,
            textColor=_BRAND_BLUE,
            spaceAfter=2,
            alignment=TA_LEFT,
        ),
        "caption": ParagraphStyle(
            "Caption",
            parent=base["Normal"],
            fontSize=9,
            textColor=_GREY,
            spaceAfter=4,
        ),
        "doc_title": ParagraphStyle(
            "DocTitle",
            parent=base["Heading1"],
            fontSize=15,
            textColor=colors.black,
            spaceBefore=6,
            spaceAfter=10,
        ),
        "section": ParagraphStyle(
            "Section",
            parent=base["Heading2"],
            fontSize=12,
            textColor=_BRAND_BLUE,
            spaceBefore=14,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontSize=10,
            leading=15,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=base["Normal"],
            fontSize=10,
            leading=15,
            leftIndent=14,
            bulletIndent=4,
            spaceAfter=3,
        ),
        "table_label": ParagraphStyle(
            "TableLabel",
            parent=base["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=_BRAND_BLUE,
            leading=12,
        ),
        "table_value": ParagraphStyle(
            "TableValue",
            parent=base["Normal"],
            fontSize=9,
            leading=12,
        ),
    }


def _section(label: str, st: dict) -> list:
    """Return flowables for a section heading + rule."""
    return [
        Paragraph(label, st["section"]),
        HRFlowable(width="100%", thickness=0.5, color=_BRAND_LIGHT),
        Spacer(1, 4),
    ]


def _feature_table(feature: dict, st: dict) -> list:
    """Build a 2-column table for the proposed_feature dict."""
    fields = [
        ("Feature Name", feature.get("name")),
        ("Description", feature.get("description")),
        ("User Story", feature.get("user_story")),
        ("Effort Estimate", feature.get("effort_estimate")),
        ("Impact Estimate", feature.get("impact_estimate")),
    ]
    rows = [
        [
            Paragraph(label, st["table_label"]),
            Paragraph(_safe(value), st["table_value"]),
        ]
        for label, value in fields
    ]
    col_widths = [1.6 * inch, 4.85 * inch]
    t = Table(rows, colWidths=col_widths, repeatRows=0)
    row_count = len(rows)
    style = TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), _BRAND_LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.4, _RULE_COLOR),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ])
    # Alternate row tint on value column only
    for i in range(1, row_count, 2):
        style.add("BACKGROUND", (1, i), (1, i), _ALT_ROW)
    t.setStyle(style)
    return [t]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_brief(pm_brief: "PMBrief", output_path: str) -> str:
    """Generate a PDF PM brief from a PMBrief dict; return the saved file path.

    All None/empty fields are substituted with 'Not available' — never crashes.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=LETTER,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=_safe(pm_brief.get("title"), "PM Brief"),
        author="InsightPulse",
    )

    st = _build_styles()
    story = []

    # ------------------------------------------------------------------ Header
    story.append(Paragraph("InsightPulse", st["brand_title"]))
    story.append(Paragraph("Product Manager Brief", st["caption"]))
    story.append(HRFlowable(width="100%", thickness=2, color=_BRAND_BLUE))
    story.append(Spacer(1, 6))
    story.append(Paragraph(_safe(pm_brief.get("title")), st["doc_title"]))

    # ---------------------------------------------------- Executive Summary
    story.extend(_section("Executive Summary", st))
    story.append(Paragraph(_safe(pm_brief.get("executive_summary")), st["body"]))

    # ------------------------------------------------------ Problem Statement
    story.extend(_section("Problem Statement", st))
    story.append(Paragraph(_safe(pm_brief.get("problem_statement")), st["body"]))

    # --------------------------------------------------------- User Evidence
    story.extend(_section("User Evidence", st))
    evidence = pm_brief.get("user_evidence") or []
    if evidence:
        for item in evidence:
            if isinstance(item, dict):
                quote = _safe(
                    item.get("quote") or item.get("title") or item.get("description")
                )
                source = item.get("source") or item.get("url") or ""
                line = f"&#8226; <i>{quote}</i>"
                if source:
                    line += f'  <font color="#566573" size="8">({source})</font>'
                story.append(Paragraph(line, st["bullet"]))
            else:
                story.append(Paragraph(f"&#8226; {_safe(item)}", st["bullet"]))
    else:
        story.append(Paragraph("Not available", st["body"]))

    # ------------------------------------------------------ Proposed Feature
    story.extend(_section("Proposed Feature", st))
    feature = pm_brief.get("proposed_feature")
    if feature and isinstance(feature, dict):
        story.extend(_feature_table(feature, st))
    else:
        story.append(Paragraph("Not available", st["body"]))

    # ----------------------------------------------------- Success Metrics
    story.extend(_section("Success Metrics", st))
    metrics = pm_brief.get("success_metrics") or []
    if metrics:
        for m in metrics:
            story.append(Paragraph(f"&#8226; {_safe(m)}", st["bullet"]))
    else:
        story.append(Paragraph("Not available", st["body"]))

    # ----------------------------------------------------------------- Risks
    story.extend(_section("Risks", st))
    risks = pm_brief.get("risks") or []
    if risks:
        for r in risks:
            story.append(Paragraph(f"&#8226; {_safe(r)}", st["bullet"]))
    else:
        story.append(Paragraph("Not available", st["body"]))

    # ------------------------------------------------- Competitive Context
    story.extend(_section("Competitive Context", st))
    story.append(Paragraph(_safe(pm_brief.get("competitive_context")), st["body"]))

    doc.build(story)
    return output_path
