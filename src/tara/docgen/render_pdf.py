"""PDF renderer using fpdf2 (LGPL-3.0; used as an unmodified library).

Unicode text needs a TrueType font file. We use `DocStyle.pdf_font_*` if set, else try common system fonts, else
fall back to the built-in Helvetica (Latin-1 only): characters outside Latin-1 are replaced with '?' and reported in
`RenderResult.warnings`, never silently dropped without a note.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .model import Block, DocStyle, Document, inline_runs

_CANDIDATES = (
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf", "/Library/Fonts/Arial Italic.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
)


@dataclass
class RenderResult:
    data: bytes
    warnings: list[str] = field(default_factory=list)


def _find_fonts(style: DocStyle) -> tuple[str, str, str] | None:
    if style.pdf_font_regular and os.path.isfile(style.pdf_font_regular):
        reg = style.pdf_font_regular
        return reg, style.pdf_font_bold if os.path.isfile(style.pdf_font_bold) else reg, \
            style.pdf_font_italic if os.path.isfile(style.pdf_font_italic) else reg
    for reg, bold, ital in _CANDIDATES:
        if os.path.isfile(reg):
            return reg, bold if os.path.isfile(bold) else reg, ital if os.path.isfile(ital) else reg
    return None


def _latin1(text: str, warnings: list[str]) -> str:
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        if "non-Latin-1 characters were replaced (no Unicode font available)" not in warnings:
            warnings.append("non-Latin-1 characters were replaced (no Unicode font available)")
        return text.encode("latin-1", "replace").decode("latin-1")


def render_pdf(doc: Document, style: DocStyle | None = None) -> RenderResult:
    from fpdf import FPDF
    style = style or DocStyle()
    warnings: list[str] = []
    fonts = _find_fonts(style)

    class Pdf(FPDF):
        def footer(self) -> None:  # type: ignore[override]
            self.set_y(-14)
            self.set_font(family, size=8)
            self.set_text_color(110, 110, 110)
            label = (doc.footer + "   |   " if doc.footer else "") + f"Page {self.page_no()}"
            self.cell(0, 8, tx(label), align="C")

    pdf = Pdf(format=style.page if style.page == "A4" else "Letter", unit="mm")
    pdf.set_margins(style.margin_cm * 10, style.margin_cm * 10, style.margin_cm * 10)
    pdf.set_auto_page_break(True, margin=style.margin_cm * 10 + 6)
    if fonts is not None:
        family = "TaraSans"
        pdf.add_font(family, "", fonts[0])
        pdf.add_font(family, "B", fonts[1])
        pdf.add_font(family, "I", fonts[2])
        tx = lambda s: s  # noqa: E731
    else:
        family = "Helvetica"
        tx = lambda s: _latin1(s, warnings)  # noqa: E731
        warnings.append("no Unicode font found; using Helvetica (set pdf_font_regular for full Unicode)")
    pdf.set_title(tx(doc.title))
    pdf.set_author(tx(doc.author))
    pdf.add_page()
    body = style.size_body
    accent = style.accent_rgb

    def rich(text: str, size: float, line: float) -> None:
        for chunk, bold, italic in inline_runs(tx(text)):
            pdf.set_font(family, ("B" if bold else "") + ("I" if italic else ""), size)
            pdf.write(line, chunk)
        pdf.ln(line)

    pdf.set_font(family, "B", style.size_h1 + 4)
    pdf.set_text_color(*accent)
    pdf.multi_cell(0, 10, tx(doc.title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    if doc.subtitle:
        pdf.set_font(family, "I", body)
        pdf.multi_cell(0, 6, tx(doc.subtitle), new_x="LMARGIN", new_y="NEXT")
    if doc.created:
        pdf.set_font(family, "", body - 2)
        pdf.multi_cell(0, 5, tx(f"Created: {doc.created}"), new_x="LMARGIN", new_y="NEXT")
    if doc.confidential:
        pdf.set_font(family, "B", body)
        pdf.multi_cell(0, 6, "CONFIDENTIAL", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    for b in doc.blocks:
        _block(pdf, b, style, family, tx, rich, accent)
    return RenderResult(bytes(pdf.output()), warnings)


def _block(pdf: Any, b: Block, style: DocStyle, family: str, tx: Any, rich: Any, accent: tuple[int, int, int]) -> None:
    body = style.size_body
    if b.kind == "heading":
        size = {1: style.size_h2 + 2, 2: style.size_h2, 3: style.size_h3}[b.level]
        pdf.ln(2)
        pdf.set_font(family, "B", size)
        pdf.set_text_color(*accent)
        pdf.multi_cell(0, size * 0.5, tx(b.text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)
    elif b.kind == "paragraph":
        rich(b.text, body, body * 0.5)
        pdf.ln(2)
    elif b.kind in ("bullets", "numbered"):
        for n, item in enumerate(b.items, 1):
            pdf.set_font(family, "", body)
            marker = "• " if b.kind == "bullets" else f"{n}. "
            pdf.set_x(pdf.l_margin + 4)
            pdf.write(body * 0.5, tx(marker) if family != "Helvetica" else ("- " if b.kind == "bullets" else f"{n}. "))
            rich(item, body, body * 0.5)
        pdf.ln(2)
    elif b.kind == "code":
        pdf.set_font("Courier", "", body - 1)
        pdf.multi_cell(0, 5, b.text.encode("latin-1", "replace").decode("latin-1"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    elif b.kind == "rule":
        y = pdf.get_y() + 2
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(5)
    elif b.kind == "table":
        pdf.set_font(family, "", body - 1)
        with pdf.table(first_row_as_headings=True, text_align="LEFT", line_height=body * 0.5) as table:
            hdr = table.row()
            for h in b.headers:
                hdr.cell(tx(h))
            for row in b.rows:
                r = table.row()
                for cell in row:
                    r.cell(tx(cell))
        pdf.ln(3)
