import threading

import pytest
from fastapi.testclient import TestClient

from backend.search.store import ChunkStore
from conftest import fake_vectors, wait_for

ACTIVE = {"queued", "processing"}


@pytest.fixture
def gate(monkeypatch):
    """Embedding que so termina quando o teste liberar — prende o job em 'embedding'."""
    import backend.search.embed as embed

    entered, release = threading.Event(), threading.Event()

    def slow(texts, cfg=None, batch_size=None, show_progress=False):
        entered.set()
        release.wait(10)
        return fake_vectors(list(texts))

    monkeypatch.setattr(embed, "embed_passages", slow)
    yield entered, release
    release.set()


@pytest.fixture
def client(tmp_cfg, fake_embed, no_llm):
    from backend.api.main import app

    app.state.cfg = tmp_cfg
    with TestClient(app) as c:
        yield c
    del app.state.cfg


def _upload(client, path):
    res = client.post("/api/ingest/jobs", files=[("files", (path.name, path.read_bytes(), "application/octet-stream"))])
    return res.json()["jobs"][0]


def _job(client, job_id):
    return client.get(f"/api/ingest/jobs/{job_id}").json()


def test_cancel_while_processing(client, tmp_cfg, fixtures_dir, gate):
    entered, release = gate
    job = _upload(client, fixtures_dir["receitas.docx"])
    assert entered.wait(10)
    assert _job(client, job["id"])["actions"] == ["cancel"]

    res = client.post(f"/api/ingest/jobs/{job['id']}/cancel")
    assert res.status_code == 200 and res.json()["cancel_requested"] is True
    assert res.json()["actions"] == []
    release.set()

    done = wait_for(lambda: (j := _job(client, job["id"]))["status"] not in ACTIVE and j)
    assert done["status"] == "cancelled" and done["finished_at"]
    assert not (tmp_cfg.uploads_dir / job["id"]).exists()
    assert not tmp_cfg.faiss_path.exists()  # nada entrou no acervo
    assert not (tmp_cfg.books_raw / "receitas.docx").exists()


def test_cancel_queued_job(client, tmp_cfg, fixtures_dir, gate):
    entered, release = gate
    first = _upload(client, fixtures_dir["receitas.docx"])
    assert entered.wait(10)
    second = _upload(client, fixtures_dir["receitas.odt"])  # fica na fila atras do primeiro
    res = client.post(f"/api/ingest/jobs/{second['id']}/cancel").json()
    assert res["status"] == "cancelled"
    assert not (tmp_cfg.uploads_dir / second["id"]).exists()
    release.set()
    wait_for(lambda: _job(client, first["id"])["status"] == "added")
    assert len(ChunkStore.load(tmp_cfg).book_titles()) == 1


def test_cancel_no_text_cleans_staging(client, tmp_cfg, fixtures_dir):
    job = _upload(client, fixtures_dir["escaneado.pdf"])
    wait_for(lambda: _job(client, job["id"])["status"] == "no_text")
    assert (tmp_cfg.uploads_dir / job["id"]).exists()
    assert client.post(f"/api/ingest/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert not (tmp_cfg.uploads_dir / job["id"]).exists()


def test_cannot_cancel_finished_or_indexing(client, tmp_cfg, fixtures_dir, repo):
    job = _upload(client, fixtures_dir["receitas.txt"])
    wait_for(lambda: _job(client, job["id"])["status"] == "added")
    res = client.post(f"/api/ingest/jobs/{job['id']}/cancel")
    assert res.status_code == 409 and res.json()["detail"] == "invalid_transition"

    other = repo.create(filename="x.txt", format="txt", size_bytes=1, file_md5="zz", staged_path=None)
    repo.claim_next()
    repo.set_stage(other["id"], "indexing")
    res = client.post(f"/api/ingest/jobs/{other['id']}/cancel")
    assert res.status_code == 409 and res.json()["detail"] == "too_late"


def test_restart_with_pending_cancel_ends_cancelled(tmp_cfg, repo):
    job = repo.create(filename="x.txt", format="txt", size_bytes=1, file_md5="m", staged_path=None)
    repo.claim_next()
    repo.cancel(job["id"])
    repo.mark_interrupted()
    assert repo.get(job["id"])["status"] == "cancelled"
