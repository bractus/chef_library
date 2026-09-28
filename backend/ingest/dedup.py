"""Duplicata por conteudo: a mesma obra em outro arquivo ou formato (research R6).

Duas etapas:
  1. candidatos pelos embeddings — para cada trecho novo, o livro do vizinho
     mais proximo no indice; livros que concentram boa parte desses vizinhos
     viram candidatos;
  2. confirmacao pelo texto — fracao das sequencias de 5 palavras do livro
     novo que tambem aparecem no candidato.

So a etapa 1 nao basta: a mesma obra extraida de PDF e de Markdown e cortada
em trechos com fronteiras diferentes, e a similaridade trecho a trecho fica em
~0,82-0,86 (medido com os Modernist Cuisine vol. 3, 4 e 6: 1-3% dos trechos
acima de 0,97). O texto em comum separa bem: 34-43% para a mesma obra, <=1,3%
para obras diferentes, inclusive da mesma serie.
"""
from __future__ import annotations

import re
import zlib
from collections import Counter
from typing import Iterable

import numpy as np

from ..core.text import fold
from ..search.store import ChunkStore

CANDIDATE_MIN_SHARE = 0.2     # fracao dos vizinhos mais proximos que torna um livro candidato
MAX_CANDIDATES = 3
SHINGLE_WORDS = 5
MIN_CONTAINMENT = 0.15        # texto do livro novo presente no candidato para ser a mesma obra

_WORD = re.compile(r"[a-z]{3,}")


def shingles(texts: Iterable[str]) -> set[int]:
    words = _WORD.findall(fold(" ".join(texts)))
    k = SHINGLE_WORDS
    return {zlib.crc32(" ".join(words[i:i + k]).encode()) for i in range(len(words) - k + 1)}


def find_duplicate(store: ChunkStore | None, vectors: np.ndarray, texts: list[str]) -> str | None:
    """Titulo do livro do acervo que e a mesma obra, ou None."""
    if store is None or len(vectors) == 0:
        return None
    with store.lock:
        if store.index.ntotal == 0:
            return None
        _, idxs = store.index.search(np.ascontiguousarray(vectors, dtype="float32"), 1)
        neighbours = Counter(store.chunks[i].book for i in idxs[:, 0] if i >= 0)
        candidates = [book for book, n in neighbours.most_common(MAX_CANDIDATES)
                      if n >= CANDIDATE_MIN_SHARE * len(vectors)]
        candidate_texts = {book: [c.text for c in store.chunks if c.book == book] for book in candidates}

    new = shingles(texts)
    if not new:
        return None
    for book in candidates:
        if len(new & shingles(candidate_texts[book])) / len(new) >= MIN_CONTAINMENT:
            return book
    return None
