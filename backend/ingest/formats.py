"""Formatos aceitos na ingestao, limite de tamanho e checagem pelos bytes iniciais.

Fonte unica para a API (GET /api/ingest/config), o worker e o CLI de indexacao.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

SUPPORTED: dict[str, str] = {
    "pdf": "PDF",
    "epub": "EPUB",
    "docx": "Word (DOCX)",
    "odt": "OpenDocument (ODT)",
    "rtf": "RTF",
    "html": "HTML",
    "txt": "Texto (TXT)",
    "md": "Markdown",
}
ALIASES: dict[str, str] = {"htm": "html"}

_ZIP_MIMETYPES = {
    "epub": "application/epub+zip",
    "odt": "application/vnd.oasis.opendocument.text",
}


def normalize_ext(filename: str) -> str | None:
    ext = Path(filename).suffix.lower().lstrip(".")
    ext = ALIASES.get(ext, ext)
    return ext if ext in SUPPORTED else None


def sniff_matches(path: Path, ext: str) -> bool:
    """Confere se o conteudo bate com a extensao. Nao valida o arquivo inteiro:
    so evita mandar, por exemplo, um .zip renomeado para .pdf ao extrator errado."""
    with path.open("rb") as fh:
        head = fh.read(8)
    if ext == "pdf":
        return head.startswith(b"%PDF")
    if ext == "rtf":
        return head.startswith(b"{\\rtf")
    if ext == "docx" and head.startswith(b"\xd0\xcf\x11\xe0"):
        return True  # contêiner OLE = DOCX com senha; o extrator reporta "protected"
    if ext in ("epub", "odt", "docx"):
        if not head.startswith(b"PK"):
            return False
        try:
            with zipfile.ZipFile(path) as zf:
                if ext == "docx":
                    return "[Content_Types].xml" in zf.namelist()
                return zf.read("mimetype").decode("ascii", "replace").strip() == _ZIP_MIMETYPES[ext]
        except (zipfile.BadZipFile, KeyError):
            return False
    return True


def config_payload(*, enabled: bool, ocr_available: bool) -> dict:
    formats = []
    for ext, label in SUPPORTED.items():
        item = {"ext": ext, "label": label}
        aliases = [a for a, target in ALIASES.items() if target == ext]
        if aliases:
            item["aliases"] = aliases
        formats.append(item)
    return {
        "enabled": enabled,
        "disabled_reason": None if enabled else "embeddings_unavailable",
        "ocr_available": ocr_available,
        "formats": formats,
    }
