from __future__ import annotations

import re
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from . import Extraction, ExtractionError, register

# abaixo disso a pagina e tratada como imagem (escaneada) — numero de pagina,
# cabecalho corrido ou lixo de OCR embutido nao contam como texto
NO_TEXT_MIN_ALPHA = 40
# fracao de paginas sem texto: >= 80% e um PDF escaneado; entre 20% e 80% e
# parcial e o usuario decide (OCR ou seguir sem) antes de indexar — oferecer
# OCR depois exigiria substituir trechos, e remocao esta fora do escopo
NO_TEXT_RATIO = 0.8
PARTIAL_TEXT_RATIO = 0.2


def text_coverage(ex: Extraction) -> str:
    """"ok" | "no_text" | "partial_text" para uma extracao de PDF."""
    if not ex.pages_total:
        return "ok"
    ratio = (ex.pages_without_text or 0) / ex.pages_total
    if ratio >= NO_TEXT_RATIO:
        return "no_text"
    if ratio >= PARTIAL_TEXT_RATIO:
        return "partial_text"
    return "ok"


def open_pdf(path: Path) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        if getattr(e, "err_code", None) in (pdfium_c.FPDF_ERR_PASSWORD, pdfium_c.FPDF_ERR_SECURITY):
            raise ExtractionError("protected", str(e)) from e
        raise ExtractionError("corrupt", str(e)) from e


def page_has_text(text: str) -> bool:
    return sum(c.isalpha() for c in text) >= NO_TEXT_MIN_ALPHA


_LINE_BREAK = re.compile(r"\r\n|\n|\r")
_SAMPLES_PER_LINE = 8
PARAGRAPH_PITCH = 1.4   # distancia entre linhas acima disso (x a mediana da pagina) = novo paragrafo


def _median(values: list[float]) -> float:
    s = sorted(values)
    return s[len(s) // 2] if s else 0.0


def _page_text(textpage: pdfium.PdfTextPage) -> str:
    """Texto da pagina com paragrafos separados por linha em branco.

    get_text_range devolve so as linhas, sem distinguir paragrafo — e o
    chunker precisa do titulo da receita isolado num paragrafo para achar
    onde cada receita comeca. O texto continua sendo o do get_text_range
    (montar a partir de retangulos duplica letras quando eles se sobrepoem);
    a posicao dos caracteres so decide onde quebrar: distancia entre linhas
    bem maior que a normal da pagina, volta para cima (nova coluna) ou recuo
    de primeira linha.
    """
    n = textpage.count_chars()
    if n <= 0:
        return ""
    text = textpage.get_text_range(0, n)
    if len(text) != n:  # indices de caractere desalinhados (ex.: pares substitutos)
        return _LINE_BREAK.sub("\n", text)

    lines: list[tuple[float, float, float, str]] = []  # (topo, esquerda, altura, texto)
    start = 0
    for m in [*_LINE_BREAK.finditer(text), None]:
        end = m.start() if m else n
        seg = text[start:end]
        idxs = [start + j for j, ch in enumerate(seg) if not ch.isspace()]
        if idxs:
            step = max(len(idxs) // _SAMPLES_PER_LINE, 1)
            boxes = [textpage.get_charbox(i) for i in idxs[::step][:_SAMPLES_PER_LINE]]
            top = max(b[3] for b in boxes)
            height = max(b[3] - b[1] for b in boxes)
            lines.append((top, boxes[0][0], height, seg.strip()))
        start = m.end() if m else n
    if not lines:
        return ""

    pitches = [a[0] - b[0] for a, b in zip(lines, lines[1:]) if a[0] > b[0]]
    pitch = _median(pitches) or 1.0
    left_margin = _median([ln[1] for ln in lines])
    em = _median([ln[2] for ln in lines]) or 1.0

    out: list[str] = []
    prev = None
    for top, left, _h, content in lines:
        if prev is not None:
            new_column = top > prev[0] + em
            spaced = prev[0] - top > pitch * PARAGRAPH_PITCH
            indented = left > left_margin + em and prev[1] <= left_margin + em / 2
            if new_column or spaced or indented:
                out.append("")
        out.append(content)
        prev = (top, left)
    return "\n".join(out)


@register("pdf")
def extract_pdf(path: Path) -> Extraction:
    doc = open_pdf(path)
    try:
        page_texts: list[str] = []
        for i in range(len(doc)):
            page = doc[i]
            textpage = page.get_textpage()
            try:
                raw = _page_text(textpage)
            except pdfium.PdfiumError:
                raw = textpage.get_text_range()
            textpage.close()
            page.close()
            page_texts.append(raw.replace("\r\n", "\n").replace("\r", "\n").strip())
    except pdfium.PdfiumError as e:
        raise ExtractionError("corrupt", str(e)) from e
    finally:
        doc.close()

    empty = sum(1 for t in page_texts if not page_has_text(t))
    return Extraction(
        text="\n\n".join(t for t in page_texts if page_has_text(t)),
        pages_total=len(page_texts),
        pages_without_text=empty,
        page_texts=page_texts,
    )
