"""Fila e historico da aba de ingestao (IngestJob, ver specs/001-book-ingestion-tab/data-model.md).

Postgres (servico "db" do docker-compose). A API atende cada requisicao numa
thread e o worker roda em outra, entao as conexoes vem de um pool; a
concorrencia fica com o banco (transacoes, FOR UPDATE, SKIP LOCKED).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..core.config import Config

STATUSES = ("queued", "processing", "added", "duplicate", "discarded", "no_text", "error", "cancelled")
# a partir da gravacao no indice o cancelamento nao e mais aceito
UNCANCELLABLE_STAGES = ("indexing", "waiting_for_index_lock")
ACTIVE = ("queued", "processing")
STAGES = ("waiting_for_index_lock", "extracting", "ocr", "cleaning", "chunking", "embedding",
          "checking_duplicates", "metadata", "indexing")
RETRYABLE_REASONS = {"embeddings_unavailable", "interrupted", "internal"}
OCR_MODES = ("none", "ocr", "skip_ocr")

# (de, para) permitidos; "new" = criacao
TRANSITIONS = {
    ("new", "queued"), ("new", "duplicate"),
    ("queued", "processing"),
    ("processing", "added"), ("processing", "duplicate"), ("processing", "discarded"),
    ("processing", "no_text"), ("processing", "error"),
    ("no_text", "queued"),
    ("error", "queued"),
    ("queued", "cancelled"), ("processing", "cancelled"), ("no_text", "cancelled"),
}


def _in_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)  # constantes do modulo, nunca entrada do usuario


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS ingest_jobs (
    id                 TEXT PRIMARY KEY,
    filename           TEXT NOT NULL,
    format             TEXT NOT NULL,
    size_bytes         BIGINT NOT NULL,
    file_md5           TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ({_in_list(STATUSES)})),
    stage              TEXT CHECK (stage IN ({_in_list(STAGES)})),
    progress_current   INTEGER,
    progress_total     INTEGER,
    reason_code        TEXT,
    reason_detail      TEXT,
    retryable          BOOLEAN NOT NULL DEFAULT FALSE,
    ocr_mode           TEXT NOT NULL DEFAULT 'none' CHECK (ocr_mode IN ({_in_list(OCR_MODES)})),
    pages_total        INTEGER,
    pages_without_text INTEGER,
    book_title         TEXT,
    book_slug          TEXT,
    lang               TEXT,
    n_chunks           INTEGER,
    duplicate_of       TEXT,
    stored_path        TEXT,
    staged_path        TEXT,
    cancel_requested   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL,
    finished_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_jobs_status_created ON ingest_jobs (status, created_at);
CREATE INDEX IF NOT EXISTS ix_jobs_created ON ingest_jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_jobs_md5 ON ingest_jobs (file_md5);
"""


class InvalidTransition(Exception):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if dt else None


class JobRepo:
    def __init__(self, cfg: Config, *, max_connections: int = 10):
        self.cfg = cfg
        self.pool = ConnectionPool(
            cfg.database_url, min_size=1, max_size=max_connections, open=True,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)

    def close(self) -> None:
        self.pool.close()

    def _one(self, sql: str, params=()) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone()

    # ---------- leitura ----------
    def get(self, job_id: str) -> dict | None:
        return self._one("SELECT * FROM ingest_jobs WHERE id = %s", (job_id,))

    def list(self, limit: int = 50, before: datetime | None = None, active: bool = False) -> list[dict]:
        sql = "SELECT * FROM ingest_jobs WHERE TRUE"
        params: list = []
        if before:
            sql += " AND created_at < %s"
            params.append(before)
        if active:
            sql += " AND status = ANY(%s)"
            params.append(list(ACTIVE))
        sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
        params.append(limit)
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def list_by_status(self, status: str) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM ingest_jobs WHERE status = %s ORDER BY created_at",
                                (status,)).fetchall()

    def has_active(self) -> bool:
        return self._one("SELECT 1 FROM ingest_jobs WHERE status = ANY(%s) LIMIT 1", (list(ACTIVE),)) is not None

    def find_added_by_md5(self, md5: str) -> dict | None:
        return self._find_by_md5(md5, ("added",))

    def find_pending_by_md5(self, md5: str) -> dict | None:
        """Mesmo arquivo ja na fila (ou pausado esperando OCR/retry)."""
        return self._find_by_md5(md5, ("queued", "processing", "no_text"))

    def _find_by_md5(self, md5: str, statuses: tuple[str, ...]) -> dict | None:
        return self._one(
            "SELECT * FROM ingest_jobs WHERE file_md5 = %s AND status = ANY(%s) ORDER BY created_at LIMIT 1",
            (md5, list(statuses)),
        )

    def cancel_requested(self, job_id: str) -> bool:
        row = self._one("SELECT cancel_requested FROM ingest_jobs WHERE id = %s", (job_id,))
        return bool(row and row["cancel_requested"])

    # ---------- escrita ----------
    def _transition(self, job_id: str, to: str, fields: dict) -> dict:
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("SELECT status FROM ingest_jobs WHERE id = %s FOR UPDATE", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if (row["status"], to) not in TRANSITIONS:
                raise InvalidTransition(f"{row['status']} -> {to}")
            fields = {**fields, "status": to, "updated_at": now()}
            cols = ", ".join(f"{k} = %s" for k in fields)
            return conn.execute(f"UPDATE ingest_jobs SET {cols} WHERE id = %s RETURNING *",
                                [*fields.values(), job_id]).fetchone()

    def create(self, *, filename: str, format: str, size_bytes: int, file_md5: str,
               staged_path: str | None, status: str = "queued", job_id: str | None = None,
               **fields) -> dict:
        if ("new", status) not in TRANSITIONS:
            raise InvalidTransition(f"new -> {status}")
        ts = now()
        row = {
            "id": job_id or uuid.uuid4().hex, "filename": filename, "format": format,
            "size_bytes": size_bytes, "file_md5": file_md5, "status": status,
            "staged_path": staged_path, "ocr_mode": "none", "retryable": False,
            "created_at": ts, "updated_at": ts,
            "finished_at": ts if status not in ACTIVE else None,
            **fields,
        }
        cols = ", ".join(row)
        with self.pool.connection() as conn:
            return conn.execute(
                f"INSERT INTO ingest_jobs ({cols}) VALUES ({', '.join(['%s'] * len(row))}) RETURNING *",
                list(row.values()),
            ).fetchone()

    def claim_next(self) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "UPDATE ingest_jobs SET status = 'processing', stage = NULL, progress_current = NULL,"
                " progress_total = NULL, reason_code = NULL, reason_detail = NULL, retryable = FALSE,"
                " updated_at = %s"
                " WHERE id = (SELECT id FROM ingest_jobs WHERE status = 'queued'"
                "             ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING *", (now(),),
            ).fetchone()

    def set_stage(self, job_id: str, stage: str, current: int | None = None, total: int | None = None) -> bool:
        """Avanca a etapa. Devolve False (sem mudar nada) se houver pedido de
        cancelamento — atomico com o cancel(), que recusa a etapa de gravacao."""
        if stage not in STAGES:
            raise ValueError(stage)
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE ingest_jobs SET stage = %s, progress_current = %s, progress_total = %s, updated_at = %s"
                " WHERE id = %s AND status = 'processing' AND NOT cancel_requested",
                (stage, current, total, now(), job_id),
            )
            return cur.rowcount == 1

    def update(self, job_id: str, **fields) -> None:
        """Campos informativos sem mudar o estado (ex.: pages_*, staged_path)."""
        if "status" in fields:
            raise ValueError("use finish/request para mudar o estado")
        fields["updated_at"] = now()
        cols = ", ".join(f"{k} = %s" for k in fields)
        with self.pool.connection() as conn:
            conn.execute(f"UPDATE ingest_jobs SET {cols} WHERE id = %s", [*fields.values(), job_id])

    def finish(self, job_id: str, status: str, **fields) -> dict:
        if status in ACTIVE:
            raise ValueError(status)
        retryable = status == "error" and fields.get("reason_code") in RETRYABLE_REASONS
        fields["retryable"] = bool(fields.get("retryable", retryable))
        fields.update(stage=None, progress_current=None, progress_total=None)
        if status != "no_text" and not (status == "error" and fields["retryable"]):
            fields["finished_at"] = now()
        return self._transition(job_id, status, fields)

    def request(self, job_id: str, action: str) -> dict:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if action not in job_actions(job, ocr_available=True) or action == "cancel":
            raise InvalidTransition(f"{action} em {job['status']}")
        fields: dict = {"reason_code": None, "reason_detail": None, "retryable": False, "finished_at": None}
        if action == "ocr":
            fields["ocr_mode"] = "ocr"
        elif action == "continue_without_ocr":
            fields["ocr_mode"] = "skip_ocr"
        return self._transition(job_id, "queued", fields)

    def cancel(self, job_id: str) -> dict:
        """Na fila ou parado em no_text: cancela na hora. Processando: pede ao
        worker, que para no proximo ponto de verificacao."""
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("SELECT status, stage FROM ingest_jobs WHERE id = %s FOR UPDATE",
                               (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            ts = now()
            if row["status"] in ("queued", "no_text"):
                sql = ("UPDATE ingest_jobs SET status = 'cancelled', finished_at = %s, updated_at = %s,"
                       " reason_code = NULL, retryable = FALSE WHERE id = %s RETURNING *")
                return conn.execute(sql, (ts, ts, job_id)).fetchone()
            if row["status"] == "processing":
                if row["stage"] in UNCANCELLABLE_STAGES:
                    raise InvalidTransition("too_late")
                sql = "UPDATE ingest_jobs SET cancel_requested = TRUE, updated_at = %s WHERE id = %s RETURNING *"
                return conn.execute(sql, (ts, job_id)).fetchone()
            raise InvalidTransition(f"cancelar em {row['status']}")

    def mark_interrupted(self) -> int:
        """Na subida da API: o que estava em processamento foi interrompido."""
        ts = now()
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE ingest_jobs SET status = 'cancelled', finished_at = %s, updated_at = %s, stage = NULL,"
                " progress_current = NULL, progress_total = NULL"
                " WHERE status = 'processing' AND cancel_requested", (ts, ts),
            )
            cur = conn.execute(
                "UPDATE ingest_jobs SET status = 'error', reason_code = 'interrupted', retryable = TRUE,"
                " stage = NULL, progress_current = NULL, progress_total = NULL, updated_at = %s"
                " WHERE status = 'processing'", (ts,),
            )
            return cur.rowcount


def job_actions(job: dict, ocr_available: bool) -> list[str]:
    status = job["status"]
    if status == "error" and job["retryable"]:
        return ["retry"]
    if status == "no_text":
        actions = ["ocr"] if ocr_available else []
        if job["reason_code"] == "partial_text":
            actions.append("continue_without_ocr")
        return actions + ["cancel"]
    if status == "queued":
        return ["cancel"]
    if status == "processing" and not job["cancel_requested"] and job["stage"] not in UNCANCELLABLE_STAGES:
        return ["cancel"]
    return []


def to_api(job: dict, ocr_available: bool) -> dict:
    progress = None
    if job["progress_total"]:
        progress = {"current": job["progress_current"] or 0, "total": job["progress_total"]}
    result = None
    if job["status"] == "added":
        result = {
            "book_title": job["book_title"], "lang": job["lang"], "n_chunks": job["n_chunks"],
            "pages_without_text": job["pages_without_text"],
        }
    return {
        "id": job["id"],
        "filename": job["filename"],
        "format": job["format"],
        "size_bytes": job["size_bytes"],
        "status": job["status"],
        "stage": job["stage"],
        "progress": progress,
        "reason_code": job["reason_code"],
        "reason_detail": job["reason_detail"],
        "retryable": bool(job["retryable"]),
        "cancel_requested": bool(job["cancel_requested"]),
        "actions": job_actions(job, ocr_available),
        "pages_total": job["pages_total"],
        "pages_without_text": job["pages_without_text"],
        "result": result,
        "duplicate_of": job["duplicate_of"],
        "created_at": iso(job["created_at"]),
        "updated_at": iso(job["updated_at"]),
        "finished_at": iso(job["finished_at"]),
    }
