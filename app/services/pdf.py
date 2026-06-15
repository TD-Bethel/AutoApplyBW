"""Render a tailored CV (plain text) into a clean, structured PDF.

Layout mirrors the project's gold-standard CV: a centred name + contact header,
ALL-CAPS section headings underlined by a rule, each entry showing the employer
in bold on the left with the date right-aligned, an italic role/location line,
and hanging-indent bullets. The whole document is auto-fitted to a maximum of
two A4 pages (the font scales down a step at a time if content would overflow).

Uses fpdf2 (pure-Python, prebuilt wheels) with the built-in Helvetica font, so
no font files ship and no compiler is needed. Text is sanitised to Latin-1.
"""
import re

from fpdf import FPDF
from fpdf.enums import XPos, YPos

_REPLACEMENTS = {
    "‘": "'", "’": "'",      # curly single quotes
    "“": '"', "”": '"',      # curly double quotes
    "–": "-", "—": "-",      # en/em dash
    "…": "...",                    # ellipsis
    " ": " ",                      # non-breaking space
    "•": "-",                      # bullet
}

MAX_PAGES = 2
_SCALES = (1.0, 0.94, 0.88, 0.83, 0.78)   # tried in order until it fits MAX_PAGES


def _sanitize(text):
    text = text or ""
    for bad, good in _REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "ignore").decode("latin-1")


def _is_heading(line):
    """Short ALL-CAPS line with letters = a section heading (SUMMARY, EXPERIENCE)."""
    return 2 < len(line) <= 60 and line == line.upper() and any(c.isalpha() for c in line)


def _is_bullet(line):
    return bool(re.match(r"^\s*[-*]\s+", line))


# Symbol/PUA bullet glyphs CVs pick up from Word or PDF extraction (Wingdings
# dot, etc.). Mapped to '- ' before sanitising so they aren't silently dropped.
_SYMBOL_BULLETS = "•▪‣◦·●"
_WRAP_THRESHOLD = 55   # a source line this long was almost certainly auto-wrapped


def _reflow(text):
    """Rejoin hard-wrapped lines into real paragraphs and normalise symbol
    bullets to '- '. CV text extracted from Word/PDF often breaks every line
    mid-sentence; without reflow, justification has nothing to stretch and each
    fragment renders ragged. A line is treated as a continuation of the one
    above only when that line was long enough to have wrapped — short, complete
    lines (a role, a date, 'CGPA: 3.4') are left alone."""
    text = re.sub("(?m)^[ 	]*(?:[" + re.escape(_SYMBOL_BULLETS) + "]|[-])[ 	]*", "- ", text)
    out, prev_len = [], 0
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            out.append("")
            prev_len = 0
            continue
        structural = _is_heading(s) or _is_bullet(s) or (" | " in s)
        if (not structural and out and out[-1] and prev_len >= _WRAP_THRESHOLD
                and not _is_heading(out[-1])):
            out[-1] = out[-1].rstrip() + " " + s     # continuation of prose/bullet
        else:
            out.append(s)
        prev_len = len(s)
    # collapse runs of blank lines
    cleaned = []
    for u in out:
        if u == "" and (not cleaned or cleaned[-1] == ""):
            continue
        cleaned.append(u)
    return "\n".join(cleaned).strip()


def _truncate_to_width(pdf, text, max_w):
    """Trim text so it fits max_w (mm); only ever needed for very long names."""
    if pdf.get_string_width(text) <= max_w:
        return text
    while text and pdf.get_string_width(text + "...") > max_w:
        text = text[:-1]
    return text + "..."


def _split_header(body_lines, fallback_name, fallback_contact):
    """Pull the name + contact off the top so they aren't rendered twice. The
    first non-blank line is always the name (even though it's ALL-CAPS like a
    heading); the body starts at the first real section heading after it."""
    name, contact = fallback_name, fallback_contact
    non_blank = [(i, ln.strip()) for i, ln in enumerate(body_lines) if ln.strip()]
    if not non_blank:
        return name, contact, len(body_lines)

    name = non_blank[0][1]
    if len(non_blank) > 1:
        cand = non_blank[1][1]
        if "@" in cand or "|" in cand or re.search(r"\d{6,}", cand):
            contact = cand

    start = len(body_lines)
    for i in range(non_blank[0][0] + 1, len(body_lines)):
        if _is_heading(body_lines[i].strip()):
            start = i
            break
    return name, contact, start


def _render(name, contact, body, scale):
    """Build the whole PDF at a given font scale; return the FPDF object."""
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.set_margins(left=18, top=16, right=18)
    pdf.add_page()
    content_w = pdf.w - pdf.l_margin - pdf.r_margin

    def f(pt):
        return max(7.5, pt * scale)

    # ---- centred header: name + contact
    pdf.set_font("Helvetica", "B", f(20))
    pdf.multi_cell(0, f(20) * 0.42, _sanitize(name), align="C",
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if contact:
        pdf.ln(1)
        pdf.set_font("Helvetica", "", f(9.5))
        pdf.set_text_color(90, 90, 90)
        pdf.multi_cell(0, f(9.5) * 0.5, _sanitize(contact), align="C",
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)
    pdf.ln(3 * scale)

    lines = body.split("\n")
    prev_entry = False  # was the previous rendered line an "Employer | Date" entry?

    for raw in lines:
        line = _sanitize(raw.rstrip())
        stripped = line.strip()

        if not stripped:
            pdf.ln(2.2 * scale)
            prev_entry = False
            continue

        # --- section heading + underline rule
        if _is_heading(stripped):
            pdf.ln(2.4 * scale)
            pdf.set_font("Helvetica", "B", f(11.5))
            pdf.cell(0, f(11.5) * 0.5, stripped, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            y = pdf.get_y() + 0.6
            pdf.set_draw_color(180, 180, 180)
            pdf.line(pdf.l_margin, y, pdf.l_margin + content_w, y)
            pdf.ln(2.2 * scale)
            prev_entry = False
            continue

        # --- bullet: round dot marker + justified, hanging-indent text
        if _is_bullet(stripped):
            text = re.sub(r"^\s*[-*]\s+", "", stripped)
            pdf.set_font("Helvetica", "", f(10))
            line_h = f(10) * 0.52
            x0, y0 = pdf.l_margin, pdf.get_y()
            r = 0.45 * scale
            pdf.set_fill_color(0, 0, 0)
            pdf.ellipse(x0 + 1.3, y0 + line_h * 0.5 - r, 2 * r, 2 * r, style="F")
            pdf.set_x(x0 + 5)
            pdf.multi_cell(content_w - 5, line_h, text, align="J",
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            prev_entry = False
            continue

        # --- entry header "Employer | Date" -> bold left, date right
        if " | " in stripped:
            left, _, right = stripped.rpartition(" | ")
            h = f(10.5) * 0.55
            pdf.set_font("Helvetica", "", f(9.5))
            date_w = pdf.get_string_width(right) + 2
            pdf.set_font("Helvetica", "B", f(10.5))
            left = _truncate_to_width(pdf, left, content_w - date_w - 2)
            pdf.cell(content_w - date_w, h, left)
            pdf.set_font("Helvetica", "", f(9.5))
            pdf.cell(date_w, h, right, align="R",
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            prev_entry = True
            continue

        # --- role/location sub-line (the line right after an entry) -> italic
        if prev_entry and "," in stripped:
            role, _, loc = stripped.rpartition(", ")
            h = f(10) * 0.55
            pdf.set_font("Helvetica", "I", f(10))
            loc_w = pdf.get_string_width(loc) + 2
            pdf.cell(content_w - loc_w, h, role)
            pdf.cell(loc_w, h, loc, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            prev_entry = False
            continue

        # --- ordinary line (summary prose, skills): justified to both margins
        pdf.set_font("Helvetica", "I" if prev_entry else "", f(10))
        pdf.multi_cell(content_w, f(10) * 0.55, stripped, align="J",
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        prev_entry = False

    return pdf


def build_pdf(title, body, contact_line=""):
    """Return PDF bytes for a CV. Auto-fits to MAX_PAGES by scaling the font.

    `title`/`contact_line` are used only if the body has no name/contact header
    of its own (the tailored CV normally carries both as its first lines)."""
    body = body or ""
    name, contact, start = _split_header(body.split("\n"), title, contact_line)
    # Rejoin hard-wrapped lines into paragraphs + fix symbol bullets, so the
    # body justifies cleanly and bullet dots render (see _reflow).
    body_text = _reflow("\n".join(body.split("\n")[start:]).strip())

    # 1. Try each font scale; the first that fits MAX_PAGES wins (keeps all content).
    for scale in _SCALES:
        pdf = _render(name, contact, body_text, scale)
        if pdf.page_no() <= MAX_PAGES:
            return bytes(pdf.output())

    # 2. Still too long even at the smallest font (a pathologically long CV the AI
    #    shouldn't produce): hard-cap by keeping the most lines that fit 2 pages.
    min_scale = _SCALES[-1]
    lines = body_text.split("\n")
    best = _render(name, contact, "", min_scale)
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        trial = _render(name, contact, "\n".join(lines[:mid]), min_scale)
        if trial.page_no() <= MAX_PAGES:
            best, lo = trial, mid
        else:
            hi = mid - 1
    return bytes(best.output())
