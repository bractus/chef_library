"""Etapas de processamento de um livro, compartilhadas pelo CLI (build_index)
e pelo worker da aba de ingestao — as regras de qualidade ficam num lugar so."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..core.config import Config
from ..core.text import fold
from ..search.store import Chunk
from .chunk import RawChunk, chunk_book
from .classify import TIER_NONCULINARY, profile_book
from .clean import GARBAGE_RATIO_DROP_CHUNK, clean_text, garbage_run_ratio
from .metadata import BookMeta, classify_book, save_book_meta

MIN_RAW_CHARS = 200


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def safe_slug(name: str) -> str:
    stem = Path(name).stem.replace("Copy of ", "").strip()
    slug = re.sub(r'[^\w\-. À-ÿ]', "_", stem)
    return slug[:150] or "livro"


def detect_lang(text: str) -> str:
    t = fold(text[:20000])
    scores = {
        "pt": len(re.findall(r'\b(que|nao|com|para|uma|voce|entao|acucar|colher|xicara|farinha|receita)\b', t)),
        "en": len(re.findall(r'\b(the|and|with|cup|tablespoon|flour|butter|until|recipe)\b', t)),
        "fr": len(re.findall(r'\b(les|dans|avec|une|beurre|farine|cuillere|faites|recette)\b', t)),
        "es": len(re.findall(r'\b(los|con|para|una|harina|cucharada|azucar|receta)\b', t)),
        "it": len(re.findall(r'\b(gli|con|della|una|farina|cucchiaio|zucchero|ricetta)\b', t)),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else "pt"


@dataclass
class Prepared:
    cleaned: str
    tier: str
    lang: str
    raw_chunks: list[RawChunk]


def prepare(text: str) -> Prepared | str:
    """Limpa, classifica e divide em trechos. Devolve o motivo do descarte
    ("too_little_text" | "reference_tome" | "non_culinary") quando o livro nao
    entra no acervo."""
    cleaned = clean_text(text)
    if len(cleaned) < MIN_RAW_CHARS:
        return "too_little_text"

    profile = profile_book(cleaned)
    if profile.tier == TIER_NONCULINARY:
        if profile.unreadable:
            return "too_little_text"
        return "reference_tome" if profile.reference_tome else "non_culinary"

    raw_chunks = chunk_book(cleaned, profile.tier)
    # descarta chunks individualmente ilegiveis (secoes com OCR de
    # caracteres transpostos — ver garbage_run_ratio), mesmo em livros
    # majoritariamente bons
    raw_chunks = [c for c in raw_chunks if garbage_run_ratio(c.text) <= GARBAGE_RATIO_DROP_CHUNK]
    if not raw_chunks:
        return "too_little_text"
    return Prepared(cleaned=cleaned, tier=profile.tier, lang=detect_lang(cleaned), raw_chunks=raw_chunks)


def unique_slug(filename: str, md5: str, taken: set[str]) -> str:
    """Slug que vira prefixo dos ids dos trechos e nome do metadata/<slug>.json;
    dois livros com o mesmo nome de arquivo nao podem se sobrescrever."""
    slug = safe_slug(filename)
    return f"{slug}-{md5[:6]}" if slug in taken else slug


def embed_text(rc: RawChunk | Chunk) -> str:
    """Texto que vira embedding — o mesmo formato da fase 2 do build_index."""
    return f"{rc.title}\n{rc.text}" if rc.title else rc.text


def distinct_label(title: str, filename: str, slug: str, taken_titles: set[str]) -> str:
    """O titulo e o rotulo do livro nas citacoes, nas estatisticas e no filtro
    por livro; um livro diferente com o mesmo titulo de outro ja no acervo
    ganha o nome do arquivo para nao se fundir com ele."""
    if title not in taken_titles:
        return title
    for candidate in (f"{title} [{filename}]", f"{title} [{slug}]"):
        if candidate not in taken_titles:
            return candidate
    return f"{title} [{slug}]"


def finalize(prepared: Prepared, filename: str, slug: str, cfg: Config, use_llm: bool, *,
             origin: str = "collection", added_at: str | None = None,
             taken_titles: set[str] | None = None) -> tuple[BookMeta, list[Chunk]]:
    """Grava o texto limpo e os metadados do livro e monta os Chunk finais."""
    cfg.books_clean.mkdir(parents=True, exist_ok=True)
    (cfg.books_clean / f"{slug}.md").write_text(prepared.cleaned, encoding="utf-8")

    raw_chunks = prepared.raw_chunks
    recipe_titles = [c.title for c in raw_chunks if c.kind == "receita"][:25]
    meta = classify_book(filename, prepared.cleaned, recipe_titles, prepared.lang, prepared.tier,
                         len(raw_chunks), cfg, use_llm=use_llm)
    meta.origin = origin
    meta.added_at = added_at
    if taken_titles is not None:
        meta.title = distinct_label(meta.title or slug, filename, slug, taken_titles)
    save_book_meta(meta, cfg.index_dir / "metadata", stem=slug)

    book_label = meta.title or slug
    chunks = [
        Chunk(
            id=f"{slug}::{i:04d}", book=book_label, title=rc.title, kind=rc.kind,
            lang=prepared.lang, text=rc.text, ingredients=rc.ingredients, n_chars=len(rc.text),
            region=meta.region, dish_types=meta.dish_types, tags=meta.tags,
        )
        for i, rc in enumerate(raw_chunks)
    ]
    return meta, chunks
