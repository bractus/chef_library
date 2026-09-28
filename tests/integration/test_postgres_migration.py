import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.ingest.migrate_sqlite import migrate_sqlite

# schema do SQLite de antes do Postgres (datas em texto, booleanos como 0/1)
LEGACY_SCHEMA = """
CREATE TABLE ingest_jobs (
    id TEXT PRIMARY KEY, filename TEXT NOT NULL, format TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    file_md5 TEXT NOT NULL, status TEXT NOT NULL, stage TEXT, progress_current INTEGER,
    progress_total INTEGER, reason_code TEXT, reason_detail TEXT, retryable INTEGER NOT NULL DEFAULT 0,
    ocr_mode TEXT NOT NULL DEFAULT 'none', pages_total INTEGER, pages_without_text INTEGER,
    book_title TEXT, book_slug TEXT, lang TEXT, n_chunks INTEGER, duplicate_of TEXT, stored_path TEXT,
    staged_path TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, finished_at TEXT
);
"""


@pytest.fixture
def app(tmp_cfg, fake_embed, no_llm):
    from backend.api.main import app

    app.state.cfg = tmp_cfg
    yield app
    del app.state.cfg


def _legacy_db(cfg):
    cfg.ingest_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.legacy_jobs_db_path)
    conn.executescript(LEGACY_SCHEMA)
    conn.executemany(
        "INSERT INTO ingest_jobs (id, filename, format, size_bytes, file_md5, status, retryable, reason_code,"
        " book_title, lang, n_chunks, created_at, updated_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("a1", "Le Cordon Bleu.pdf", "pdf", 46224334, "m1", "added", 0, None, "Le Cordon Bleu", "pt", 459,
             "2026-09-27T17:45:27.296395Z", "2026-09-27T17:58:05.338005Z", "2026-09-27T17:58:05.338005Z"),
            ("a2", "Modernist No 2.pdf", "pdf", 404380907, "m2", "error", 1, "interrupted", None, None, None,
             "2026-09-27T18:04:03.228824Z", "2026-09-27T18:20:30.000000Z", None),
        ],
    )
    conn.commit()
    conn.close()


def test_migrates_legacy_history_once(app, tmp_cfg, repo):
    _legacy_db(tmp_cfg)
    with TestClient(app) as client:
        jobs = client.get("/api/ingest/jobs").json()["jobs"]
    assert [(j["id"], j["status"]) for j in jobs] == [("a2", "error"), ("a1", "added")]
    assert jobs[1]["result"]["n_chunks"] == 459 and jobs[1]["created_at"] == "2026-09-27T17:45:27.296395Z"
    assert jobs[0]["retryable"] is True and jobs[0]["actions"] == ["retry"]
    assert not tmp_cfg.legacy_jobs_db_path.exists()
    assert tmp_cfg.legacy_jobs_db_path.with_name("jobs.sqlite3.migrated").exists()
    assert migrate_sqlite(tmp_cfg, repo) == 0  # sem arquivo antigo: nada a fazer


def test_migration_is_idempotent(tmp_cfg, repo):
    _legacy_db(tmp_cfg)
    backup = tmp_cfg.ingest_dir / "copia.sqlite3"
    shutil.copy(tmp_cfg.legacy_jobs_db_path, backup)
    assert migrate_sqlite(tmp_cfg, repo) == 2
    shutil.copy(backup, tmp_cfg.legacy_jobs_db_path)
    assert migrate_sqlite(tmp_cfg, repo) == 0
    assert len(repo.list()) == 2


def test_committed_but_unmarked_book_is_recovered(app, tmp_cfg, repo, fixtures_dir):
    """Queda entre a gravacao no acervo e a marcacao como adicionado."""
    staged = tmp_cfg.uploads_dir / "j1" / "livro.pdf"
    staged.parent.mkdir(parents=True)
    shutil.copy(fixtures_dir["receitas.pdf"], staged)
    repo.create(job_id="j1", filename="livro.pdf", format="pdf", size_bytes=1, file_md5="md5-j1",
                staged_path=str(staged))
    repo.claim_next()
    tmp_cfg.processed_path.parent.mkdir(parents=True)
    tmp_cfg.processed_path.write_text(json.dumps({"md5-j1": {"n_chunks": 6, "tier": "receitas", "slug": "livro"}}))
    (tmp_cfg.index_dir / "metadata").mkdir(parents=True)
    (tmp_cfg.index_dir / "metadata" / "livro.json").write_text(json.dumps({"title": "Livro de Receitas", "lang": "pt"}))

    with TestClient(app) as client:
        job = client.get("/api/ingest/jobs/j1").json()
    assert job["status"] == "added" and job["result"]["book_title"] == "Livro de Receitas"
    assert (tmp_cfg.books_raw / "livro.pdf").exists() and not staged.parent.exists()
