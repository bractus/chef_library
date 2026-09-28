import json

import pytest

from backend.ingest import commit
from backend.search.store import Chunk, ChunkStore, StoreHolder
from conftest import fake_vectors


def _chunks(prefix: str, n: int) -> list[Chunk]:
    return [
        Chunk(id=f"{prefix}::{i:04d}", book=f"Livro {prefix}", title=f"Receita {i}", kind="receita",
              lang="pt", text=f"texto {prefix} {i} farinha", ingredients=[], n_chars=20)
        for i in range(n)
    ]


def _commit(holder, cfg, prefix, n=3):
    chunks = _chunks(prefix, n)
    vectors = fake_vectors([c.text for c in chunks])
    commit.commit_book(holder, vectors, chunks, md5=f"md5-{prefix}", job_id=f"job-{prefix}", cfg=cfg,
                       processed_entry={"n_chunks": n, "tier": "receitas", "slug": prefix})
    return chunks


def _raw_ids(cfg):
    return [json.loads(l)["id"] for l in cfg.chunks_raw_path.read_text(encoding="utf-8").splitlines()]


def test_commit_on_empty_library_creates_index(tmp_cfg):
    holder = StoreHolder(tmp_cfg)
    _commit(holder, tmp_cfg, "a")
    loaded = ChunkStore.load(tmp_cfg)
    assert loaded.index.ntotal == len(loaded.chunks) == 3
    assert json.loads(tmp_cfg.manifest_path.read_text())["n_chunks"] == 3
    assert commit.load_processed(tmp_cfg)["md5-a"]["slug"] == "a"
    assert _raw_ids(tmp_cfg) == [f"a::{i:04d}" for i in range(3)]
    assert holder.get().index.ntotal == 3


def test_commit_appends_to_existing_library(tmp_cfg):
    holder = StoreHolder(tmp_cfg)
    _commit(holder, tmp_cfg, "a")
    _commit(holder, tmp_cfg, "b", n=2)
    loaded = ChunkStore.load(tmp_cfg)
    assert loaded.index.ntotal == len(loaded.chunks) == 5
    assert {c.book for c in loaded.chunks} == {"Livro a", "Livro b"}
    assert not tmp_cfg.commit_journal_path.exists()


def test_failure_before_journal_reverts_memory_and_keeps_files(tmp_cfg, monkeypatch):
    import faiss

    holder = StoreHolder(tmp_cfg)
    _commit(holder, tmp_cfg, "a")
    before = tmp_cfg.meta_path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disco cheio")

    monkeypatch.setattr(faiss, "write_index", boom)
    with pytest.raises(OSError):
        _commit(holder, tmp_cfg, "b")
    assert holder.get().index.ntotal == len(holder.get().chunks) == 3
    assert tmp_cfg.meta_path.read_bytes() == before
    assert not (tmp_cfg.index_dir / "chunks.faiss.tmp").exists()
    assert not (tmp_cfg.index_dir / "chunks.jsonl.tmp").exists()


def test_interruption_after_journal_is_completed_by_recover(tmp_cfg, monkeypatch):
    holder = StoreHolder(tmp_cfg)
    _commit(holder, tmp_cfg, "a")

    def crash(cfg, holder=None):
        raise KeyboardInterrupt  # processo morto entre o diario e os os.replace

    monkeypatch.setattr(commit, "_apply_journal", crash)
    with pytest.raises(KeyboardInterrupt):
        _commit(holder, tmp_cfg, "b", n=2)
    monkeypatch.undo()

    assert tmp_cfg.commit_journal_path.exists()
    assert ChunkStore.load(tmp_cfg).index.ntotal == 3  # ainda o estado antigo, consistente
    assert commit.recover(tmp_cfg) is True
    loaded = ChunkStore.load(tmp_cfg)
    assert loaded.index.ntotal == len(loaded.chunks) == 5
    assert _raw_ids(tmp_cfg).count("b::0000") == 1


def test_recover_is_idempotent(tmp_cfg, monkeypatch):
    holder = StoreHolder(tmp_cfg)
    _commit(holder, tmp_cfg, "a")
    journal = None
    real_apply = commit._apply_journal

    def apply_then_crash(cfg, holder=None):
        nonlocal journal
        journal = cfg.commit_journal_path.read_text()
        real_apply(cfg, holder)
        raise KeyboardInterrupt  # morreu depois de aplicar, antes de o processo seguir

    monkeypatch.setattr(commit, "_apply_journal", apply_then_crash)
    with pytest.raises(KeyboardInterrupt):
        _commit(holder, tmp_cfg, "b", n=2)
    monkeypatch.undo()

    tmp_cfg.commit_journal_path.write_text(journal)  # simula diario ainda presente
    commit.recover(tmp_cfg)
    commit.recover(tmp_cfg)
    assert _raw_ids(tmp_cfg).count("b::0000") == 1
    assert len(_raw_ids(tmp_cfg)) == 5


def test_stray_tmp_without_journal_is_discarded(tmp_cfg):
    holder = StoreHolder(tmp_cfg)
    _commit(holder, tmp_cfg, "a")
    (tmp_cfg.index_dir / "chunks.faiss.tmp").write_bytes(b"lixo")
    (tmp_cfg.index_dir / "chunks.jsonl.tmp").write_text("lixo")
    assert commit.recover(tmp_cfg) is False
    assert not (tmp_cfg.index_dir / "chunks.faiss.tmp").exists()
    assert ChunkStore.load(tmp_cfg).index.ntotal == 3


def test_search_sees_new_chunks_after_commit(tmp_cfg, fake_embed):
    holder = StoreHolder(tmp_cfg)
    _commit(holder, tmp_cfg, "a")
    hits = holder.get().search("texto a 1 farinha", k=1)
    assert hits and hits[0].id == "a::0001"
