import json

from backend.ingest import commit
from backend.ingest.remove_books import remove_books
from backend.search.store import Chunk, ChunkStore, StoreHolder
from conftest import fake_vectors


def _add(holder, cfg, slug, book, n, md5):
    chunks = [Chunk(id=f"{slug}::{i:04d}", book=book, title=f"R{i}", kind="receita", lang="pt",
                    text=f"{slug} texto {i}", ingredients=[], n_chars=10) for i in range(n)]
    commit.commit_book(holder, fake_vectors([c.text for c in chunks]), chunks, md5=md5, job_id=slug, cfg=cfg,
                       processed_entry={"n_chunks": n, "tier": "receitas", "slug": slug})
    meta_dir = cfg.index_dir / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / f"{slug}.json").write_text(json.dumps({"title": book, "lang": "pt"}))


def test_remove_old_copy_and_clean_label(tmp_cfg, repo, fake_embed):
    holder = StoreHolder(tmp_cfg)
    _add(holder, tmp_cfg, "antigo", "Livro X", 3, "m-antigo")
    _add(holder, tmp_cfg, "novo", "Livro X [livro-x.pdf]", 4, "m-novo")
    _add(holder, tmp_cfg, "outro", "Outro Livro", 2, "m-outro")
    job = repo.create(filename="livro-x.pdf", format="pdf", size_bytes=1, file_md5="m-novo", staged_path=None)
    repo.claim_next()
    repo.finish(job["id"], "added", book_title="Livro X [livro-x.pdf]", n_chunks=4)

    dry = remove_books(tmp_cfg, ["Livro X"], dry_run=True)
    assert dry["trechos_removidos"] == 3 and dry["rotulos_limpos"] == {"Livro X [livro-x.pdf]": "Livro X"}
    assert len(ChunkStore.load(tmp_cfg).chunks) == 9  # dry-run nao grava nada

    remove_books(tmp_cfg, ["Livro X"])
    store = ChunkStore.load(tmp_cfg)
    assert store.index.ntotal == len(store.chunks) == 6
    assert sorted(store.book_titles()) == ["Livro X", "Outro Livro"]
    assert {c.id.split("::")[0] for c in store.chunks if c.book == "Livro X"} == {"novo"}
    assert store.search("novo texto 1", k=1)[0].id == "novo::0001"

    raw = [json.loads(l) for l in tmp_cfg.chunks_raw_path.read_text().splitlines()]
    assert len(raw) == 6 and {r["book"] for r in raw} == {"Livro X", "Outro Livro"}
    processed = commit.load_processed(tmp_cfg)
    assert processed["m-antigo"] == {"n_chunks": 0, "removed": True, "slug": "antigo"}
    assert processed["m-novo"]["n_chunks"] == 4
    assert not (tmp_cfg.index_dir / "metadata" / "antigo.json").exists()
    assert json.loads((tmp_cfg.index_dir / "metadata" / "novo.json").read_text())["title"] == "Livro X"
    assert json.loads(tmp_cfg.manifest_path.read_text())["n_books"] == 2
    assert repo.get(job["id"])["book_title"] == "Livro X"


def test_unknown_title_aborts_without_changes(tmp_cfg):
    holder = StoreHolder(tmp_cfg)
    _add(holder, tmp_cfg, "a", "Livro A", 2, "m-a")
    before = tmp_cfg.meta_path.read_bytes()
    try:
        remove_books(tmp_cfg, ["Nao Existe"])
    except SystemExit as e:
        assert "Nao Existe" in str(e)
    assert tmp_cfg.meta_path.read_bytes() == before
