import random

import numpy as np

from backend.ingest.dedup import find_duplicate
from backend.search.store import Chunk, ChunkStore
from conftest import EMBED_DIM, fake_vectors


def _store(books: dict[str, list[str]]) -> ChunkStore:
    store = ChunkStore.empty(EMBED_DIM)
    for book, texts in books.items():
        chunks = [Chunk(id=f"{book}::{i}", book=book, title="", kind="receita", lang="pt",
                        text=t, ingredients=[], n_chars=len(t)) for i, t in enumerate(texts)]
        store.append(fake_vectors(texts), chunks)
    return store


_SYLLABLES = ["ba", "ce", "di", "fo", "gu", "la", "me", "ni", "po", "ru", "sa", "te", "vi", "xo", "za"]


def _paragraphs(seed: int, n: int = 24) -> list[str]:
    """Texto de um livro 'real': palavras sorteadas por livro, sem molde comum
    (os livros de make_fixtures compartilham frases-modelo de proposito)."""
    rng = random.Random(seed)
    vocab = ["".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(2, 4))) for _ in range(3000)]
    return [" ".join(rng.choice(vocab) for _ in range(60)) for _ in range(n)]


def _rechunk(paragraphs: list[str], size: int) -> list[str]:
    """Mesmo texto cortado em outras fronteiras (outra extracao da mesma obra)."""
    return [" ".join(paragraphs[i:i + size]) for i in range(0, len(paragraphs), size)]


def test_same_work_with_other_chunk_boundaries_is_duplicate():
    original = _paragraphs(1)
    store = _store({"Original": original, "Outro": _paragraphs(2)})
    rechunked = _rechunk(original, 3)
    vectors = fake_vectors(rechunked)
    sims = (store.index.search(vectors, 1)[0][:, 0])
    assert (sims < 0.97).mean() > 0.5  # a regra antiga (so embeddings) nao pegaria
    assert find_duplicate(store, vectors, rechunked) == "Original"


def test_different_book_is_not_duplicate():
    store = _store({"A": _paragraphs(1), "B": _paragraphs(2)})
    new = _paragraphs(3)
    assert find_duplicate(store, fake_vectors(new), new) is None


def test_candidate_without_shared_text_is_not_duplicate():
    """Vizinhos proximos (mesmo tema/serie) mas texto diferente: nao e a mesma obra."""
    store = _store({"Serie vol 1": _paragraphs(1)})
    new = _paragraphs(4)
    vectors = store.index.reconstruct_batch(np.arange(min(len(new), store.index.ntotal)))
    assert find_duplicate(store, vectors, new[:len(vectors)]) is None


def test_empty_or_missing_store():
    texts = _paragraphs(5)
    assert find_duplicate(None, fake_vectors(texts), texts) is None
    assert find_duplicate(ChunkStore.empty(EMBED_DIM), fake_vectors(texts), texts) is None
