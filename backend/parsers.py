"""Extract structured, per-slide text from lesson materials.

Supported: .pptx (per slide, incl. speaker notes), .pdf (per page), .docx
(per heading/section). Everything is normalised into a list of "units" so the
analyzer can reason about slides regardless of the source format.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

SUPPORTED_EXTENSIONS = {".pptx", ".ppt", ".pdf", ".docx"}

MAX_CHARS_PER_UNIT = 1200
MAX_TOTAL_CHARS = 60_000


@dataclass
class Unit:
    """One analysable chunk of the old material (usually one slide/page)."""

    index: int  # 1-based slide/page/section number
    title: str
    text: str
    notes: str = ""

    def as_dict(self) -> dict:
        return {"index": self.index, "title": self.title, "text": self.text, "notes": self.notes}


@dataclass
class ParsedMaterial:
    kind: str  # "pptx" | "pdf" | "docx"
    source_name: str
    units: list = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Render units as a numbered transcript for the LLM, with length caps."""
        lines, total = [], 0
        for u in self.units:
            block_parts = [f"--- Unit {u.index} ---"]
            if u.title:
                block_parts.append(f"Title: {u.title}")
            if u.text:
                block_parts.append(u.text[:MAX_CHARS_PER_UNIT])
            if u.notes:
                block_parts.append(f"(speaker notes: {u.notes[:400]})")
            block = "\n".join(block_parts)
            if total + len(block) > MAX_TOTAL_CHARS:
                lines.append("... [material truncated]")
                break
            lines.append(block)
            total += len(block)
        return "\n\n".join(lines)


# --------------------------------------------------------------------------- #
# PPTX
# --------------------------------------------------------------------------- #
def parse_pptx(path: str) -> ParsedMaterial:
    from pptx import Presentation

    prs = Presentation(path)
    units = []
    for i, slide in enumerate(prs.slides, start=1):
        title = ""
        chunks = []
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            txt = shape.text_frame.text.strip()
            if not txt:
                continue
            if shape == getattr(slide.shapes, "title", None) or (
                shape.is_placeholder and shape.placeholder_format.idx == 0
            ):
                title = txt
            else:
                chunks.append(txt)
        notes = ""
        if slide.has_notes_slide:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
        units.append(Unit(index=i, title=title, text="\n".join(chunks), notes=notes))
    return ParsedMaterial(kind="pptx", source_name=os.path.basename(path), units=units)


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def parse_pdf(path: str) -> ParsedMaterial:
    from pypdf import PdfReader

    reader = PdfReader(path)
    units = []
    for i, page in enumerate(reader.pages, start=1):
        raw = (page.extract_text() or "").strip()
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        title = lines[0][:120] if lines else ""
        text = "\n".join(lines[1:]) if len(lines) > 1 else ""
        units.append(Unit(index=i, title=title, text=text))
    return ParsedMaterial(kind="pdf", source_name=os.path.basename(path), units=units)


# --------------------------------------------------------------------------- #
# DOCX
# --------------------------------------------------------------------------- #
def parse_docx(path: str, target_chars_per_unit: int = 2000) -> ParsedMaterial:
    """Sections start at Heading/Title styles; long runs are split by size."""
    import docx as docx_lib

    document = docx_lib.Document(path)
    units: list[Unit] = []
    cur_title, cur_chunks, cur_len = "", [], 0

    def flush():
        nonlocal cur_title, cur_chunks, cur_len
        text = "\n".join(cur_chunks).strip()
        if text or cur_title:
            units.append(Unit(index=len(units) + 1, title=cur_title, text=text))
        cur_title, cur_chunks, cur_len = "", [], 0

    def iter_block_items(doc):
        from docx.document import Document as _Doc
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.ns import qn

        body = doc.element.body if isinstance(doc, _Doc) else doc
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, doc)
            elif child.tag == qn("w:tbl"):
                yield Table(child, doc)

    for block in iter_block_items(document):
        if block.__class__.__name__ == "Paragraph":
            style = (block.style.name or "") if block.style is not None else ""
            text = block.text.strip()
            is_heading = style.startswith("Heading") or style == "Title"
            if is_heading:
                flush()
                cur_title = text
                continue
            if text:
                cur_chunks.append(text)
                cur_len += len(text)
                if cur_len >= target_chars_per_unit:
                    flush()
        else:  # table
            rows = []
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells]
                rows.append(" | ".join(cells))
            cur_chunks.append("\n".join(rows))
            cur_len += sum(len(r) for r in rows)
            if cur_len >= target_chars_per_unit:
                flush()
    flush()
    return ParsedMaterial(kind="docx", source_name=os.path.basename(path), units=units)


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# PPT (legacy binary format)
# --------------------------------------------------------------------------- #
# Record types from the MS-PPT spec (only the text atoms we care about).
_TEXT_CHARS_ATOM = 0x0FA0   # UTF-16LE text
_TEXT_BYTES_ATOM = 0x0FA8   # ANSI text
_CSTRING_ATOM = 0x0FBA      # UTF-16LE (slide title placeholder strings etc.)
_SLIDE_BOUNDARY_TYPES = {1000, 1006, 1008, 1016}  # document / slide / notes / master


def _walk_ppt_records(data: bytes, offset: int, end: int, texts: list, out: list) -> None:
    """Recursively walk PPT record containers, collecting text atoms.

    Container records (version nibble 0xF) are descended into; text atoms are
    appended to `out`; a Slide record boundary flushes a new unit.
    """
    pos = offset
    while pos + 8 <= end:
        ver_inst = int.from_bytes(data[pos : pos + 2], "little")
        rtype = int.from_bytes(data[pos + 2 : pos + 4], "little")
        rlen = int.from_bytes(data[pos + 4 : pos + 8], "little")
        body_start = pos + 8
        body_end = min(body_start + rlen, end)
        if rtype in _SLIDE_BOUNDARY_TYPES and (ver_inst & 0x0F) == 0x0F:
            if texts:
                out.append(list(texts))
                texts.clear()
            _walk_ppt_records(data, body_start, body_end, texts, out)
            if texts:
                out.append(list(texts))
                texts.clear()
            pos = body_start + rlen
            continue
        if (ver_inst & 0x0F) == 0x0F:  # generic container -> descend
            _walk_ppt_records(data, body_start, body_end, texts, out)
        elif rtype == _TEXT_CHARS_ATOM or rtype == _CSTRING_ATOM:
            try:
                t = data[body_start:body_end].decode("utf-16-le", errors="ignore").strip()
                if t:
                    texts.append(t)
            except Exception:
                pass
        elif rtype == _TEXT_BYTES_ATOM:
            try:
                t = data[body_start:body_end].decode("cp1252", errors="ignore").strip()
                if t:
                    texts.append(t)
            except Exception:
                pass
        pos = body_start + rlen if rlen > 0 else pos + 8


def parse_ppt(path: str) -> ParsedMaterial:
    """Extract text from legacy .ppt (OLE2) files.

    Uses the olefile compound-file reader plus a lightweight record scan for
    text atoms (TextCharsAtom / TextBytesAtom / CString). Slide boundaries are
    approximated from Slide containers - good enough for gap analysis, which
    needs the deck's content, not pixel layout.
    """
    import olefile

    ole = olefile.OleFileIO(path)
    try:
        if not ole.exists("PowerPoint Document"):
            raise ValueError("Not a readable .ppt file (missing PowerPoint stream).")
        data = ole.openstream("PowerPoint Document").read()
    finally:
        ole.close()

    texts: list = []
    units_raw: list = []
    _walk_ppt_records(data, 0, len(data), texts, units_raw)
    if texts:
        units_raw.append(list(texts))

    units = []
    for i, chunk in enumerate(units_raw, start=1):
        # de-duplicate while keeping order
        seen, lines = set(), []
        for t in chunk:
            for line in (ln.strip() for ln in t.replace(chr(11), chr(10)).split(chr(10))):
                if line and line not in seen and len(line) > 1:
                    seen.add(line)
                    lines.append(line)
        if not lines:
            continue
        units.append(Unit(index=len(units) + 1, title=lines[0][:120], text=chr(10).join(lines[1:])))

    if not units:
        raise ValueError(
            "No text could be extracted from this .ppt file. "
            "Please re-save it as .pptx (File > Save As) and upload again."
        )
    return ParsedMaterial(kind="ppt", source_name=os.path.basename(path), units=units)


def parse_file(path: str) -> ParsedMaterial:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pptx":
        return parse_pptx(path)
    if ext == ".ppt":
        return parse_ppt(path)
    if ext == ".pdf":
        return parse_pdf(path)
    if ext == ".docx":
        return parse_docx(path)
    raise ValueError(f"Unsupported file type: {ext}. Supported: {sorted(SUPPORTED_EXTENSIONS)}")
