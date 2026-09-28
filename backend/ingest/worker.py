"""Worker da fila de ingestao: uma thread no processo da API, um livro por vez.

Um so worker basta (as chamadas externas sao o gargalo e o commit no indice
e serializado de qualquer forma) e elimina conflito entre ingestoes. Por isso
a API precisa rodar com um unico processo uvicorn.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..core.config import Config
from ..search import embed
from ..search.store import StoreHolder
from . import dedup
from .commit import commit_book, load_processed, record_processed
from .extract import ExtractionError, extract, store_text
from .extract.pdf import page_has_text, text_coverage
from .jobs import RETRYABLE_REASONS, JobRepo
from .pipeline import embed_text, file_md5, finalize, prepare, unique_slug

log = logging.getLogger("chef.ingest")

EMBED_BATCH_CHUNKS = 512
_IDLE_WAIT_S = 2.0


class _Cancelled(Exception):
    pass


class _Discard(Exception):
    def __init__(self, status: str, **fields):
        super().__init__(status)
        self.status = status
        self.fields = fields


class IngestWorker(threading.Thread):
    def __init__(self, cfg: Config, repo: JobRepo, holder: StoreHolder):
        super().__init__(name="ingest-worker", daemon=True)
        self.cfg = cfg
        self.repo = repo
        self.holder = holder
        self._wake = threading.Event()
        self._stop = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            job = self.repo.claim_next()
            if job is None:
                self._wake.wait(_IDLE_WAIT_S)
                self._wake.clear()
                continue
            self.process(job)

    # ---------- um job ----------
    def process(self, job: dict) -> None:
        job_id = job["id"]
        try:
            self._process(job)
        except _Cancelled:
            self._finish(job_id, "cancelled")
        except _Discard as d:
            self._finish(job_id, d.status, **d.fields)
        except ExtractionError as e:
            self._finish(job_id, "error", reason_code=e.reason_code, reason_detail=str(e)[:500])
        except Exception as e:  # um livro com problema nunca derruba a fila
            log.exception("falha ao processar %s", job["filename"])
            self._finish(job_id, "error", reason_code="internal", reason_detail=f"{type(e).__name__}: {e}"[:500])

    def _finish(self, job_id: str, status: str, **fields) -> None:
        """Encerra o job. O staging e apagado ANTES de o estado final ficar
        visivel, quando o job nao puder mais ser retomado."""
        retryable = fields.get("retryable", fields.get("reason_code") in RETRYABLE_REASONS)
        resumable = status == "no_text" or (status == "error" and retryable)
        if not resumable:
            shutil.rmtree(self.cfg.uploads_dir / job_id, ignore_errors=True)
        self.repo.finish(job_id, status, **fields)

    def _stage(self, job_id: str, stage: str, current: int | None = None, total: int | None = None) -> None:
        """Avanca a etapa; e tambem o ponto em que um cancelamento e atendido."""
        if not self.repo.set_stage(job_id, stage, current, total):
            raise _Cancelled()

    def _staged(self, job: dict) -> Path:
        path = Path(job["staged_path"]) if job["staged_path"] else None
        if path is None or not path.exists():
            raise _Discard("error", reason_code="internal", retryable=0,
                           reason_detail="arquivo original nao encontrado")
        return path

    def _text(self, job: dict, path: Path) -> str:
        job_id = job["id"]
        self._stage(job_id, "extracting")
        ex = extract(path)
        if ex.pages_total is not None:
            self.repo.update(job_id, pages_total=ex.pages_total, pages_without_text=ex.pages_without_text)

        coverage = text_coverage(ex)
        mode = job["ocr_mode"]
        if coverage != "ok" and mode == "none":
            raise _Discard("no_text", reason_code=coverage,
                           pages_total=ex.pages_total, pages_without_text=ex.pages_without_text)
        if coverage != "ok" and mode == "ocr":
            from .extract.ocr import merge_pages, ocr_pages

            empty = [i for i, t in enumerate(ex.page_texts or []) if not page_has_text(t)]
            self._stage(job_id, "ocr", 0, len(empty))
            ocr_texts = ocr_pages(path, empty, lambda done, total: self._stage(job_id, "ocr", done, total),
                                  workers=self.cfg.ocr_workers)
            still_empty = sum(1 for i in empty if not page_has_text(ocr_texts.get(i, "")))
            self.repo.update(job_id, pages_without_text=still_empty)
            text = merge_pages(ex.page_texts or [], ocr_texts)
        else:
            text = ex.text
        store_text(job["file_md5"], text, self.cfg)
        return text

    def _embed(self, job_id: str, texts: list[str]) -> np.ndarray:
        self._stage(job_id, "embedding", 0, len(texts))
        parts = []
        try:
            for start in range(0, len(texts), EMBED_BATCH_CHUNKS):
                parts.append(embed.embed_passages(texts[start:start + EMBED_BATCH_CHUNKS], self.cfg))
                self._stage(job_id, "embedding", min(start + EMBED_BATCH_CHUNKS, len(texts)), len(texts))
        except RuntimeError as e:  # key ausente ou API fora do ar depois dos retries (search/embed.py)
            raise _Discard("error", reason_code="embeddings_unavailable", reason_detail=str(e)[:500]) from e
        return np.vstack(parts)

    def _process(self, job: dict) -> None:
        job_id, md5 = job["id"], job["file_md5"]
        path = self._staged(job)
        text = self._text(job, path)

        self._stage(job_id, "cleaning")
        prepared = prepare(text)
        if isinstance(prepared, str):
            record_processed(md5, {"n_chunks": 0}, self.cfg)
            raise _Discard("discarded", reason_code=prepared)
        self._stage(job_id, "chunking")

        vectors = self._embed(job_id, [embed_text(rc) for rc in prepared.raw_chunks])

        self._stage(job_id, "checking_duplicates")
        store = self.holder.get_or_none()
        same_as = dedup.find_duplicate(store, vectors, [rc.text for rc in prepared.raw_chunks])
        if same_as:
            record_processed(md5, {"n_chunks": 0}, self.cfg)
            raise _Discard("duplicate", reason_code="duplicate_content", duplicate_of=same_as)

        self._stage(job_id, "metadata")
        taken = (store.slugs() if store else set()) | {
            e["slug"] for e in load_processed(self.cfg).values() if e.get("slug")
        }
        slug = unique_slug(job["filename"], md5, taken)
        added_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta, chunks = finalize(prepared, job["filename"], slug, self.cfg, use_llm=True,
                                origin="upload", added_at=added_at,
                                taken_titles=set(store.book_titles()) if store else set())

        self._stage(job_id, "indexing")
        commit_book(
            self.holder, vectors, chunks, md5=md5, job_id=job_id, cfg=self.cfg,
            processed_entry={"n_chunks": len(chunks), "tier": prepared.tier, "slug": slug},
            # ja dentro da gravacao: aqui o cancelamento nao e mais atendido
            on_wait=lambda: self.repo.set_stage(job_id, "waiting_for_index_lock"),
        )
        # o livro ja esta no acervo: o historico reflete isso antes de mover o
        # original (copiar centenas de MB entre volumes leva segundos)
        self.repo.finish(job_id, "added", book_title=meta.title or slug, book_slug=slug,
                         lang=prepared.lang, n_chunks=len(chunks))
        self.store_original(self.repo.get(job_id))

    # ---------- recuperacao ----------
    def recover(self) -> None:
        """Na subida da API, antes de marcar os interrompidos: completa livros
        que chegaram a ser gravados no acervo mas nao foram marcados como
        adicionados, e move originais que ficaram no staging."""
        processed = load_processed(self.cfg)
        for job in self.repo.list_by_status("processing"):
            entry = processed.get(job["file_md5"])
            if not entry or entry.get("n_chunks", 0) <= 0:
                continue
            meta_path = self.cfg.index_dir / "metadata" / f"{entry['slug']}.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            self.repo.finish(job["id"], "added", book_title=meta.get("title") or entry["slug"],
                             book_slug=entry["slug"], lang=meta.get("lang"), n_chunks=entry["n_chunks"])
            log.warning("livro ja gravado no acervo marcado como adicionado: %s", job["filename"])
        for job in self.repo.list_by_status("added"):
            if job["staged_path"] and Path(job["staged_path"]).exists():
                self.store_original(job)

    # ---------- arquivos ----------
    def store_original(self, job: dict) -> None:
        """Move o original do staging para books/ (fonte de um rebuild pelo CLI)."""
        staged = Path(job["staged_path"])
        stored = self._move_to_books(staged, job["filename"], job["file_md5"])
        self.repo.update(job["id"], stored_path=stored, staged_path=None)
        shutil.rmtree(self.cfg.uploads_dir / job["id"], ignore_errors=True)

    def _move_to_books(self, staged: Path, filename: str, md5: str) -> str:
        """Move o original para books/, fonte de um rebuild pelo CLI."""
        books = self.cfg.books_raw
        books.mkdir(parents=True, exist_ok=True)
        target = books / filename
        if target.exists():
            if file_md5(target) != md5:
                target = books / f"{Path(filename).stem} ({md5[:6]}){Path(filename).suffix}"
        if not target.exists():
            shutil.move(str(staged), target)
        try:
            return str(target.relative_to(books.parent))
        except ValueError:
            return str(target)
