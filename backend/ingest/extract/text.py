from __future__ import annotations

from pathlib import Path

from charset_normalizer import from_bytes

from . import Extraction, register


def decode_bytes(raw: bytes) -> str:
    best = from_bytes(raw).best()
    return str(best) if best is not None else raw.decode("utf-8", errors="replace")


@register("txt")
@register("md")
def extract_text(path: Path) -> Extraction:
    return Extraction(text=decode_bytes(path.read_bytes()))
