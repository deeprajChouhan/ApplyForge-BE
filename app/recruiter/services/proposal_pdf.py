"""
Role proposal → PDF export.

Renders the client-safe view of a role (title, band, skills, description,
market benchmark, signature area) as a single-page (mostly) A4 PDF the agency
can email as a proposal. Reuses reportlab which is already in the deps.

Kept deliberately monochrome + boring so it prints well and reads like a
proposal doc rather than a screenshot.
"""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.recruiter.models import Agency, Client, Role


_PRIMARY = colors.HexColor("#7c3aed")
_INK = colors.HexColor("#111827")
_MUTED = colors.HexColor("#6b7280")
_ACCENT_BG = colors.HexColor("#f3f4f6")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    out: dict[str, ParagraphStyle] = {}
    out["h1"] = ParagraphStyle(
        "h1",
        parent=base["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=_INK,
        spaceAfter=6,
    )
    out["h2"] = ParagraphStyle(
        "h2",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=_PRIMARY,
        spaceBefore=14,
        spaceAfter=6,
    )
    out["label"] = ParagraphStyle(
        "label",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=_MUTED,
        spaceAfter=2,
    )
    out["body"] = ParagraphStyle(
        "body",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        textColor=_INK,
    )
    out["small"] = ParagraphStyle(
        "small",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=_MUTED,
    )
    out["chip"] = ParagraphStyle(
        "chip",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=_INK,
    )
    return out


def _skills_line(skills: list[str], style: ParagraphStyle) -> Paragraph:
    if not skills:
        return Paragraph("<i>None specified</i>", style)
    return Paragraph(" · ".join(skills), style)


def _market_table(role: Role, styles: dict[str, ParagraphStyle]) -> Table | None:
    m = role.market_snapshot or None
    if not isinstance(m, dict):
        return None
    cur = m.get("currency") or role.budget_currency or "USD"

    def money(v):
        if v is None:
            return "—"
        try:
            return f"{cur} {int(v):,}"
        except (TypeError, ValueError):
            return "—"

    data = [
        [
            Paragraph("<b>p25</b>", styles["label"]),
            Paragraph("<b>Median (p50)</b>", styles["label"]),
            Paragraph("<b>p75</b>", styles["label"]),
            Paragraph("<b>Sample</b>", styles["label"]),
        ],
        [
            Paragraph(money(m.get("salary_p25")), styles["body"]),
            Paragraph(f"<b>{money(m.get('salary_p50'))}</b>", styles["body"]),
            Paragraph(money(m.get("salary_p75")), styles["body"]),
            Paragraph(str(m.get("sample_size") or 0), styles["body"]),
        ],
    ]
    tbl = Table(data, colWidths=[4 * cm, 5 * cm, 4 * cm, 3 * cm])
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _ACCENT_BG),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return tbl


def render_role_proposal_pdf(
    role: Role, agency: Agency, client: Client | None = None
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"{agency.name} — {role.title}",
        author=agency.name,
        subject="Role proposal",
    )
    styles = _styles()
    story = []

    header = Table(
        [[Paragraph(f"<b>{agency.name}</b>", styles["body"]),
          Paragraph(datetime.utcnow().strftime("%d %b %Y"), styles["small"])]],
        colWidths=[11 * cm, 6 * cm],
    )
    header.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(header)
    story.append(HRFlowable(width="100%", thickness=0.7, color=colors.HexColor("#e5e7eb")))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Role proposal", styles["small"]))
    story.append(Paragraph(role.title or "Untitled role", styles["h1"]))

    meta_bits = []
    if role.seniority:
        meta_bits.append(role.seniority)
    if role.employment_type:
        meta_bits.append(
            role.employment_type.value.replace("_", " ")
            if hasattr(role.employment_type, "value")
            else str(role.employment_type).replace("_", " ")
        )
    if role.location:
        meta_bits.append(role.location)
    if role.min_years_experience is not None:
        meta_bits.append(f"{role.min_years_experience:g}+ yrs")
    if role.salary_min or role.salary_max:
        band_currency = role.budget_currency or "USD"
        meta_bits.append(
            f"{band_currency} {(role.salary_min or 0):,}–{(role.salary_max or 0):,}"
        )
    if meta_bits:
        story.append(Paragraph(" · ".join(meta_bits), styles["small"]))
    story.append(Spacer(1, 8))

    if client is not None:
        story.append(Paragraph("Prepared for", styles["label"]))
        story.append(Paragraph(client.name, styles["body"]))
        story.append(Spacer(1, 4))

    if role.description:
        story.append(Paragraph("About the role", styles["h2"]))
        story.append(Paragraph(role.description.replace("\n", "<br/>"), styles["body"]))

    story.append(Paragraph("Must-have skills", styles["h2"]))
    story.append(_skills_line(role.required_skills or [], styles["body"]))
    story.append(Paragraph("Nice to have", styles["h2"]))
    story.append(_skills_line(role.preferred_skills or [], styles["body"]))

    tbl = _market_table(role, styles)
    if tbl is not None:
        story.append(Paragraph("Market benchmark", styles["h2"]))
        story.append(tbl)
        srcs = (role.market_snapshot or {}).get("sources") or []
        if srcs:
            story.append(Spacer(1, 4))
            story.append(Paragraph(f"Sourced from {', '.join(srcs)}", styles["small"]))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.7, color=colors.HexColor("#e5e7eb")))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Signatures", styles["h2"]))
    sig = Table(
        [
            [
                Paragraph("<b>For the agency</b><br/>Name<br/>Date<br/><br/><br/>_______________________", styles["small"]),
                Paragraph("<b>For the client</b><br/>Name<br/>Date<br/><br/><br/>_______________________", styles["small"]),
            ]
        ],
        colWidths=[8.5 * cm, 8.5 * cm],
    )
    sig.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(sig)

    story.append(Spacer(1, 20))
    story.append(
        Paragraph(
            f"Generated by {agency.name} · ApplyForge Recruiter · {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}",
            styles["small"],
        )
    )

    doc.build(story)
    return buf.getvalue()
