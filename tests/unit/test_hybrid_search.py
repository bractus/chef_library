from backend.search.store import Chunk, ChunkStore
from conftest import EMBED_DIM, fake_vectors


def _store(texts: list[str]) -> ChunkStore:
    store = ChunkStore.empty(EMBED_DIM)
    chunks = [Chunk(id=f"b::{i}", book=f"Livro {i}", title="", kind="receita", lang="pt",
                    text=t, ingredients=[], n_chars=len(t)) for i, t in enumerate(texts)]
    store.append(fake_vectors(texts), chunks)
    return store


def test_rare_term_is_not_drowned_by_generic_words(fake_embed):
    generic = [f"receita completa de bolo de chocolate numero {i} com cobertura" for i in range(400)]
    specific = [
        "entremets aux fruits rouges: biscuit joconde, mousse framboise, glaçage miroir",
        "l'entremet se monte en cercle, couche de biscuit puis mousse",
        "entremet praliné noisette, croustillant feuilletine",
    ]
    store = _store(generic + specific)
    hits = store.search("receita completa de entremet", k=8)
    assert sum("entremet" in h.text for h in hits) == 3


def test_generic_query_stays_semantic(fake_embed):
    store = _store([f"receita de bolo de chocolate numero {i}" for i in range(50)])
    assert store._rare_terms("receita de bolo de chocolate") == []
    assert len(store.search("receita de bolo de chocolate", k=5)) == 5


def test_term_cache_is_reset_on_append(fake_embed):
    store = _store([f"receita de pao numero {i}" for i in range(300)])
    assert store._rare_terms("tempura") == []
    extra = ["tempura de legumes com massa leve e gelada"]
    store.append(fake_vectors(extra), [Chunk(id="n::0", book="Novo", title="", kind="receita", lang="pt",
                                             text=extra[0], ingredients=[], n_chars=10)])
    assert len(store._rare_terms("tempura")) == 1
    assert store.search("tempura", k=1)[0].id == "n::0"
