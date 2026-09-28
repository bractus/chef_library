"""OCR de paginas de PDF escaneado com Tesseract (research R5).

O pdfium nao e seguro para uso concorrente (nem com documentos separados),
entao as paginas sao renderizadas numa thread so; o paralelismo fica no
Tesseract, que roda como subprocesso. O numero de paginas renderizadas em
voo e limitado — uma pagina a 300 DPI ocupa dezenas de MB em memoria.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

import pypdfium2 as pdfium

from ...core.config import CONFIG
from ..pipeline import detect_lang

OCR_DPI = 300
_PROBE_PAGES = 3
_TESS_LANG = {"pt": "por", "en": "eng", "fr": "fra", "es": "spa", "it": "ita"}


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def _render(doc: pdfium.PdfDocument, index: int):
    page = doc[index]
    try:
        return page.render(scale=OCR_DPI / 72).to_pil()
    finally:
        page.close()


# cada processo do Tesseract usa OpenMP com uma thread por nucleo; rodando
# varios em paralelo, sem esse limite eles disputam os mesmos nucleos (no
# container com 4 CPUs: ~23 s/pagina em vez de poucos segundos)
_TESS_ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}
_TESS_TIMEOUT_S = 300


def _tesseract(image, lang: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "page.png"
        image.save(png)
        result = subprocess.run(
            ["tesseract", str(png), "stdout", "-l", lang],
            env=_TESS_ENV, capture_output=True, timeout=_TESS_TIMEOUT_S, check=True,
        )
    return result.stdout.decode("utf-8", errors="replace").strip()


def ocr_pages(path: Path, page_indexes: list[int],
              progress: Callable[[int, int], None] | None = None, workers: int | None = None) -> dict[int, str]:
    """OCR das paginas pedidas. As primeiras passam por por+eng so para detectar
    o idioma do livro; o resto usa o idioma detectado (mais preciso e rapido).
    Uma excecao levantada por `progress` (ex.: cancelamento) interrompe o OCR."""
    total = len(page_indexes)
    out: dict[int, str] = {}
    done = 0

    def tick() -> None:
        nonlocal done
        done += 1
        if progress:
            progress(done, total)

    workers = max(1, workers or CONFIG.ocr_workers)
    doc = pdfium.PdfDocument(str(path))
    try:
        probe = page_indexes[:_PROBE_PAGES]
        for i in probe:
            out[i] = _tesseract(_render(doc, i), "por+eng")
            tick()

        code = _TESS_LANG.get(detect_lang("\n".join(out.values())), "por")
        lang = "eng" if code == "eng" else f"{code}+eng"
        rest = page_indexes[_PROBE_PAGES:]
        pool = ThreadPoolExecutor(max_workers=workers)
        pending: dict[Future, int] = {}
        try:
            def drain(block_until: int) -> None:
                while len(pending) > block_until:
                    finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for fut in finished:
                        out[pending.pop(fut)] = fut.result()
                        tick()

            for i in rest:
                pending[pool.submit(_tesseract, _render(doc, i), lang)] = i
                drain(workers * 2)
            drain(0)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    finally:
        doc.close()
    return out


def merge_pages(page_texts: list[str], ocr_texts: dict[int, str]) -> str:
    return "\n\n".join(
        t for t in (ocr_texts.get(i, pt) for i, pt in enumerate(page_texts)) if t.strip()
    )
