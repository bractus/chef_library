"""Rotas /api/ingest/* (specs/001-book-ingestion-tab/contracts/ingest-api.md)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from ..core.config import Config
from ..ingest.commit import load_processed
from ..ingest.extract.ocr import ocr_available
from ..ingest.formats import config_payload, normalize_ext
from ..ingest.jobs import InvalidTransition, JobRepo, iso, to_api
from ..ingest.worker import IngestWorker
from ..search.store import StoreHolder

router = APIRouter(prefix="/api/ingest")

_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\]')
_BLOCK = 1 << 20


@dataclass
class IngestContext:
    cfg: Config
    repo: JobRepo
    worker: IngestWorker
    holder: StoreHolder


def _ctx(request: Request) -> IngestContext:
    return request.app.state.ingest


def safe_filename(name: str | None) -> str:
    """Nome usado em disco: nunca o nome cru do cliente (evita path traversal)."""
    base = Path((name or "").replace("\\", "/")).name
    base = _UNSAFE.sub("_", base).strip(" .")
    if len(base) > 200:
        stem, suffix = Path(base).stem, Path(base).suffix
        base = stem[: 200 - len(suffix)] + suffix
    return base or "livro"


def _known_title(md5: str, cfg: Config, repo: JobRepo) -> str | None:
    added = repo.find_added_by_md5(md5)
    if added:
        return added["book_title"]
    pending = repo.find_pending_by_md5(md5)
    if pending:
        return pending["filename"]
    slug = load_processed(cfg).get(md5, {}).get("slug")
    if slug:
        meta = cfg.index_dir / "metadata" / f"{slug}.json"
        if meta.exists():
            return json.loads(meta.read_text(encoding="utf-8")).get("title") or slug
        return slug
    return None


def _is_known_book(md5: str, cfg: Config, repo: JobRepo) -> bool:
    entry = load_processed(cfg).get(md5)
    return (
        bool(entry and entry.get("n_chunks", 0) > 0)
        or repo.find_added_by_md5(md5) is not None
        or repo.find_pending_by_md5(md5) is not None
    )


@router.get("/config")
def get_config(request: Request) -> dict:
    ctx = _ctx(request)
    return config_payload(enabled=ctx.cfg.ingest_enabled, ocr_available=ocr_available())


@router.post("/jobs", status_code=201)
async def upload(request: Request, files: list[UploadFile] = File(...)) -> dict:
    ctx = _ctx(request)
    if not ctx.cfg.ingest_enabled:
        raise HTTPException(status_code=409, detail="embeddings_unavailable")

    jobs, rejected = [], []
    seen_in_batch: set[str] = set()
    queued_any = False
    for upload_file in files:
        name = safe_filename(upload_file.filename)
        ext = normalize_ext(name)
        if ext is None:
            rejected.append({"filename": name, "reason_code": "unsupported_format"})
            continue

        job_id = uuid.uuid4().hex
        job_dir = ctx.cfg.uploads_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        dest = job_dir / name
        md5 = hashlib.md5()
        size = 0
        with dest.open("wb") as out:
            while block := await upload_file.read(_BLOCK):
                size += len(block)
                md5.update(block)
                out.write(block)
        await upload_file.close()

        digest = md5.hexdigest()
        if digest in seen_in_batch or _is_known_book(digest, ctx.cfg, ctx.repo):
            shutil.rmtree(job_dir, ignore_errors=True)
            job = ctx.repo.create(
                job_id=job_id, filename=name, format=ext, size_bytes=size, file_md5=digest,
                staged_path=None, status="duplicate", reason_code="duplicate_file",
                duplicate_of=_known_title(digest, ctx.cfg, ctx.repo),
            )
        else:
            job = ctx.repo.create(
                job_id=job_id, filename=name, format=ext, size_bytes=size, file_md5=digest,
                staged_path=str(dest),
            )
            queued_any = True
        seen_in_batch.add(digest)
        jobs.append(job)

    if queued_any:
        ctx.worker.wake()
    ocr = ocr_available()
    return {"jobs": [to_api(j, ocr) for j in jobs], "rejected": rejected}


@router.get("/jobs")
def list_jobs(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    before: Optional[datetime] = None,
    active: bool = False,
) -> dict:
    ctx = _ctx(request)
    if before is not None and before.tzinfo is None:
        before = before.replace(tzinfo=timezone.utc)
    rows = ctx.repo.list(limit=limit, before=before, active=active)
    ocr = ocr_available()
    return {
        "jobs": [to_api(j, ocr) for j in rows],
        "has_active": ctx.repo.has_active(),
        "next_before": iso(rows[-1]["created_at"]) if len(rows) == limit else None,
    }


def _get_or_404(ctx: IngestContext, job_id: str) -> dict:
    job = ctx.repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return job


def _act(request: Request, job_id: str, action: str) -> dict:
    ctx = _ctx(request)
    _get_or_404(ctx, job_id)
    if action == "ocr" and not ocr_available():
        raise HTTPException(status_code=409, detail="ocr_unavailable")
    try:
        job = ctx.repo.request(job_id, action)
    except InvalidTransition as e:
        raise HTTPException(status_code=409, detail="invalid_transition") from e
    ctx.worker.wake()
    return to_api(job, ocr_available())


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dict:
    return to_api(_get_or_404(_ctx(request), job_id), ocr_available())


@router.post("/jobs/{job_id}/retry")
def retry(request: Request, job_id: str) -> dict:
    return _act(request, job_id, "retry")


@router.post("/jobs/{job_id}/ocr")
def run_ocr(request: Request, job_id: str) -> dict:
    return _act(request, job_id, "ocr")


@router.post("/jobs/{job_id}/cancel")
def cancel(request: Request, job_id: str) -> dict:
    ctx = _ctx(request)
    _get_or_404(ctx, job_id)
    try:
        job = ctx.repo.cancel(job_id)
    except InvalidTransition as e:
        detail = "too_late" if str(e) == "too_late" else "invalid_transition"
        raise HTTPException(status_code=409, detail=detail) from e
    if job["status"] == "cancelled":
        shutil.rmtree(ctx.cfg.uploads_dir / job_id, ignore_errors=True)
    return to_api(job, ocr_available())


@router.post("/jobs/{job_id}/continue-without-ocr")
def continue_without_ocr(request: Request, job_id: str) -> dict:
    return _act(request, job_id, "continue_without_ocr")
