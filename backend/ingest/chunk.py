"""Corta o texto limpo de um livro em chunks semanticos.

Estrategia, na ordem de preferencia:
  1. Livros com estrutura de receita (titulo + INGREDIENTES + MODO DE PREPARO,
     ou o formato classico "Proportions:/Procede:") => um chunk por receita.
  2. Livros de tecnica/historia sem esse padrao => um chunk por secao (linhas
     de titulo em CAIXA ALTA) quando ha secoes o bastante; senao, janela de
     paragrafos por tamanho-alvo.

Cada chunk carrega titulo, tipo (receita/tecnica/texto) e a lista de
ingredientes detectados nas linhas de quantidade, para facilitar filtro.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .classify import _ING_HDR, _MET_HDR, _CLASSIC_HDR, _QTY_LINE, TIER_RECIPES
from ..core.text import MAX_TITLE_CHARS, UPPERCASE_TITLE_RATIO, uppercase_ratio

_TITLE_CASE = re.compile(r'^([A-ZÀ-Ü][a-zà-ÿ\'’]*\s*){2,10}$')

TARGET_CHUNK_CHARS = 1200
MAX_CHUNK_CHARS = 2200
MIN_CHUNK_CHARS = 200
MIN_TITLE_CHARS = 3


def _is_title_line(s: str) -> bool:
    s = s.strip()
    if not s or len(s) > MAX_TITLE_CHARS or len(s) < MIN_TITLE_CHARS:
        return False
    if not any(c.isalpha() for c in s):
        return False
    if uppercase_ratio(s) > UPPERCASE_TITLE_RATIO:
        return True
    return bool(_TITLE_CASE.match(s)) and len(s.split()) <= 8


_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+')


def _hard_split_paragraph(p: str, size: int) -> list[str]:
    """Corta um paragrafo maior que `size` em pedacos, preferindo fronteira
    de frase. Rede de seguranca para paragrafos gigantes sem quebra de linha
    interna (comum em trechos de OCR mal segmentados) — sem isso, um unico
    paragrafo de centenas de milhares de caracteres viraria um chunk so,
    estourando o limite de contexto do modelo de embedding."""
    if len(p) <= size:
        return [p]
    sentences = _SENTENCE_BOUNDARY.split(p)
    if len(sentences) == 1:
        # nem pontuacao de frase tem — corta no tamanho mesmo
        return [p[i:i + size] for i in range(0, len(p), size)]
    out, buf = [], ""
    for s in sentences:
        if buf and len(buf) + len(s) + 1 > size:
            out.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf:
        out.append(buf)
    # se mesmo assim alguma frase isolada for maior que `size`, corta na marra
    final = []
    for piece in out:
        final.extend(_hard_split_paragraph(piece, size) if len(piece) > size * 1.5 else [piece])
    return final


def _split_paragraphs(text: str) -> list[str]:
    """Divide em paragrafos por linha em branco, preservando linhas de titulo isoladas."""
    paras, buf = [], []
    for line in text.split("\n"):
        if not line.strip():
            if buf:
                paras.append("\n".join(buf).strip())
                buf = []
            continue
        buf.append(line)
    if buf:
        paras.append("\n".join(buf).strip())
    return [p for p in paras if p]


@dataclass
class RawChunk:
    title: str
    kind: str
    text: str
    ingredients: list[str] = field(default_factory=list)


def _extract_ingredients(text: str) -> list[str]:
    out = []
    for m in _QTY_LINE.finditer(text):
        line = text[m.start():text.find("\n", m.start()) if "\n" in text[m.start():] else len(text)]
        line = line.strip()
        # remove a parte de quantidade+unidade do inicio, guarda o resto como "ingrediente"
        rest = _QTY_LINE.sub("", line, count=1).strip(" -:*").strip()
        rest = re.split(r'[,;(]', rest)[0].strip()
        if 2 <= len(rest) <= 60:
            out.append(rest)
    # dedup preservando ordem
    seen, uniq = set(), []
    for i in out:
        key = i.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(i)
    return uniq[:25]


def _looks_like_recipe_start(paragraph: str, lookahead: list[str]) -> bool:
    """Um paragrafo-titulo e inicio de receita se, nos proximos paragrafos,
    aparece um cabecalho de ingrediente/preparo, marcador classico, ou uma
    concentracao de linhas de quantidade."""
    window = "\n".join(lookahead[:6])
    if _ING_HDR.search(window) or _MET_HDR.search(window) or _CLASSIC_HDR.search(window):
        return True
    return len(_QTY_LINE.findall(window)) >= 2


def _chunk_by_recipe(paragraphs: list[str]) -> list[RawChunk]:
    starts: list[int] = []
    for i, p in enumerate(paragraphs):
        first_line = p.split("\n", 1)[0]
        if _is_title_line(first_line) and len(p.split("\n")) <= 2:
            if _looks_like_recipe_start(p, paragraphs[i + 1:i + 7]):
                starts.append(i)

    chunks: list[RawChunk] = []
    if not starts:
        return chunks

    # material antes da primeira receita = texto introdutorio (se substancial)
    if starts[0] > 0:
        intro = "\n\n".join(paragraphs[:starts[0]]).strip()
        if len(intro) >= MIN_CHUNK_CHARS:
            chunks.extend(_window_prose(intro, kind="texto", title="Introdução"))

    for si, start in enumerate(starts):
        end = starts[si + 1] if si + 1 < len(starts) else len(paragraphs)
        block = paragraphs[start:end]
        first_line = block[0].split("\n", 1)[0].strip()
        title = first_line.title() if first_line.isupper() else first_line
        body = "\n\n".join(block).strip()
        if len(body) < 15:
            continue
        # receita gigante (varias sub-receitas coladas) -> tambem quebra por tamanho
        if len(body) > MAX_CHUNK_CHARS * 2:
            for j, part in enumerate(_split_long(body, MAX_CHUNK_CHARS)):
                t = title if j == 0 else f"{title} (cont.)"
                chunks.append(RawChunk(t, "receita", part, _extract_ingredients(part)))
        else:
            chunks.append(RawChunk(title, "receita", body, _extract_ingredients(body)))
    return chunks


def _split_long(text: str, target: int) -> list[str]:
    paras = _split_paragraphs(text)
    out, buf, cur = [], [], 0
    for p in paras:
        for piece in _hard_split_paragraph(p, target * 2):
            if cur + len(piece) > target and buf:
                out.append("\n\n".join(buf))
                buf, cur = [], 0
            buf.append(piece)
            cur += len(piece)
    if buf:
        out.append("\n\n".join(buf))
    return out


def _window_prose(text: str, kind: str = "texto", title: str = "") -> list[RawChunk]:
    """Empacota paragrafos em janelas de ~TARGET_CHUNK_CHARS, sem cortar
    paragrafo ao meio, usando linhas-titulo como fronteira preferencial."""
    paragraphs = _split_paragraphs(text)
    chunks: list[RawChunk] = []
    buf: list[str] = []
    cur_title = title
    cur_len = 0

    def flush():
        nonlocal buf, cur_len
        if buf:
            body = "\n\n".join(buf).strip()
            if len(body) >= 30:
                chunks.append(RawChunk(cur_title or _guess_title(body), kind, body, []))
        buf, cur_len = [], 0

    for raw_p in paragraphs:
        first_line = raw_p.split("\n", 1)[0]
        is_section_title = _is_title_line(first_line) and len(first_line) < len(raw_p) + 5 and raw_p.count("\n") == 0
        if is_section_title and cur_len >= MIN_CHUNK_CHARS:
            flush()
            cur_title = first_line.strip()
            continue
        # rede de seguranca: paragrafo unico maior que o teto (OCR sem
        # quebra de linha interna) nao pode virar um chunk gigante sozinho
        for p in _hard_split_paragraph(raw_p, MAX_CHUNK_CHARS * 2):
            if cur_len + len(p) > MAX_CHUNK_CHARS and cur_len >= MIN_CHUNK_CHARS:
                flush()
            buf.append(p)
            cur_len += len(p)
            if cur_len >= TARGET_CHUNK_CHARS:
                flush()
    flush()
    return chunks


def _guess_title(body: str) -> str:
    first = body.split("\n", 1)[0].strip()
    return (first[:70] + "…") if len(first) > 70 else first


def chunk_book(clean_text_: str, tier: str) -> list[RawChunk]:
    """Ponto de entrada: escolhe a estrategia de chunking pelo tier do livro."""
    if not clean_text_.strip():
        return []

    paragraphs = _split_paragraphs(clean_text_)

    if tier == TIER_RECIPES:
        chunks = _chunk_by_recipe(paragraphs)
        if chunks:
            return chunks
        # sem titulos detectaveis mas classificado como receita (ex.: livro
        # classico so com "Proportions:") -> cai para janela de prosa,
        # ainda marcado como candidato a receita nas fronteiras de titulo
        return _window_prose(clean_text_, kind="receita")

    return _window_prose(clean_text_, kind="tecnica")
