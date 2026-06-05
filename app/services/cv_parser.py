"""Extract plain text from an uploaded CV (PDF, DOCX, or TXT).

DOCX is parsed with the stdlib (a .docx is just a zip of XML) so we avoid the
lxml/python-docx native dependency.
"""
import io
import zipfile
import xml.etree.ElementTree as ET

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_text(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return _from_pdf(data)
    if name.endswith(".docx"):
        return _from_docx(data)
    if name.endswith(".txt"):
        return data.decode("utf-8", errors="replace").strip()
    raise ValueError("Unsupported file type. Please upload a PDF, DOCX, or TXT file.")


def _from_pdf(data: bytes) -> str:
    from pypdf import PdfReader  # imported lazily so the app boots without it
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    text = "\n".join(parts).strip()
    if not text:
        raise ValueError(
            "Could not read any text from this PDF. It may be a scanned image — "
            "please upload a DOCX or text-based PDF."
        )
    return text


def _from_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        with zf.open("word/document.xml") as f:
            tree = ET.parse(f)
    paragraphs = []
    for para in tree.iter(f"{WORD_NS}p"):
        texts = [node.text for node in para.iter(f"{WORD_NS}t") if node.text]
        paragraphs.append("".join(texts))
    text = "\n".join(p for p in paragraphs).strip()
    if not text:
        raise ValueError("Could not read any text from this DOCX file.")
    return text
