"""PDF version of the VIE report.

The markdown report is the single source of truth: this module renders that
same text to PDF rather than building a second layout from the analysis
objects, so the two formats can never disagree. It understands only the
markdown the report itself uses (headings, bold, italics, bullets, quotes and
two-column tables).

Uses fpdf2, which is pure Python, so PDF output needs no system tools.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, List

from src.output.exceptions import ReportWriteError
from src.output.report import render_report, report_filename
from src.pipeline import PipelineOutput

# The built-in PDF fonts only cover Latin-1. Model text often contains
# typographic characters outside it, so map the common ones to plain
# equivalents; anything else becomes "?" rather than failing the report.
_CHAR_REPLACEMENTS = {
    "—": "-", "–": "-", "‒": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", "≈": "~", "≥": ">=", "≤": "<=",
    "→": "->", "•": "-", " ": " ", " ": " ", " ": " ",
}

_TEXT = (33, 37, 41)
_MUTED = (108, 117, 125)
_ACCENT = (31, 78, 121)
_RULE = (206, 212, 218)
_SHADE = (241, 243, 245)


def _latin1(text: str) -> str:
    """Make text safe for the built-in PDF fonts.

    Args:
        text (str): Any text.

    Returns:
        str: Latin-1 encodable text.
    """
    for char, replacement in _CHAR_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    return text.encode("latin-1", "replace").decode("latin-1")


def _to_fpdf_markup(text: str) -> str:
    """Convert report markdown emphasis to fpdf2's markup.

    fpdf2 uses **bold** and __italic__, so single-asterisk italics are
    rewritten. Literal double underscores in the text are left alone only if
    they are not paired, which the report never produces.

    Args:
        text (str): One line of report markdown.

    Returns:
        str: The line in fpdf2 markup.
    """
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"__\1__", text)
    return _latin1(text)


def _make_pdf() -> Any:
    """Create an empty A4 document.

    Returns:
        Any: An fpdf2 ``FPDF`` instance.

    Raises:
        ReportWriteError: If fpdf2 is not installed.
    """
    try:
        from fpdf import FPDF
    except ImportError as exc:
        raise ReportWriteError(
            "PDF output needs the fpdf2 package. Run: pip install -r requirements.txt"
        ) from exc

    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_text_color(*_TEXT)
    return pdf


def _render_table(pdf: Any, rows: List[List[str]]) -> None:
    """Draw a simple two-column table with a shaded header row.

    Args:
        pdf (Any): The fpdf2 document.
        rows (List[List[str]]): Header row first, then body rows.
    """
    pdf.set_draw_color(*_RULE)
    pdf.set_font("Helvetica", size=9.5)
    with pdf.table(
        col_widths=(60, 40),
        line_height=6,
        headings_style=_heading_style(),
        borders_layout="HORIZONTAL_LINES",
        text_align="LEFT",
    ) as table:
        for row in rows:
            cells = table.row()
            for value in row:
                cells.cell(_latin1(value))
    pdf.ln(3)


def _heading_style() -> Any:
    """Build the table header style.

    Returns:
        Any: An fpdf2 ``FontFace``.
    """
    from fpdf.fonts import FontFace

    return FontFace(emphasis="BOLD", fill_color=_SHADE)


def render_pdf(markdown: str) -> bytes:
    """Render report markdown to PDF bytes.

    Args:
        markdown (str): Output of ``render_report``.

    Returns:
        bytes: The PDF file content.

    Raises:
        ReportWriteError: If fpdf2 is not installed.
    """
    pdf = _make_pdf()
    width = pdf.epw
    table_rows: List[List[str]] = []

    def paragraph(text: str, size: float = 10, style: str = "", indent: float = 0,
                  height: float = 5.5, markup: bool = True, fill: bool = False) -> None:
        """Write one left-aligned paragraph and move to the next line.

        Args:
            text (str): Line of report markdown.
            size (float): Font size in points.
            style (str): fpdf2 font style, "" or "B" or "I".
            indent (float): Left indent in millimetres.
            height (float): Line height in millimetres.
            markup (bool): Whether to interpret bold and italic markup.
            fill (bool): Whether to shade the paragraph background.
        """
        pdf.set_font("Helvetica", style, size)
        pdf.set_x(pdf.l_margin + indent)
        body = _to_fpdf_markup(text) if markup else _latin1(text)
        pdf.multi_cell(width - indent, height, body, markdown=markup, fill=fill,
                       align="L", new_x="LMARGIN", new_y="NEXT")

    lines = markdown.splitlines() + [""]
    for line in lines:
        stripped = line.strip()

        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not all(set(c) <= set("-:") for c in cells):
                table_rows.append(cells)
            continue
        if table_rows:
            _render_table(pdf, table_rows)
            table_rows = []

        if not stripped:
            pdf.ln(2)
        elif stripped.startswith("# "):
            pdf.set_text_color(*_ACCENT)
            paragraph(stripped[2:], size=17, style="B", height=8, markup=False)
            pdf.set_text_color(*_TEXT)
        elif stripped.startswith("## "):
            pdf.ln(3)
            pdf.set_text_color(*_ACCENT)
            paragraph(stripped[3:], size=12.5, style="B", height=7, markup=False)
            pdf.set_draw_color(*_RULE)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
            pdf.ln(2)
            pdf.set_text_color(*_TEXT)
        elif stripped.startswith("- "):
            paragraph("- " + stripped[2:], indent=4)
        elif stripped.startswith(">"):
            text = stripped.lstrip(">").strip()
            pdf.set_fill_color(253, 243, 224)
            if text.startswith("- "):
                paragraph("   - " + text[2:], fill=True)
            elif text:
                paragraph(text, fill=True)
        elif stripped.startswith("*") and stripped.endswith("*") and not stripped.startswith("**"):
            pdf.set_text_color(*_MUTED)
            paragraph(stripped.strip("*"), size=9, style="I", height=5, markup=False)
            pdf.set_text_color(*_TEXT)
        else:
            paragraph(stripped)

    return bytes(pdf.output())


def write_pdf_report(
    output: PipelineOutput, output_dir: Path, generated_on: date | None = None
) -> Path:
    """Render a run as PDF and write it next to the markdown report.

    Args:
        output (PipelineOutput): The pipeline run to report on.
        output_dir (Path): Directory to write into. Created if missing.
        generated_on (date | None): Report date. Defaults to today.

    Returns:
        Path: The written PDF file.

    Raises:
        ReportWriteError: If fpdf2 is missing or the file cannot be written.
    """
    generated_on = generated_on or date.today()
    content = render_pdf(render_report(output, generated_on))
    path = Path(output_dir) / report_filename(output.result.ticker, generated_on).replace(".md", ".pdf")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError as exc:
        raise ReportWriteError(f"Could not write the PDF report to {path}: {exc}") from exc
    return path
