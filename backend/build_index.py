"""Pipeline completo: books/*.md -> books_md/ (limpo) -> chunks -> indice FAISS.

Roda em duas fases, para deixar a parte cara (limpeza + classificacao por
LLM) retomavel sem custo duplicado:

  Fase 1 (retomavel, chama o LLM por livro)
    books/*.md -> dedup -> limpeza -> classificacao de tier -> descarta
    nao-culinario/tomos -> chunking -> metadados do livro (regiao, tipo de
    prato, tags) -> grava books_md/<livro>.md, data/metadata/<livro>.json e
    acrescenta os chunks a data/chunks_raw.jsonl. Um manifesto em
    data/cache/processed.json marca cada livro (por hash do conteudo) como
    concluido, para poder interromper e retomar sem reprocessar nem pagar
    de novo as chamadas de LLM.

  Fase 2 (idempotente, so custo local)
    le data/chunks_raw.jsonl inteiro, gera os embeddings (E5 local, GPU
    MPS se disponivel) e grava o indice FAISS + chunks.jsonl finais.

Uso:
    python -m backend.build_index                # tudo, com LLM de metadados
    python -m backend.build_index --no-llm        # metadados so por heuristica (gratis, rapido)
    python -m backend.build_index --limit 20       # so os 20 primeiros livros (teste)
    python -m backend.build_index --skip-embed     # so a fase 1
    python -m backend.build_index --rebuild         # ignora o cache e reprocessa tudo
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

from .classify import TIER_NONCULINARY, TIER_RECIPES, profile_book
from .chunk import chunk_book
from .clean import clean_text, garbage_run_ratio
from .config import CONFIG, Config
from .metadata import classify_book, save_book_meta
from .store import Chunk, ChunkStore

MIN_RAW_CHARS = 200


def _log(msg: str) -> None:
    print(msg, flush=True)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _safe_slug(name: str) -> str:
    stem = Path(name).stem.replace("Copy of ", "").strip()
    slug = re.sub(r'[^\w\-. À-ÿ]', "_", stem)
    return slug[:150] or "livro"


def _fold(s: str) -> str:
    n = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in n if not unicodedata.combining(c))


def _detect_lang(text: str) -> str:
    t = _fold(text[:20000])
    scores = {
        "pt": len(re.findall(r'\b(que|nao|com|para|uma|voce|entao|acucar|colher|xicara|farinha|receita)\b', t)),
        "en": len(re.findall(r'\b(the|and|with|cup|tablespoon|flour|butter|until|recipe)\b', t)),
        "fr": len(re.findall(r'\b(les|dans|avec|une|beurre|farine|cuillere|faites|recette)\b', t)),
        "es": len(re.findall(r'\b(los|con|para|una|harina|cucharada|azucar|receta)\b', t)),
        "it": len(re.findall(r'\b(gli|con|della|una|farina|cucchiaio|zucchero|ricetta)\b', t)),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else "pt"


def discover_and_dedupe(cfg: Config) -> list[Path]:
    """Lista books/*.md, ignora nao-markdown, e remove duplicatas exatas
    (mesmo hash de conteudo), preferindo o nome sem prefixo 'Copy of'."""
    all_md = sorted(cfg.books_raw.glob("*.md"))
    non_md = [p for p in cfg.books_raw.iterdir() if p.is_file() and p.suffix.lower() != ".md"]
    if non_md:
        _log(f"[aviso] {len(non_md)} arquivos nao-markdown em books/ ignorados "
             f"(precisam de conversao separada para .md): "
             f"{', '.join(p.name for p in non_md[:5])}{'...' if len(non_md) > 5 else ''}")

    groups: dict[str, list[Path]] = {}
    for p in all_md:
        if p.stat().st_size < MIN_RAW_CHARS:
            continue
        groups.setdefault(_md5(p), []).append(p)

    chosen = []
    for paths in groups.values():
        paths.sort(key=lambda p: (p.name.startswith("Copy of "), len(p.name), p.name))
        chosen.append(paths[0])
    dup_count = sum(len(v) - 1 for v in groups.values())
    _log(f"[dedup] {len(all_md)} .md -> {len(chosen)} unicos ({dup_count} duplicatas exatas removidas)")
    return sorted(chosen)


def _manifest_path(cfg: Config) -> Path:
    return cfg.index_dir / "cache" / "processed.json"


def _load_manifest(cfg: Config) -> dict:
    p = _manifest_path(cfg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_manifest(cfg: Config, manifest: dict) -> None:
    p = _manifest_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def process_book(path: Path, cfg: Config, use_llm: bool) -> dict | None:
    """Limpa, classifica, faz chunk e extrai metadados de um livro.

    Devolve None se o livro for descartado (vazio apos limpeza, ou tier
    nao-culinario/tomo de referencia).
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned = clean_text(raw)
    if len(cleaned) < MIN_RAW_CHARS:
        return None

    profile = profile_book(cleaned)
    if profile.tier == TIER_NONCULINARY:
        return None

    lang = _detect_lang(cleaned)
    raw_chunks = chunk_book(cleaned, profile.tier)
    # descarta chunks individualmente ilegiveis (secoes com OCR de
    # caracteres transpostos — ver garbage_run_ratio), mesmo em livros
    # majoritariamente bons
    raw_chunks = [c for c in raw_chunks if garbage_run_ratio(c.text) <= 0.20]
    if not raw_chunks:
        return None

    slug = _safe_slug(path.name)
    (cfg.books_clean).mkdir(parents=True, exist_ok=True)
    (cfg.books_clean / f"{slug}.md").write_text(cleaned, encoding="utf-8")

    recipe_titles = [c.title for c in raw_chunks if c.kind == "receita"][:25]
    meta = classify_book(path.name, cleaned, recipe_titles, lang, profile.tier,
                          len(raw_chunks), cfg, use_llm=use_llm)
    save_book_meta(meta, cfg.index_dir / "metadata")

    book_label = meta.title or slug
    chunks = []
    for i, rc in enumerate(raw_chunks):
        chunks.append(Chunk(
            id=f"{slug}::{i:04d}", book=book_label, title=rc.title, kind=rc.kind,
            lang=lang, text=rc.text, ingredients=rc.ingredients, n_chars=len(rc.text),
            region=meta.region, dish_types=meta.dish_types, tags=meta.tags,
        ))
    return {"slug": slug, "tier": profile.tier, "n_chunks": len(chunks), "chunks": chunks}


def phase1(cfg: Config, use_llm: bool, limit: int | None, rebuild: bool) -> Path:
    books = discover_and_dedupe(cfg)
    if limit:
        books = books[:limit]

    manifest = {} if rebuild else _load_manifest(cfg)
    chunks_path = cfg.index_dir / "chunks_raw.jsonl"
    cfg.index_dir.mkdir(parents=True, exist_ok=True)
    if rebuild and chunks_path.exists():
        chunks_path.unlink()

    mode = "a" if chunks_path.exists() else "w"
    kept, skipped, errors = 0, 0, 0
    t0 = time.time()
    with chunks_path.open(mode, encoding="utf-8") as out:
        for i, path in enumerate(books, 1):
            h = _md5(path)
            if h in manifest:
                kept += manifest[h].get("n_chunks", 0) > 0
                continue
            try:
                result = process_book(path, cfg, use_llm)
            except Exception as e:
                _log(f"  [erro] {path.name}: {type(e).__name__}: {e}")
                errors += 1
                manifest[h] = {"n_chunks": 0, "error": str(e)}
                _save_manifest(cfg, manifest)
                continue

            if result is None:
                skipped += 1
                manifest[h] = {"n_chunks": 0}
            else:
                for c in result["chunks"]:
                    out.write(c.to_json() + "\n")
                out.flush()
                kept += 1
                manifest[h] = {"n_chunks": result["n_chunks"], "tier": result["tier"], "slug": result["slug"]}

            _save_manifest(cfg, manifest)
            if i % 10 == 0 or i == len(books):
                dt = time.time() - t0
                _log(f"  [{i}/{len(books)}] mantidos={kept} descartados={skipped} erros={errors} ({dt:.0f}s)")

    _log(f"[fase 1] concluida: {kept} livros indexados, {skipped} descartados, {errors} erros")
    return chunks_path


def phase2(cfg: Config, chunks_path: Path) -> None:
    from .embed import embed_passages

    if not chunks_path.exists():
        raise FileNotFoundError(f"{chunks_path} nao existe — rode a fase 1 primeiro")

    chunks = [Chunk(**json.loads(l)) for l in chunks_path.open(encoding="utf-8") if l.strip()]
    _log(f"[fase 2] {len(chunks)} chunks para gerar embedding")
    if not chunks:
        _log("[fase 2] nada para indexar")
        return

    texts = [f"{c.title}\n{c.text}" if c.title else c.text for c in chunks]
    BATCH = 512
    all_vecs = []
    t0 = time.time()
    for start in range(0, len(texts), BATCH):
        batch = texts[start:start + BATCH]
        vecs = embed_passages(batch, cfg, show_progress=False)
        all_vecs.append(vecs)
        done = min(start + BATCH, len(texts))
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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-llm", action="store_true", help="metadados so por heuristica, sem chamar o OpenRouter")
    ap.add_argument("--limit", type=int, default=None, help="processa so os N primeiros livros (teste)")
    ap.add_argument("--skip-embed", action="store_true", help="roda so a fase 1 (limpeza + metadados)")
    ap.add_argument("--rebuild", action="store_true", help="ignora o cache e reprocessa tudo do zero")
    args = ap.parse_args(argv)

    cfg = CONFIG
    _log(f"books_raw={cfg.books_raw}  books_clean={cfg.books_clean}  index_dir={cfg.index_dir}")
    chunks_path = phase1(cfg, use_llm=not args.no_llm, limit=args.limit, rebuild=args.rebuild)
    if not args.skip_embed:
        phase2(cfg, chunks_path)


if __name__ == "__main__":
    main()
