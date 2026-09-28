"""Pipeline completo: books/* -> books_md/ (limpo) -> chunks -> indice FAISS.

Aceita os mesmos formatos da aba "Adicionar livros" (ver formats.py): pdf,
epub, docx, odt, rtf, html, txt, md. O texto extraido de cada arquivo fica
em cache (data/ingest/text/<md5>.txt), entao um rebuild nao refaz OCR nem
extracao.

Roda em duas fases, para deixar a parte cara (limpeza + classificacao por
LLM) retomavel sem custo duplicado:

  Fase 1 (retomavel, chama o LLM por livro)
    books/* -> dedup -> extracao -> limpeza -> classificacao de tier ->
    descarta nao-culinario/tomos -> chunking -> metadados do livro (regiao,
    tipo de prato, tags) -> grava books_md/<livro>.md, data/metadata/<livro>.json
    e acrescenta os chunks a data/chunks_raw.jsonl. Um manifesto em
    data/cache/processed.json marca cada livro (por hash do arquivo) como
    concluido, para poder interromper e retomar sem reprocessar nem pagar
    de novo as chamadas de LLM. Livros adicionados pela aba ja estao nele.

  Fase 2 (idempotente, so custo local)
    le data/chunks_raw.jsonl inteiro, gera os embeddings e grava o indice
    FAISS + chunks.jsonl finais.

Uso:
    python -m backend.ingest.build_index                # tudo, com LLM de metadados
    python -m backend.ingest.build_index --no-llm        # metadados so por heuristica (gratis, rapido)
    python -m backend.ingest.build_index --limit 20       # so os 20 primeiros livros (teste)
    python -m backend.ingest.build_index --skip-embed     # so a fase 1
    python -m backend.ingest.build_index --rebuild        # ignora o cache e reprocessa tudo
    python -m backend.ingest.build_index --ocr            # faz OCR de PDFs escaneados (lento)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.config import CONFIG, Config
from ..search.store import Chunk, ChunkStore
from .commit import index_lock, load_processed, recover
from .extract import ExtractionError, cached_text, extract, store_text
from .extract.pdf import page_has_text, text_coverage
from .formats import normalize_ext
from .pipeline import MIN_RAW_CHARS, embed_text, file_md5, finalize, prepare, unique_slug

EMBED_BATCH_CHUNKS = 512
REBUILD_MIN_COVERAGE = 0.9
SKIPPED_NO_TEXT = "sem_texto"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _supported_files(cfg: Config) -> tuple[list[Path], list[Path]]:
    if not cfg.books_raw.is_dir():
        return [], []
    files = [p for p in cfg.books_raw.iterdir() if p.is_file() and not p.name.startswith(".")]
    ok = sorted(p for p in files if normalize_ext(p.name))
    other = [p for p in files if not normalize_ext(p.name)]
    return ok, other


def discover_and_dedupe(cfg: Config) -> list[Path]:
    """Lista os livros em formato suportado e remove duplicatas exatas
    (mesmo hash de conteudo), preferindo o nome sem prefixo 'Copy of'."""
    books, other = _supported_files(cfg)
    if other:
        _log(f"[aviso] {len(other)} arquivos em books/ ignorados (formato nao suportado): "
             f"{', '.join(p.name for p in other[:5])}{'...' if len(other) > 5 else ''}")

    groups: dict[str, list[Path]] = {}
    for p in books:
        if p.stat().st_size < MIN_RAW_CHARS:
            continue
        groups.setdefault(file_md5(p), []).append(p)

    chosen = []
    for paths in groups.values():
        paths.sort(key=lambda p: (p.name.startswith("Copy of "), len(p.name), p.name))
        chosen.append(paths[0])
    dup_count = sum(len(v) - 1 for v in groups.values())
    _log(f"[dedup] {len(books)} livros -> {len(chosen)} unicos ({dup_count} duplicatas exatas removidas)")
    return sorted(chosen)


def _save_manifest(cfg: Config, manifest: dict) -> None:
    p = cfg.processed_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class ProcessedBook:
    slug: str
    tier: str
    chunks: list[Chunk]

    @property
    def n_chunks(self) -> int:
        return len(self.chunks)


def book_text(path: Path, md5: str, cfg: Config, ocr: bool) -> str | None:
    """Texto do livro, do cache ou extraido. None = PDF escaneado sem --ocr."""
    text = cached_text(md5, cfg)
    if text is not None:
        return text
    ex = extract(path)
    if text_coverage(ex) == "no_text":
        if not ocr:
            return None
        from .extract.ocr import merge_pages, ocr_pages

        empty = [i for i, t in enumerate(ex.page_texts or []) if not page_has_text(t)]
        _log(f"  [ocr] {path.name}: {len(empty)} paginas")
        text = merge_pages(ex.page_texts or [], ocr_pages(path, empty, workers=cfg.ocr_workers))
    else:
        text = ex.text
    store_text(md5, text, cfg)
    return text


def _existing_titles(cfg: Config, manifest: dict) -> set[str]:
    titles = set()
    for entry in manifest.values():
        meta = cfg.index_dir / "metadata" / f"{entry.get('slug')}.json"
        if entry.get("slug") and meta.exists():
            titles.add(json.loads(meta.read_text(encoding="utf-8")).get("title") or entry["slug"])
    return titles


def process_book(path: Path, cfg: Config, use_llm: bool, *, md5: str | None = None,
                 taken: set[str] | None = None, taken_titles: set[str] | None = None,
                 ocr: bool = False) -> ProcessedBook | str | None:
    """Extrai, limpa, classifica, faz chunk e extrai metadados de um livro.

    Devolve None se o livro for descartado (vazio apos limpeza, ou tier
    nao-culinario/tomo de referencia) e SKIPPED_NO_TEXT para PDF escaneado
    sem --ocr.
    """
    md5 = md5 or file_md5(path)
    text = book_text(path, md5, cfg, ocr)
    if text is None:
        return SKIPPED_NO_TEXT
    prepared = prepare(text)
    if isinstance(prepared, str):
        return None
    taken = taken if taken is not None else set()
    taken_titles = taken_titles if taken_titles is not None else set()
    slug = unique_slug(path.name, md5, taken)
    taken.add(slug)
    meta, chunks = finalize(prepared, path.name, slug, cfg, use_llm, taken_titles=taken_titles)
    taken_titles.add(meta.title or slug)
    return ProcessedBook(slug=slug, tier=prepared.tier, chunks=chunks)


def rebuild_guard(cfg: Config) -> str | None:
    """Mensagem de recusa se um --rebuild apagaria livros cujos originais nao estao em books/."""
    books, _ = _supported_files(cfg)
    indexed = sum(1 for e in load_processed(cfg).values() if e.get("n_chunks", 0) > 0)
    if indexed and len(books) < REBUILD_MIN_COVERAGE * indexed:
        return (
            f"[rebuild] books/ tem {len(books)} arquivos, mas o acervo tem {indexed} livros indexados.\n"
            f"          Um rebuild agora apagaria {indexed - len(books)} livros cujos originais nao estao em books/.\n"
            f"          Coloque os originais em books/ ou use --force para continuar mesmo assim."
        )
    return None


def phase1(cfg: Config, use_llm: bool, limit: int | None, rebuild: bool, ocr: bool = False) -> Path:
    books = discover_and_dedupe(cfg)
    if limit:
        books = books[:limit]

    manifest = {} if rebuild else load_processed(cfg)
    taken = {e["slug"] for e in manifest.values() if e.get("slug")}
    taken_titles = _existing_titles(cfg, manifest)
    chunks_path = cfg.chunks_raw_path
    cfg.index_dir.mkdir(parents=True, exist_ok=True)
    if rebuild and chunks_path.exists():
        chunks_path.unlink()

    mode = "a" if chunks_path.exists() else "w"
    kept, skipped, errors, no_text = 0, 0, 0, 0
    t0 = time.time()
    with chunks_path.open(mode, encoding="utf-8") as out:
        for i, path in enumerate(books, 1):
            h = file_md5(path)
            if h in manifest:
                if manifest[h].get("n_chunks", 0) > 0:
                    kept += 1
                continue
            try:
                result = process_book(path, cfg, use_llm, md5=h, taken=taken, taken_titles=taken_titles, ocr=ocr)
            except Exception as e:
                reason = e.reason_code if isinstance(e, ExtractionError) else type(e).__name__
                _log(f"  [erro] {path.name}: {reason}: {e}")
                errors += 1
                manifest[h] = {"n_chunks": 0, "error": str(e)}
                _save_manifest(cfg, manifest)
                continue

            if result == SKIPPED_NO_TEXT:
                # fora do manifesto de proposito: uma execucao futura com --ocr pega o livro
                _log(f"[ocr] {path.name}: sem texto extraivel, pulado (use --ocr)")
                no_text += 1
                continue
            if result is None:
                skipped += 1
                manifest[h] = {"n_chunks": 0}
            else:
                for c in result.chunks:
                    out.write(c.to_json() + "\n")
                out.flush()
                kept += 1
                manifest[h] = {"n_chunks": result.n_chunks, "tier": result.tier, "slug": result.slug}

            _save_manifest(cfg, manifest)
            if i % 10 == 0 or i == len(books):
                dt = time.time() - t0
                _log(f"  [{i}/{len(books)}] mantidos={kept} descartados={skipped} erros={errors} ({dt:.0f}s)")

    extra = f", {no_text} sem texto (use --ocr)" if no_text else ""
    _log(f"[fase 1] concluida: {kept} livros indexados, {skipped} descartados, {errors} erros{extra}")
    return chunks_path


def phase2(cfg: Config, chunks_path: Path) -> None:
    from ..search.embed import embed_passages

    if not chunks_path.exists():
        raise FileNotFoundError(f"{chunks_path} nao existe — rode a fase 1 primeiro")

    chunks = [Chunk(**json.loads(l)) for l in chunks_path.open(encoding="utf-8") if l.strip()]
    _log(f"[fase 2] {len(chunks)} chunks para gerar embedding")
    if not chunks:
        _log("[fase 2] nada para indexar")
        return

    texts = [embed_text(c) for c in chunks]
    all_vecs = []
    t0 = time.time()
    for start in range(0, len(texts), EMBED_BATCH_CHUNKS):
        batch = texts[start:start + EMBED_BATCH_CHUNKS]
        vecs = embed_passages(batch, cfg, show_progress=False)
        all_vecs.append(vecs)
        done = min(start + EMBED_BATCH_CHUNKS, len(texts))
        dt = time.time() - t0
        rate = done / max(dt, 0.01)
        eta = (len(texts) - done) / max(rate, 0.01)
        _log(f"  [{done}/{len(texts)}] {rate:.0f} chunks/s, eta {eta:.0f}s")

    import numpy as np
    vectors = np.vstack(all_vecs)
    index = ChunkStore.create(vectors.shape[1])
    index.add(vectors)
    ChunkStore.save(index, chunks, cfg)

    manifest_summary = {
        "n_chunks": len(chunks),
        "n_books": len({c.book for c in chunks}),
        "embed_model": cfg.embed_model,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    cfg.manifest_path.write_text(json.dumps(manifest_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"[fase 2] indice salvo em {cfg.faiss_path} ({len(chunks)} vetores)")


def main(argv: list[str] | None = None, cfg: Config = CONFIG) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-llm", action="store_true", help="metadados so por heuristica, sem chamar o OpenRouter")
    ap.add_argument("--limit", type=int, default=None, help="processa so os N primeiros livros (teste)")
    ap.add_argument("--skip-embed", action="store_true", help="roda so a fase 1 (limpeza + metadados)")
    ap.add_argument("--rebuild", action="store_true", help="ignora o cache e reprocessa tudo do zero")
    ap.add_argument("--force", action="store_true",
                    help="com --rebuild, prossegue mesmo se books/ nao tiver os originais do acervo")
    ap.add_argument("--ocr", action="store_true", help="faz OCR de PDFs escaneados (lento)")
    args = ap.parse_args(argv)

    _log(f"books_raw={cfg.books_raw}  books_clean={cfg.books_clean}  index_dir={cfg.index_dir}")
    with index_lock(cfg, on_wait=lambda: _log("[lock] aguardando a API terminar um commit...")):
        if recover(cfg):
            _log("[recuperacao] commit interrompido da API concluido")
        if args.rebuild and not args.force:
            refusal = rebuild_guard(cfg)
            if refusal:
                _log(refusal)
                return 2
        chunks_path = phase1(cfg, use_llm=not args.no_llm, limit=args.limit, rebuild=args.rebuild, ocr=args.ocr)
        if not args.skip_embed:
            phase2(cfg, chunks_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
