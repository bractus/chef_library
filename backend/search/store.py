"""Indice FAISS + metadados dos chunks."""
from __future__ import annotations

import bisect
import json
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from ..core.config import CONFIG, Config
from .embed import embed_queries
from ..core.text import fold


# busca hibrida (ver ChunkStore._fuse)
_WORD = re.compile(r"[a-z0-9]+")
_MIN_TERM_LEN = 4
_RARE_TERM_MAX_DF = 0.01   # termo em ate 1% dos trechos conta como especifico
_MAX_TF = 5                # ocorrencias de um termo num trecho que ainda contam
# palavras de pergunta que nunca sao especificas — nem vale conta-las no acervo
_STOPWORDS = frozenset("""
    como para fazer receita receitas qual quais onde quando porque sobre isso esse essa este esta
    completa completo simples facil rapida rapido melhor melhores tipo tipos modo preparo usar
    what which when where does with from have that this make recipe recipes best easy simple
    comment faire recette recettes avec pour dans quelle quel cette como hacer receta recetas
    come fare ricetta ricette della sono
""".split())
_FUSION_POOL = 200         # candidatos de cada ranking que entram na fusao
_RRF_K = 60                # constante padrao do reciprocal rank fusion


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


@dataclass(frozen=True)
class SearchFilters:
    """Os filtros da UI andam sempre juntos (busca, API e fallback do agente);
    mante-los num objeto so evita repetir a mesma lista de cinco parametros
    em cada camada."""

    kind: str | None = None
    ingredient: str | None = None
    book: str | None = None
    region: str | None = None
    dish_type: str | None = None

    @classmethod
    def from_request(
        cls,
        kind: str | None = None,
        ingredient: str | None = None,
        book: str | None = None,
        region: str | None = None,
        dish_type: str | None = None,
    ) -> "SearchFilters":
        """Constroi a partir de query params, tratando "" como ausente."""
        return cls(kind or None, ingredient or None, book or None,
                   region or None, dish_type or None)

    @property
    def active(self) -> bool:
        return any((self.kind, self.ingredient, self.book, self.region, self.dish_type))

    _LABELS = (
        ("ingredient", "ingrediente"),
        ("kind", "tipo"),
        ("dish_type", "tipo de prato"),
        ("book", "livro"),
        ("region", "regiao"),
    )

    def describe(self) -> str:
        """Resumo legivel dos filtros ativos, para dar ao agente o contexto
        que o usuario informou na UI."""
        return ", ".join(
            f"{label}: {getattr(self, attr)}"
            for attr, label in self._LABELS
            if getattr(self, attr)
        )


class _FilterMatcher:
    """Filtros ja normalizados (folding aplicado uma vez, nao por chunk)."""

    def __init__(self, filters: SearchFilters):
        self.f = filters
        # "camarao, alho, limao" -> exige TODOS os termos (AND), nao so um
        terms = re.split(r'[,;]', filters.ingredient) if filters.ingredient else []
        self.ingredients = [t for t in (fold(t).strip() for t in terms) if t]
        self.book = fold(filters.book) if filters.book else None

    def matches(self, chunk: "Chunk", folded_text: str) -> bool:
        f = self.f
        if f.kind and chunk.kind != f.kind:
            return False
        if self.book and self.book not in fold(chunk.book):
            return False
        if self.ingredients and not all(t in folded_text for t in self.ingredients):
            return False
        if f.region and chunk.region != f.region:
            return False
        if f.dish_type and f.dish_type not in chunk.dish_types:
            return False
        return True


class ChunkStore:
    """Indice FAISS de produto interno sobre vetores normalizados (= cosseno)."""

    def __init__(self, index, chunks: list[Chunk]):
        self.index = index
        self.chunks = chunks
        self._folded = [fold(f"{c.title}\n{c.text}") for c in chunks]
        # o worker de ingestao acrescenta livros enquanto a API responde buscas;
        # FAISS nao e seguro para ler durante um add
        self.lock = threading.RLock()
        self._blob: str | None = None
        self._blob_starts: list[int] = []
        self._term_cache: dict[str, dict[int, int] | None] = {}

    @classmethod
    def empty(cls, dim: int) -> "ChunkStore":
        return cls(cls.create(dim), [])

    def append(self, vectors: np.ndarray, chunks: list[Chunk]) -> None:
        with self.lock:
            self.index.add(vectors)
            self.chunks.extend(chunks)
            self._folded.extend(fold(f"{c.title}\n{c.text}") for c in chunks)
            self._term_cache.clear()

    def truncate(self, n: int) -> None:
        """Desfaz um append que nao chegou a ser persistido."""
        import faiss

        with self.lock:
            if self.index.ntotal > n:
                self.index.remove_ids(faiss.IDSelectorRange(n, self.index.ntotal))
            del self.chunks[n:]
            del self._folded[n:]
            self._term_cache.clear()

    # ---------- persistencia ----------
    @classmethod
    def load(cls, cfg: Config = CONFIG) -> "ChunkStore":
        import faiss

        if not cfg.faiss_path.exists():
            raise FileNotFoundError(
                f"Indice nao encontrado em {cfg.faiss_path}. Rode primeiro:\n"
                f"  python -m backend.ingest.build_index"
            )
        index = faiss.read_index(str(cfg.faiss_path))
        chunks = [Chunk(**json.loads(l)) for l in cfg.meta_path.open(encoding="utf-8") if l.strip()]
        if index.ntotal != len(chunks):
            raise RuntimeError(
                f"Indice inconsistente: {index.ntotal} vetores mas {len(chunks)} chunks. "
                f"Reconstrua com: python -m backend.ingest.build_index --rebuild"
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
        filters: SearchFilters | None = None,
        oversample: int = 80,
    ) -> list[Hit]:
        """Busca semantica, com filtros opcionais aplicados sobre o candidato.

        `oversample` amplia a busca antes de filtrar, para que os filtros nao
        devolvam menos resultados do que o pedido.
        """
        if self.index.ntotal == 0:
            return []
        filters = filters or SearchFilters()
        qv = embed_queries([query])  # chamada de rede: fora do lock

        matcher = _FilterMatcher(filters)
        hits: list[Hit] = []
        with self.lock:
            rare = self._rare_terms(query)
            want = k * oversample if filters.active else k
            if rare:
                want = max(want, _FUSION_POOL)
            scores, idxs = self.index.search(qv, min(want, self.index.ntotal))
            semantic = [(int(i), float(s)) for s, i in zip(scores[0], idxs[0]) if i >= 0]
            ranked = self._fuse(qv[0], semantic, rare) if rare else semantic
            for i, score in ranked:
                c = self.chunks[i]
                if not matcher.matches(c, self._folded[i]):
                    continue
                hits.append(Hit(
                    score, c.id, c.book, c.title, c.kind, c.lang, c.text,
                    c.ingredients, c.region, c.dish_types, c.tags,
                ))
                if len(hits) >= k:
                    break
        return hits

    def _text_blob(self) -> tuple[str, list[int]]:
        """Todos os textos numa string so, para contar/achar termos em C
        (str.count/str.find) em vez de percorrer 97 mil trechos em Python.
        Refeito na proxima busca depois de um append."""
        if self._blob is None or len(self._blob_starts) != len(self._folded):
            starts, pos = [], 0
            for f in self._folded:
                starts.append(pos)
                pos += len(f) + 1
            self._blob, self._blob_starts = "\x00".join(self._folded), starts
        return self._blob, self._blob_starts

    def _term_hits(self, term: str) -> dict[int, int] | None:
        """{trecho: ocorrencias} de um termo, ou None se ele for comum demais.
        Substring de proposito: "entremet" tambem casa "entremets"."""
        blob, starts = self._text_blob()
        cap = _RARE_TERM_MAX_DF * len(starts)
        if blob.count(term) > cap * _MAX_TF:  # comum demais: nem vale enumerar
            return None
        hits: dict[int, int] = {}
        pos = blob.find(term)
        while pos != -1:
            doc = bisect.bisect_right(starts, pos) - 1
            hits[doc] = hits.get(doc, 0) + 1
            pos = blob.find(term, pos + len(term))
        return hits if 0 < len(hits) <= cap else None

    def _rare_terms(self, query: str) -> list[tuple[float, dict[int, int]]]:
        """Termos da pergunta que aparecem em poucos trechos ("entremet",
        "tempura"): (idf, {trecho: ocorrencias}). Palavras comuns ("receita",
        "chocolate") ficam de fora e a busca continua so semantica."""
        n = len(self._folded)
        out = []
        for term in {t for t in _WORD.findall(fold(query)) if len(t) >= _MIN_TERM_LEN} - _STOPWORDS:
            if term not in self._term_cache:
                self._term_cache[term] = self._term_hits(term)
            hits = self._term_cache[term]
            if hits:
                out.append((math.log(n / len(hits)), hits))
        return out

    def _fuse(self, qvec: np.ndarray, semantic: list[tuple[int, float]],
              rare: list[tuple[float, dict[int, int]]]) -> list[tuple[int, float]]:
        """Busca hibrida: junta o ranking semantico com um ranking lexical dos
        termos raros por reciprocal rank fusion. Sem isso, palavras genericas
        da pergunta dominam o embedding e o termo especifico se perde — em
        "receita completa de entremet", so 2 dos 8 trechos falavam de entremet.
        O score devolvido continua sendo o cosseno, para a UI."""
        lexical: dict[int, float] = {}
        for idf, hits in rare:
            for i, tf in hits.items():
                lexical[i] = lexical.get(i, 0.0) + idf * min(tf, _MAX_TF)
        ids = np.fromiter(lexical, dtype="int64")
        cos = dict(zip(ids.tolist(), (self.index.reconstruct_batch(ids) @ qvec).tolist()))
        lex_ranked = sorted(lexical, key=lambda i: (-lexical[i], -cos[i]))[:_FUSION_POOL]

        fused: dict[int, float] = {}
        for rank, (i, _s) in enumerate(semantic):
            fused[i] = fused.get(i, 0.0) + 1.0 / (_RRF_K + rank)
        for rank, i in enumerate(lex_ranked):
            fused[i] = fused.get(i, 0.0) + 1.0 / (_RRF_K + rank)
        cos.update(semantic)
        return [(i, cos[i]) for i in sorted(fused, key=fused.get, reverse=True)]

    # ---------- estatisticas p/ a UI ----------
    def _tally(self, values: Iterable[str]) -> dict[str, int]:
        """Contagem ordenada do mais frequente para o menos."""
        return dict(Counter(values).most_common())

    def stats(self) -> dict:
        with self.lock:
            return {
                "chunks": len(self.chunks),
                "books": len({c.book for c in self.chunks}),
                "kinds": self._tally(c.kind for c in self.chunks),
                "langs": self._tally(c.lang for c in self.chunks),
                "regions": self._tally(c.region for c in self.chunks),
                "dish_types": self._tally(d for c in self.chunks for d in c.dish_types),
            }

    def book_titles(self) -> list[str]:
        with self.lock:
            return sorted({c.book for c in self.chunks})

    def regions(self) -> list[str]:
        with self.lock:
            return sorted({c.region for c in self.chunks})

    def dish_type_options(self) -> list[str]:
        out: set[str] = set()
        with self.lock:
            for c in self.chunks:
                out.update(c.dish_types)
        return sorted(out)

    def slugs(self) -> set[str]:
        with self.lock:
            return {c.id.split("::", 1)[0] for c in self.chunks}


class StoreHolder:
    """Referencia ao ChunkStore da API. O worker de ingestao acrescenta livros
    nele (ou o cria, com o acervo vazio), e um rebuild feito pelo CLI por fora
    e detectado pelo mtime de manifest.json e recarregado."""

    def __init__(self, cfg: Config = CONFIG):
        self.cfg = cfg
        self._store: ChunkStore | None = None
        self._load_lock = threading.Lock()
        self.loaded_manifest_mtime: float | None = None
        # durante um commit do worker o manifest muda antes de mark_committed();
        # recarregar ai criaria uma segunda copia inteira do indice em memoria
        self.committing = False

    def configure(self, cfg: Config) -> None:
        with self._load_lock:
            self.cfg = cfg
            self._store = None
            self.loaded_manifest_mtime = None

    def _manifest_mtime(self) -> float | None:
        p = self.cfg.manifest_path
        return p.stat().st_mtime if p.exists() else None

    def get(self) -> ChunkStore:
        """Carrega na primeira chamada; FileNotFoundError se nao houver indice."""
        with self._load_lock:
            if self._store is None:
                self._store = ChunkStore.load(self.cfg)
                self.loaded_manifest_mtime = self._manifest_mtime()
            return self._store

    def get_or_none(self) -> ChunkStore | None:
        try:
            return self.get()
        except FileNotFoundError:
            return None

    def set(self, store: ChunkStore) -> None:
        with self._load_lock:
            self._store = store

    def mark_committed(self) -> None:
        """Chamado depois de um commit do proprio worker, para nao recarregar a toa."""
        self.loaded_manifest_mtime = self._manifest_mtime()

    def reload_if_changed(self) -> None:
        if self.committing:
            return
        mtime = self._manifest_mtime()
        if self._store is None or mtime is None or mtime == self.loaded_manifest_mtime:
            return
        # solta a copia antiga ANTES de carregar a nova: com as duas em memoria
        # ao mesmo tempo (>4 GB) a VM do Docker mata o processo
        with self._load_lock:
            self._store = None
        self.get()


HOLDER = StoreHolder()
