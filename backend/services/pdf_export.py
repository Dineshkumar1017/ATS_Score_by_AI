import io
import logging
import re
from html.parser import HTMLParser

try:
    from weasyprint import HTML, CSS
    WEASYPRINT_INSTALLED = True
except Exception as exc:
    HTML = None
    CSS = None
    WEASYPRINT_INSTALLED = False
    WEASYPRINT_IMPORT_ERROR = exc
else:
    WEASYPRINT_IMPORT_ERROR = None

logger = logging.getLogger('ats_resume_scorer')

class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"h1", "h2", "h3", "p", "div", "li", "tr"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text + " ")

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*", "\n\n", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.get_text()


def _wrap_text(text: str, width: int = 92) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        words = raw_line.strip().split()
        if not words:
            lines.append("")
            continue

        line = words[0]
        for word in words[1:]:
            if len(line) + len(word) + 1 <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def _pdf_escape(text: str) -> str:
    text = text.encode("latin-1", errors="replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _fallback_pdf_from_text(title: str, body: str) -> bytes:
    page_width = 612
    page_height = 792
    left_margin = 50
    top_margin = 750
    line_height = 14
    bottom_margin = 50
    max_lines = int((top_margin - bottom_margin) / line_height)

    lines = [title, ""] + _wrap_text(body)
    pages = [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)] or [[]]

    objects: list[bytes] = []

    def add_object(content: bytes) -> int:
        objects.append(content)
        return len(objects)

    font_obj = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_refs: list[int] = []

    for page_lines in pages:
        commands = ["BT", "/F1 10 Tf", f"{left_margin} {top_margin} Td", f"{line_height} TL"]
        for line in page_lines:
            commands.append(f"({_pdf_escape(line)}) Tj")
            commands.append("T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1", errors="replace")
        content_obj = add_object(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
        page_obj = add_object(
            b"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 612 792] "
            + b"/Resources << /Font << /F1 "
            + str(font_obj).encode("ascii")
            + b" 0 R >> >> /Contents "
            + str(content_obj).encode("ascii")
            + b" 0 R >>"
        )
        page_refs.append(page_obj)

    kids = b" ".join(str(ref).encode("ascii") + b" 0 R" for ref in page_refs)
    pages_obj_content = (
        b"<< /Type /Pages /Kids ["
        + kids
        + b"] /Count "
        + str(len(page_refs)).encode("ascii")
        + b" >>"
    )
    pages_obj = add_object(pages_obj_content)

    for page_ref in page_refs:
        objects[page_ref - 1] = objects[page_ref - 1].replace(b"/Parent 0 0 R", b"/Parent " + str(pages_obj).encode("ascii") + b" 0 R")

    catalog_obj = add_object(b"<< /Type /Catalog /Pages " + str(pages_obj).encode("ascii") + b" 0 R >>")

    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode("ascii"))
        output.write(obj)
        output.write(b"\nendobj\n")

    xref_start = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))

    output.write(
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode("ascii")
        + b" /Root "
        + str(catalog_obj).encode("ascii")
        + b" 0 R >>\nstartxref\n"
        + str(xref_start).encode("ascii")
        + b"\n%%EOF\n"
    )
    return output.getvalue()


def generate_combined_pdf(html_docs: dict[str, str]) -> bytes:
    if not WEASYPRINT_INSTALLED:
        logger.warning(f"WeasyPrint unavailable, using plain PDF fallback: {WEASYPRINT_IMPORT_ERROR}")
        body = "\n\n".join(
            f"{name.replace('_', ' ').title()}\n{'=' * 40}\n{_html_to_text(html)}"
            for name, html in html_docs.items()
        )
        return _fallback_pdf_from_text("ATS Resume Score Report", body)
        
    documents = []
    
    # Render all 3 HTML strings to WeasyPrint Document objects
    for name, html_str in html_docs.items():
        doc = HTML(string=html_str).render()
        documents.append(doc)
    
    # Merge them into the first document
    first_doc = documents[0]
    for other_doc in documents[1:]:
        for page in other_doc.pages:
            first_doc.pages.append(page)
            
    # Write combined PDF bytes
    pdf_bytes = first_doc.write_pdf()
    return pdf_bytes
