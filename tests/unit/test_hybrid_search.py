from backend.search.store import Chunk, ChunkStore, SearchFilters
from conftest import EMBED_DIM, fake_vectors


def _store(texts: list[str], books: list[str] | None = None) -> ChunkStore:
    store = ChunkStore.empty(EMBED_DIM)
    books = books or [f"Livro {i}" for i in range(len(texts))]
    chunks = [Chunk(id=f"b::{i}", book=b, title="", kind="receita", lang="pt",
                    text=t, ingredients=[], n_chars=len(t)) for i, (t, b) in enumerate(zip(texts, books))]
    store.append(fake_vectors(texts), chunks)
    return store


def test_filter_finds_match_outside_nearest_neighbours(fake_embed):
    # 1000 trechos quase identicos a "receita" ocupam os vizinhos mais proximos;
    # o unico com pera e presunto iberico precisa aparecer mesmo assim
    generic = ["receita"] * 1000
    target = "Peras e presunto ibérico com molho de salsinha, azeite de oliva e alho. 4 peras maduras"
    store = _store(generic + [target])
    hits = store.search("receita", k=8, filters=SearchFilters(ingredient="pera,presunto ibérico"))
    assert [h.text for h in hits] == [target]


def test_ingredient_matches_word_start_only(fake_embed):
    texts = [
        "asse a 180 graus, controlando a temperatura do forno",
        "espere a massa descansar",
        "peras cozidas no vinho tinto",
        "presunto\nibérico em lâminas finas",
    ]
    store = _store(texts)
    pera = store.search("sobremesa", k=8, filters=SearchFilters(ingredient="pera"))
    assert [h.text for h in pera] == [texts[2]]
    presunto = store.search("entrada", k=8, filters=SearchFilters(ingredient="presunto ibérico"))
    assert [h.text for h in presunto] == [texts[3]]


def test_book_filter_ranks_only_that_book(fake_embed):
    texts = [f"receita de peixe numero {i}" for i in range(300)] + ["receita de cordeiro no fogo"]
    books = ["Outro"] * 300 + ["Sete Fogos"]
    store = _store(texts, books)
    hits = store.search("receita de peixe", k=5, filters=SearchFilters(book="sete fogos"))
    assert [h.book for h in hits] == ["Sete Fogos"]


def test_filter_without_matches_returns_nothing(fake_embed):
    store = _store(["receita de pao", "receita de bolo"])
    assert store.search("receita", k=5, filters=SearchFilters(ingredient="trufa")) == []


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
