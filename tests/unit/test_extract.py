import pytest

from backend.ingest.extract import ExtractionError, extract
from make_fixtures import recipe_book

SEED = {"receitas.txt": 1, "receitas.pdf": 2, "receitas.epub": 3, "receitas.docx": 4,
        "receitas.odt": 5, "receitas.rtf": 6, "receitas.html": 7}


@pytest.mark.parametrize("name", sorted(SEED))
def test_titles_on_their_own_lines(fixtures_dir, name):
    text = extract(fixtures_dir[name]).text
    lines = {ln.strip() for ln in text.splitlines()}
    for recipe in recipe_book(SEED[name]):
        title = recipe["title"]
        if name == "receitas.txt":  # latin-1 nao tem todos os acentos do conjunto; compara sem eles
            assert any(title[:6] in ln for ln in lines), title
        else:
            assert title in lines, (name, title)
    assert "#" not in text


def test_html_drops_script_and_nav(fixtures_dir):
    text = extract(fixtures_dir["receitas.html"]).text
    assert "rastreio" not in text and "Menu Inicio" not in text


def test_docx_table_becomes_lines(fixtures_dir):
    first = recipe_book(4)[0]
    text = extract(fixtures_dir["receitas.docx"]).text
    qty, _, name = first["ingredients"][0].partition(" de ")
    assert f"{qty} de {name}" in text


@pytest.mark.parametrize("name,reason", [
    ("drm.epub", "protected"), ("protegido.docx", "protected"), ("quebrado.pdf", "corrupt"),
])
def test_unreadable_files(fixtures_dir, name, reason):
    with pytest.raises(ExtractionError) as exc:
        extract(fixtures_dir[name])
    assert exc.value.reason_code == reason


def test_extension_mismatch_is_corrupt(tmp_path):
    fake = tmp_path / "livro.epub"
    fake.write_bytes(b"isto nao e um zip" * 20)
    with pytest.raises(ExtractionError) as exc:
        extract(fake)
    assert exc.value.reason_code == "corrupt"


def test_pdf_page_stats(fixtures_dir):
    text_pdf = extract(fixtures_dir["receitas.pdf"])
    assert (text_pdf.pages_total, text_pdf.pages_without_text) == (6, 0)
    scanned = extract(fixtures_dir["escaneado.pdf"])
    assert scanned.pages_without_text == scanned.pages_total == 3
    partial = extract(fixtures_dir["parcial.pdf"])
    assert (partial.pages_total, partial.pages_without_text) == (9, 3)


def test_pdf_paragraphs_are_reconstructed(fixtures_dir):
    text = extract(fixtures_dir["receitas.pdf"]).text
    title = recipe_book(2)[0]["title"]
    assert f"{title}\n\nIngredientes" in text
