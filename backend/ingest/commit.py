"""Commit atomico de um livro no indice (research R2) e lock entre a API e o CLI (R3).

chunks.faiss e chunks.jsonl precisam mudar juntos (ChunkStore.load recusa
ntotal != len(chunks)), mas os.replace so e atomico por arquivo. Por isso:

  1. grava chunks.faiss.tmp e chunks.jsonl.tmp (fsync);
  2. grava commit.json (fsync) — este e o ponto de commit;
  3. troca os .tmp pelos finais, acrescenta em chunks_raw.jsonl, atualiza
     processed.json e manifest.json e apaga o diario.

Uma interrupcao antes do passo 2 deixa so .tmp soltos (descartados na
recuperacao); depois dele, a recuperacao refaz o passo 3, que e idempotente.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from ..core.config import Config
from ..search.store import Chunk, ChunkStore, StoreHolder

_INDEX_FILES = ("chunks.faiss", "chunks.jsonl")


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _fsync_file(tmp)
    os.replace(tmp, path)


@contextmanager
def index_lock(cfg: Config, on_wait: Callable[[], None] | None = None) -> Iterator[None]:
    cfg.index_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.lock_path, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if on_wait:
                on_wait()
            fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load_processed(cfg: Config) -> dict:
    p = cfg.processed_path
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _set_processed(cfg: Config, md5: str, entry: dict) -> None:
    processed = load_processed(cfg)
    processed[md5] = entry
    _write_json_atomic(cfg.processed_path, processed)


def record_processed(md5: str, entry: dict, cfg: Config) -> None:
    """Registra um livro que nao entrou no acervo (duplicata/descarte), para o CLI pula-lo."""
    with index_lock(cfg):
        _set_processed(cfg, md5, entry)


def commit_book(holder: StoreHolder, vectors: np.ndarray, chunks: list[Chunk], *, md5: str,
                processed_entry: dict, job_id: str, cfg: Config,
                on_wait: Callable[[], None] | None = None) -> None:
    vectors = np.ascontiguousarray(vectors, dtype="float32")
    with index_lock(cfg, on_wait):
        # se o CLI reconstruiu o indice enquanto esperavamos o lock, a copia em
        # memoria esta velha e grava-la apagaria o rebuild
        holder.reload_if_changed()
        holder.committing = True
        try:
            _commit_locked(holder, vectors, chunks, md5=md5, processed_entry=processed_entry,
                           job_id=job_id, cfg=cfg)
        finally:
            holder.committing = False


def _commit_locked(holder: StoreHolder, vectors: np.ndarray, chunks: list[Chunk], *, md5: str,
                   processed_entry: dict, job_id: str, cfg: Config) -> None:
    import faiss

    store = holder.get_or_none()
    created = store is None
    if created:
        store = ChunkStore.empty(vectors.shape[1])

    faiss_tmp = cfg.index_dir / "chunks.faiss.tmp"
    meta_tmp = cfg.index_dir / "chunks.jsonl.tmp"
    new_lines = [c.to_json() + "\n" for c in chunks]
    n_before = store.index.ntotal
    store.append(vectors, chunks)
    try:
        # so o worker escreve no indice (buscas so leem), entao serializar
        # sem segurar o lock nao bloqueia as consultas durante a gravacao
        faiss.write_index(store.index, str(faiss_tmp))
        _fsync_file(faiss_tmp)
        if cfg.meta_path.exists() and not created:
            shutil.copyfile(cfg.meta_path, meta_tmp)
        else:
            meta_tmp.write_text("", encoding="utf-8")
        with meta_tmp.open("a", encoding="utf-8") as fh:
            fh.writelines(new_lines)
        _fsync_file(meta_tmp)
    except BaseException:
        store.truncate(n_before)
        for p in (faiss_tmp, meta_tmp):
            p.unlink(missing_ok=True)
        raise

    raw = cfg.chunks_raw_path
    journal = {
        "job_id": job_id,
        "md5": md5,
        "processed_entry": processed_entry,
        "n_chunks_after": store.index.ntotal,
        "n_books": len(store.book_titles()),
        "raw_size_before": raw.stat().st_size if raw.exists() else 0,
        "raw_lines": new_lines,
    }
    _write_json_atomic(cfg.commit_journal_path, journal)
    if created:
        holder.set(store)
    _apply_journal(cfg, holder)


def _apply_journal(cfg: Config, holder: StoreHolder | None = None) -> None:
    journal = json.loads(cfg.commit_journal_path.read_text(encoding="utf-8"))
    for name in _INDEX_FILES:
        tmp = cfg.index_dir / f"{name}.tmp"
        if tmp.exists():
            os.replace(tmp, cfg.index_dir / name)

    raw = cfg.chunks_raw_path
    raw.touch(exist_ok=True)
    with raw.open("r+b") as fh:
        fh.truncate(journal["raw_size_before"])  # idempotente se a recuperacao rodar de novo
        fh.seek(0, os.SEEK_END)
        fh.write("".join(journal["raw_lines"]).encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())

    _set_processed(cfg, journal["md5"], journal["processed_entry"])
    _write_json_atomic(cfg.manifest_path, {
        "n_chunks": journal["n_chunks_after"],
        "n_books": journal["n_books"],
        "embed_model": cfg.embed_model,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    cfg.commit_journal_path.unlink()
    if holder is not None:
        holder.mark_committed()


def recover(cfg: Config) -> bool:
    """Conclui um commit interrompido depois do ponto de commit, ou descarta
    .tmp de um commit que nao chegou la. Devolve True se concluiu um commit."""
    if cfg.commit_journal_path.exists():
        _apply_journal(cfg)
        return True
    for name in _INDEX_FILES:
        (cfg.index_dir / f"{name}.tmp").unlink(missing_ok=True)
    return False
