"""Extracao de texto por formato, com saida no formato que o pipeline ja espera:
paragrafos separados por linha em branco e titulos em linha propria, sem "#"
(o chunker detecta titulo por capitalizacao, ver chunk._is_title_line)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ...core.config import Config
from ..formats import normalize_ext, sniff_matches


@dataclass
class Extraction:
    text: str
    pages_total: int | None = None
    pages_without_text: int | None = None
    page_texts: list[str] | None = None
    warnings: list[str] = field(default_factory=list)


class ExtractionError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        super().__init__(detail or reason_code)
        self.reason_code = reason_code  # "protected" | "corrupt"


_EXTRACTORS: dict[str, Callable[[Path], Extraction]] = {}


def register(ext: str):
    def deco(fn: Callable[[Path], Extraction]):
        _EXTRACTORS[ext] = fn
        return fn
    return deco


def extract(path: Path) -> Extraction:
    ext = normalize_ext(path.name)
    if ext is None or ext not in _EXTRACTORS:
        raise ExtractionError("corrupt", f"formato nao suportado: {path.suffix}")
    if not sniff_matches(path, ext):
        raise ExtractionError("corrupt", f"conteudo nao bate com a extensao .{ext}")
    return _EXTRACTORS[ext](path)


def _cache_path(md5: str, cfg: Config) -> Path:
    return cfg.text_cache_dir / f"{md5}.txt"


def cached_text(md5: str, cfg: Config) -> str | None:
    p = _cache_path(md5, cfg)
    return p.read_text(encoding="utf-8") if p.exists() else None


def store_text(md5: str, text: str, cfg: Config) -> None:
    p = _cache_path(md5, cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


# registra os extratores (cada modulo usa @register)
from . import text as _text  # noqa: E402,F401
from . import pdf as _pdf  # noqa: E402,F401
from . import html as _html  # noqa: E402,F401
from . import epub as _epub  # noqa: E402,F401
from . import docx as _docx  # noqa: E402,F401
from . import odt as _odt  # noqa: E402,F401
from . import rtf as _rtf  # noqa: E402,F401
