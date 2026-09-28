from backend.ingest.extract import extract
from backend.ingest.pipeline import Prepared, prepare, unique_slug


def test_recipe_book_is_prepared(fixtures_dir):
    result = prepare(extract(fixtures_dir["receitas.md"]).text)
    assert isinstance(result, Prepared)
    assert result.tier == "receitas" and result.lang == "pt"
    assert [c.kind for c in result.raw_chunks].count("receita") == 6


def test_non_culinary_is_discarded(fixtures_dir):
    assert prepare(extract(fixtures_dir["manual.txt"]).text) == "non_culinary"


def test_short_text_is_discarded():
    assert prepare("Bolo\n\nfarinha") == "too_little_text"


def test_unique_slug():
    assert unique_slug("Cozinha Mineira.pdf", "abcdef123", set()) == "Cozinha Mineira"
    assert unique_slug("Cozinha Mineira.pdf", "abcdef123", {"Cozinha Mineira"}) == "Cozinha Mineira-abcdef"
    assert unique_slug("Copy of Pães.epub", "0", set()) == "Pães"


def test_latin1_txt_keeps_accents(fixtures_dir):
    text = extract(fixtures_dir["receitas.txt"]).text
    assert "preparo" in text and ("ç" in text or "ã" in text or "é" in text)
