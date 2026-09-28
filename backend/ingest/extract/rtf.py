from __future__ import annotations

from pathlib import Path

from striprtf.striprtf import rtf_to_text

from . import Extraction, register


@register("rtf")
def extract_rtf(path: Path) -> Extraction:
    raw = path.read_bytes()
    try:
        markup = raw.decode("cp1252")
    except UnicodeDecodeError:
        markup = raw.decode("latin-1")
    return Extraction(text=rtf_to_text(markup, errors="ignore"))
