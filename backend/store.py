"""Indice FAISS + metadados dos chunks."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from .config import CONFIG, Config
from .embed import embed_queries


@dataclass
class Chunk:
    id: str
    book: str          # titulo do livro de origem
    title: str         # titulo da receita / secao
    kind: str          # "receita" | "tecnica" | "texto"
    lang: str
    text: str
    ingredients: list[str]        # ingredientes detectados, para o filtro por ingrediente
    n_chars: int
    region: str = "nao_especificado"     # cozinha/regiao do LIVRO (herdado do metadata.py)
    dish_types: list[str] = field(default_factory=list)   # tipos de prato do LIVRO
    tags: list[str] = field(default_factory=list)         # outros atributos do LIVRO

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class Hit:
    score: float
    id: str
    book: str
    title: str
    kind: str
    lang: str
    text: str
    ingredients: list[str]
    region: str = "nao_especificado"
    dish_types: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def _fold(s: str) -> str:
    """Minuscula sem acento — para casar 'acucar' com 'açúcar'."""
    n = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in n if not unicodedata.combining(c))


class ChunkStore:
    """Indice FAISS de produto interno sobre vetores normalizados (= cosseno)."""

    def __init__(self, index, chunks: list[Chunk]):
        self.index = index
        self.chunks = chunks
        self._folded = [_fold(f"{c.title}\n{c.text}") for c in chunks]

    # ---------- persistencia ----------
    @classmethod
    def load(cls, cfg: Config = CONFIG) -> "ChunkStore":
        import faiss

        if not cfg.faiss_path.exists():
            raise FileNotFoundError(
                f"Indice nao encontrado em {cfg.faiss_path}. Rode primeiro:\n"
                f"  python -m backend.build_index"
            )
        index = faiss.read_index(str(cfg.faiss_path))
        chunks = [Chunk(**json.loads(l)) for l in cfg.meta_path.open(encoding="utf-8") if l.strip()]
        if index.ntotal != len(chunks):
            raise RuntimeError(
                f"Indice inconsistente: {index.ntotal} vetores mas {len(chunks)} chunks. "
                f"Reconstrua com: python -m backend.build_index --rebuild"
            )
        return cls(index, chunks)

    @staticmethod
    def create(dim: int):
        import faiss

        return faiss.IndexFlatIP(dim)

    @staticmethod
    def save(index, chunks: Iterable[Chunk], cfg: Config = CONFIG) -> None:
        import faiss

        cfg.index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(cfg.faiss_path))
        with cfg.meta_path.open("w", encoding="utf-8") as fh:
            for c in chunks:
                fh.write(c.to_json() + "\n")

    # ---------- busca ----------
    def search(
        self,
        query: str,
        k: int = 8,
        kind: str | None = None,
        ingredient: str | None = None,
        book: str | None = None,
        region: str | None = None,
        dish_type: str | None = None,
        oversample: int = 80,
    ) -> list[Hit]:
        """Busca semantica, com filtros opcionais aplicados sobre o candidato.

        `oversample` amplia a busca antes de filtrar, para que os filtros nao
        devolvam menos resultados do que o pedido.
        """
        if self.index.ntotal == 0:
            return []
        any_filter = kind or ingredient or book or region or dish_type
        want = k if not any_filter else min(k * oversample, self.index.ntotal)
        qv = embed_queries([query])
        scores, idxs = self.index.search(qv, min(want, self.index.ntotal))

        # "camarao, alho, limao" -> filtra chunks que mencionam TODOS os
        # ingredientes listados (AND), nao so um.
        ing_fs = [_fold(t) for t in re.split(r'[,;]', ingredient)] if ingredient else []
        ing_fs = [t.strip() for t in ing_fs if t.strip()]
        book_f = _fold(book) if book else None

        hits: list[Hit] = []
        for score, i in zip(scores[0], idxs[0]):
            if i < 0:
                continue
            c = self.chunks[i]
            if kind and c.kind != kind:
                continue
            if book_f and book_f not in _fold(c.book):
                continue
            if ing_fs and not all(t in self._folded[i] for t in ing_fs):
                continue
            if region and c.region != region:
                continue
            if dish_type and dish_type not in c.dish_types:
                continue
            hits.append(Hit(
                float(score), c.id, c.book, c.title, c.kind, c.lang, c.text,
                c.ingredients, c.region, c.dish_types, c.tags,
            ))
            if len(hits) >= k:
                break
        return hits

    # ---------- estatisticas p/ a UI ----------
    def stats(self) -> dict:
        books = {c.book for c in self.chunks}
        kinds: dict[str, int] = {}
        langs: dict[str, int] = {}
        regions: dict[str, int] = {}
        dish_types: dict[str, int] = {}
        for c in self.chunks:
            kinds[c.kind] = kinds.get(c.kind, 0) + 1
            langs[c.lang] = langs.get(c.lang, 0) + 1
            regions[c.region] = regions.get(c.region, 0) + 1
            for d in c.dish_types:
                dish_types[d] = dish_types.get(d, 0) + 1
        return {
            "chunks": len(self.chunks),
            "books": len(books),
            "kinds": dict(sorted(kinds.items(), key=lambda x: -x[1])),
            "langs": dict(sorted(langs.items(), key=lambda x: -x[1])),
            "regions": dict(sorted(regions.items(), key=lambda x: -x[1])),
            "dish_types": dict(sorted(dish_types.items(), key=lambda x: -x[1])),
        }

    def book_titles(self) -> list[str]:
        return sorted({c.book for c in self.chunks})

    def regions(self) -> list[str]:
        return sorted({c.region for c in self.chunks})

    def dish_type_options(self) -> list[str]:
        out: set[str] = set()
        for c in self.chunks:
            out.update(c.dish_types)
        return sorted(out)
