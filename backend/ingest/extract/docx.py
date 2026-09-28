from __future__ import annotations

import zipfile
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from . import Extraction, ExtractionError, register

_OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _is_heading(p: Paragraph) -> bool:
    name = (p.style.name if p.style is not None else "") or ""
    return name == "Title" or name.startswith("Heading")


@register("docx")
def extract_docx(path: Path) -> Extraction:
    with path.open("rb") as fh:
        if fh.read(8) == _OLE_HEADER:
            raise ExtractionError("protected", "DOCX protegido por senha (conteiner OLE)")
    try:
        document = docx.Document(str(path))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise ExtractionError("corrupt", f"{type(e).__name__}: {e}") from e

    blocks: list[str] = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if text:
                blocks.append(text)
        elif isinstance(item, Table):
            rows = []
            for row in item.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    rows.append(" ".join(cells))
            if rows:
                blocks.append("\n".join(rows))
    return Extraction(text="\n\n".join(blocks))
