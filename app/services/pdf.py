"""Render plain text (a tailored CV or cover letter) into a simple, clean PDF.

Uses fpdf2, which is pure-Python with prebuilt wheels, so it installs without a
compiler. We stick to the built-in Helvetica font (Latin-1) and sanitise the
text to that range, which is fine for English CVs and avoids shipping a font
file. The humanizer pass already strips em dashes, curly quotes and emojis.
"""
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# Map the few non-Latin-1 characters that still slip through to safe equivalents.
_REPLACEMENTS = {
    "‘": "'", "’": "'",          # curly single quotes
    "“": '"', "”": '"',          # curly double quotes
    "–": "-", "—": "-",          # en/em dash
    "…": "...",                        # ellipsis
    " ": " ",                          # non-breaking space
    "•": "-",                          # bullet
}


def _sanitize(text):
    text = text or ""
    for bad, good in _REPLACEMENTS.items():
        text = text.replace(bad, good)
    # Drop anything the core font cannot encode (e.g. leftover emoji).
    return text.encode("latin-1", "ignore").decode("latin-1")


def build_pdf(title, body, contact_line=""):
    """Return PDF bytes for a document with a heading and a block of body text."""
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=18, top=18, right=18)
    pdf.add_page()

    def cell(text, height):
        pdf.multi_cell(0, height, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if title:
        pdf.set_font("Helvetica", "B", 18)
        cell(_sanitize(title), 9)
        pdf.ln(1)

    if contact_line:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(90, 90, 90)
        cell(_sanitize(contact_line), 6)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)

    pdf.set_font("Helvetica", "", 11)
    for line in _sanitize(body).split("\n"):
        if line.strip() == "":
            pdf.ln(4)
        else:
            cell(line, 6)

    out = pdf.output()  # bytes/bytearray in fpdf2 2.8+
    return bytes(out)
