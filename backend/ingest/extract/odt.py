from __future__ import annotations

import zipfile
from pathlib import Path
from xml.parsers.expat import ExpatError

from odf import teletype
from odf.namespaces import TEXTNS
from odf.opendocument import load

from . import Extraction, ExtractionError, register


@register("odt")
def extract_odt(path: Path) -> Extraction:
    try:
        doc = load(str(path))
    except (zipfile.BadZipFile, KeyError, ExpatError, ValueError) as e:
        raise ExtractionError("corrupt", f"{type(e).__name__}: {e}") from e

    wanted = {(TEXTNS, "h"), (TEXTNS, "p")}
    blocks: list[str] = []

    def walk(node) -> None:
        for child in node.childNodes:
            if getattr(child, "qname", None) in wanted:
                t = teletype.extractText(child).strip()
                if t:
                    blocks.append(t)
            else:
                walk(child)

    walk(doc.text)
    return Extraction(text="\n\n".join(blocks))
