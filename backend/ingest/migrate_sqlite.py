"""Migra o historico da fila do SQLite (data/ingest/jobs.sqlite3) para o Postgres.

Roda sozinho na subida da API quando o arquivo antigo existe; tambem pode ser
chamado a mao:

    docker compose exec backend python -m backend.ingest.migrate_sqlite

Idempotente (ON CONFLICT DO NOTHING). Depois de copiar e conferir as contagens,
renomeia o arquivo para jobs.sqlite3.migrated, que fica como backup.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime, timezone

from ..core.config import CONFIG, Config
from .jobs import JobRepo

log = logging.getLogger("chef.ingest")

_BOOL = ("retryable", "cancel_requested")
_TIME = ("created_at", "updated_at", "finished_at")


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def migrate_sqlite(cfg: Config, repo: JobRepo) -> int:
    """Copia as linhas que faltam no Postgres. Devolve quantas foram inseridas."""
    src = cfg.legacy_jobs_db_path
    if not src.exists():
        return 0
    conn = sqlite3.connect(src)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM ingest_jobs")]
    finally:
        conn.close()

    inserted = 0
    with repo.pool.connection() as pg, pg.transaction():
        pg_cols = [r["column_name"] for r in pg.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'ingest_jobs' AND table_schema = current_schema()")]
        for row in rows:
            data = {k: v for k, v in row.items() if k in pg_cols}
            for k in _BOOL:
                if k in data:
                    data[k] = bool(data[k])
            for k in _TIME:
                if k in data:
                    data[k] = _ts(data[k])
            cols = ", ".join(data)
            cur = pg.execute(
                f"INSERT INTO ingest_jobs ({cols}) VALUES ({', '.join(['%s'] * len(data))})"
                " ON CONFLICT (id) DO NOTHING", list(data.values()),
            )
            inserted += cur.rowcount
        ids = [r["id"] for r in rows]
        present = pg.execute("SELECT count(*) AS n FROM ingest_jobs WHERE id = ANY(%s)", (ids,)).fetchone()["n"]
    if present != len(rows):
        raise RuntimeError(f"migracao incompleta: {present} de {len(rows)} linhas no Postgres")

    for suffix in ("", "-wal", "-shm"):
        part = src.with_name(src.name + suffix)
        if part.exists():
            part.rename(part.with_name(part.name.replace("jobs.sqlite3", "jobs.sqlite3.migrated")))
    log.warning("historico da fila migrado do SQLite: %d linhas (%d novas)", len(rows), inserted)
    return inserted


if __name__ == "__main__":
    had_legacy = CONFIG.legacy_jobs_db_path.exists()
    repo = JobRepo(CONFIG)
    try:
        n = migrate_sqlite(CONFIG, repo)
    finally:
        repo.close()
    print(f"{n} linhas inseridas" if had_legacy else "nada a migrar: jobs.sqlite3 nao existe")
    sys.exit(0)
