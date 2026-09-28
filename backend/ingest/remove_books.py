"""Manutencao: remove livros do acervo (ex.: duplicatas) e limpa rotulos que so
tinham o sufixo " [arquivo]" por causa da colisao com o livro removido.

Remover livros nao faz parte da aba (Clarifications Q2 da spec); isto e uma
ferramenta de manutencao, para rodar com o backend PARADO (o indice e
reescrito inteiro e o backend carrega a versao nova ao subir):

    docker compose stop backend
    docker compose run --rm backend python -m backend.ingest.remove_books \\
        --title "Livro A" --title "Livro B" [--dry-run]
    docker compose start backend
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from ..core.config import CONFIG, Config
from ..search.store import Chunk, ChunkStore
from .commit import _fsync_file, _write_json_atomic, index_lock, load_processed, recover



def _log(msg: str) -> None:
    print(msg, flush=True)


def _rewrite_jsonl(path: Path, transform) -> int:
    """Reescreve um JSONL linha a linha (streaming). transform devolve a linha nova ou None para tirar."""
    tmp = path.with_name(path.name + ".tmp")
    removed = 0
    with path.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            out = transform(json.loads(line))
            if out is None:
                removed += 1
            else:
                dst.write(json.dumps(out, ensure_ascii=False) + "\n")
    _fsync_file(tmp)
    os.replace(tmp, path)
    return removed


def remove_books(cfg: Config, titles: list[str], dry_run: bool = False) -> dict:
    store = ChunkStore.load(cfg)
    books = set(store.book_titles())
    missing = [t for t in titles if t not in books]
    if missing:
        raise SystemExit(f"titulos nao encontrados no acervo: {missing}")
    remove = set(titles)

    drop = np.array([c.book in remove for c in store.chunks])
    removed_slugs = {c.id.split("::", 1)[0] for c, d in zip(store.chunks, drop) if d}
    remaining = books - remove
    # "Titulo [arquivo]" so ganhou o sufixo por colidir com "Titulo"; se "Titulo"
    # e um dos removidos, volta a ser "Titulo". Comparacao exata com o titulo
    # removido (nomes de arquivo tambem tem colchetes — regex quebrava aqui).
    relabel = {}
    for book in sorted(remaining):
        for title in titles:
            if book.startswith(f"{title} [") and book.endswith("]") and title not in relabel.values():
                relabel[book] = title
                break

    report = {
        "livros_removidos": sorted(remove),
        "trechos_removidos": int(drop.sum()),
        "slugs_removidos": sorted(removed_slugs),
        "rotulos_limpos": relabel,
        "trechos_antes": len(store.chunks),
        "trechos_depois": int((~drop).sum()),
        "livros_depois": len(remaining),
    }
    if dry_run:
        return report

    # indice: reconstroi so com os vetores mantidos (IndexFlat nao tem ids estaveis)
    keep = np.flatnonzero(~drop)
    vectors = store.index.reconstruct_n(0, store.index.ntotal)[keep]
    kept_chunks: list[Chunk] = []
    for i in keep.tolist():
        c = store.chunks[i]
        if c.book in relabel:
            c.book = relabel[c.book]
        kept_chunks.append(c)
    del store
    index = ChunkStore.create(vectors.shape[1])
    index.add(np.ascontiguousarray(vectors))

    import faiss

    faiss_tmp = cfg.index_dir / "chunks.faiss.tmp"
    meta_tmp = cfg.index_dir / "chunks.jsonl.tmp"
    faiss.write_index(index, str(faiss_tmp))
    _fsync_file(faiss_tmp)
    with meta_tmp.open("w", encoding="utf-8") as fh:
        fh.writelines(c.to_json() + "\n" for c in kept_chunks)
    _fsync_file(meta_tmp)
    os.replace(faiss_tmp, cfg.faiss_path)
    os.replace(meta_tmp, cfg.meta_path)

    def raw(c: dict) -> dict | None:
        if c["book"] in remove:
            return None
        c["book"] = relabel.get(c["book"], c["book"])
        return c

    if cfg.chunks_raw_path.exists():
        _rewrite_jsonl(cfg.chunks_raw_path, raw)

    # processed.json: o livro removido continua registrado (n_chunks=0), para o
    # CLI nao o reindexar se o arquivo original aparecer em books/
    processed = load_processed(cfg)
    for md5, entry in processed.items():
        if entry.get("slug") in removed_slugs:
            processed[md5] = {"n_chunks": 0, "removed": True, "slug": entry["slug"]}
    _write_json_atomic(cfg.processed_path, processed)

    meta_dir = cfg.index_dir / "metadata"
    for path in meta_dir.glob("*.json"):
        meta = json.loads(path.read_text(encoding="utf-8"))
        if meta.get("title") in remove:
            path.unlink()
        elif meta.get("title") in relabel:
            meta["title"] = relabel[meta["title"]]
            path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_json_atomic(cfg.manifest_path, {
        "n_chunks": len(kept_chunks),
        "n_books": len({c.book for c in kept_chunks}),
        "embed_model": cfg.embed_model,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    if relabel:  # historico da aba mostra o rotulo novo
        from .jobs import JobRepo

        repo = JobRepo(cfg, max_connections=1)
        try:
            with repo.pool.connection() as conn:
                for old, new in relabel.items():
                    conn.execute("UPDATE ingest_jobs SET book_title = %s WHERE book_title = %s", (new, old))
        finally:
            repo.close()

    check = ChunkStore.load(cfg)
    assert check.index.ntotal == len(check.chunks) == len(kept_chunks)
    return report


def main(argv: list[str] | None = None, cfg: Config = CONFIG) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", action="append", required=True, help="titulo exato do livro a remover (repetivel)")
    ap.add_argument("--dry-run", action="store_true", help="so mostra o que seria feito")
    args = ap.parse_args(argv)
    with index_lock(cfg, on_wait=lambda: _log("[lock] aguardando outro processo liberar o indice...")):
        recover(cfg)
        report = remove_books(cfg, args.title, dry_run=args.dry_run)
    _log(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
