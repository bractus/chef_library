import json
import shutil

from backend.ingest import build_index
from backend.ingest.commit import load_processed
from backend.ingest.pipeline import file_md5
from backend.search.store import ChunkStore


def _books(cfg, fixtures_dir, *names):
    cfg.books_raw.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copy(fixtures_dir[n], cfg.books_raw / n)


def test_indexes_all_supported_formats(tmp_cfg, fixtures_dir, fake_embed, capsys):
    _books(tmp_cfg, fixtures_dir, "receitas.txt", "receitas.pdf", "receitas.epub")
    (tmp_cfg.books_raw / "planilha.xlsx").write_bytes(b"x" * 300)
    assert build_index.main(["--no-llm"], cfg=tmp_cfg) == 0
    store = ChunkStore.load(tmp_cfg)
    assert len(store.chunks) == 18 and len(store.book_titles()) == 3
    assert len(load_processed(tmp_cfg)) == 3
    out = capsys.readouterr().out
    assert "formato nao suportado" in out and "planilha.xlsx" in out
    # o texto extraido vai para o cache, para um rebuild nao refazer a extracao
    assert (tmp_cfg.text_cache_dir / f"{file_md5(tmp_cfg.books_raw / 'receitas.pdf')}.txt").exists()


def test_book_already_in_manifest_is_skipped(tmp_cfg, fixtures_dir, fake_embed, no_llm):
    _books(tmp_cfg, fixtures_dir, "receitas.txt")
    md5 = file_md5(tmp_cfg.books_raw / "receitas.txt")
    tmp_cfg.processed_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_cfg.processed_path.write_text(json.dumps({md5: {"n_chunks": 6, "tier": "receitas", "slug": "receitas"}}))
    assert build_index.main(["--no-llm", "--skip-embed"], cfg=tmp_cfg) == 0
    assert no_llm == []  # nada reprocessado


def test_rebuild_guard_refuses_and_force_proceeds(tmp_cfg, fixtures_dir, fake_embed, capsys):
    _books(tmp_cfg, fixtures_dir, "receitas.txt")
    processed = {f"md5-{i}": {"n_chunks": 5, "tier": "receitas", "slug": f"livro{i}"} for i in range(10)}
    tmp_cfg.processed_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_cfg.processed_path.write_text(json.dumps(processed))
    tmp_cfg.chunks_raw_path.write_text("linha que nao pode sumir\n")

    assert build_index.main(["--rebuild", "--no-llm"], cfg=tmp_cfg) == 2
    assert tmp_cfg.chunks_raw_path.read_text() == "linha que nao pode sumir\n"
    out = capsys.readouterr().out
    assert "books/ tem 1 arquivos, mas o acervo tem 10 livros indexados" in out
    assert "apagaria 9 livros" in out

    assert build_index.main(["--rebuild", "--no-llm", "--force"], cfg=tmp_cfg) == 0
    assert len(ChunkStore.load(tmp_cfg).chunks) == 6


def test_scanned_pdf_skipped_without_ocr(tmp_cfg, fixtures_dir, fake_embed, capsys):
    _books(tmp_cfg, fixtures_dir, "escaneado.pdf", "receitas.txt")
    assert build_index.main(["--no-llm", "--skip-embed"], cfg=tmp_cfg) == 0
    out = capsys.readouterr().out
    assert "[ocr] escaneado.pdf: sem texto extraivel, pulado (use --ocr)" in out
    assert file_md5(tmp_cfg.books_raw / "escaneado.pdf") not in load_processed(tmp_cfg)


def test_same_stem_books_get_distinct_slugs(tmp_cfg, fixtures_dir, fake_embed):
    tmp_cfg.books_raw.mkdir(parents=True)
    shutil.copy(fixtures_dir["receitas.pdf"], tmp_cfg.books_raw / "cozinha.pdf")
    shutil.copy(fixtures_dir["receitas.docx"], tmp_cfg.books_raw / "cozinha.docx")
    assert build_index.main(["--no-llm"], cfg=tmp_cfg) == 0
    store = ChunkStore.load(tmp_cfg)
    ids = [c.id for c in store.chunks]
    assert len(ids) == len(set(ids))
    assert len(store.slugs()) == 2
