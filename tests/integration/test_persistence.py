import shutil

import pytest
from fastapi.testclient import TestClient

from backend.search.store import ChunkStore
from conftest import wait_for
from make_fixtures import book_text, recipe_book

ACTIVE = {"queued", "processing"}


@pytest.fixture
def app(tmp_cfg, fake_embed, no_llm):
    from backend.api.main import app

    app.state.cfg = tmp_cfg
    yield app
    del app.state.cfg


def _wait(client, job_id):
    return wait_for(lambda: (j := client.get(f"/api/ingest/jobs/{job_id}").json())["status"] not in ACTIVE and j)


def _upload(client, name, data):
    return client.post("/api/ingest/jobs", files=[("files", (name, data, "application/octet-stream"))]).json()["jobs"][0]


def test_added_book_survives_restart(app, tmp_cfg, fixtures_dir):
    with TestClient(app) as client:
        job = _wait(client, _upload(client, "receitas.docx", fixtures_dir["receitas.docx"].read_bytes())["id"])
        assert job["status"] == "added"
        assert not (tmp_cfg.uploads_dir / job["id"]).exists()  # staging limpo

    assert (tmp_cfg.books_raw / "receitas.docx").exists()
    assert len(ChunkStore.load(tmp_cfg).chunks) == 6

    with TestClient(app) as client:  # "reinicio"
        assert client.get("/api/stats").json()["books"] == 1
        history = client.get("/api/ingest/jobs").json()["jobs"]
        assert [j["status"] for j in history] == ["added"]


def test_interrupted_job_can_be_retried(app, tmp_cfg, fixtures_dir, repo):
    staged = tmp_cfg.uploads_dir / "job1" / "receitas.odt"
    staged.parent.mkdir(parents=True)
    shutil.copy(fixtures_dir["receitas.odt"], staged)
    repo.create(job_id="job1", filename="receitas.odt", format="odt", size_bytes=staged.stat().st_size,
                file_md5="m1", staged_path=str(staged))
    repo.claim_next()  # ficou "processing" quando o processo morreu

    with TestClient(app) as client:
        job = client.get("/api/ingest/jobs/job1").json()
        assert (job["status"], job["reason_code"], job["actions"]) == ("error", "interrupted", ["retry"])
        assert client.post("/api/ingest/jobs/job1/retry").json()["status"] == "queued"
        assert _wait(client, "job1")["status"] == "added"


def test_same_name_different_content(app, tmp_cfg, fixtures_dir):
    with TestClient(app) as client:
        a = _wait(client, _upload(client, "livro.pdf", fixtures_dir["receitas.pdf"].read_bytes())["id"])
        b = _wait(client, _upload(client, "livro.pdf", fixtures_dir["parcial.pdf"].read_bytes())["id"])
        if b["status"] == "no_text":  # parcial.pdf pede decisao sobre OCR
            client.post(f"/api/ingest/jobs/{b['id']}/continue-without-ocr")
            b = _wait(client, b["id"])
    # parcial.pdf tem as mesmas paginas de texto de receitas.pdf -> duplicata por conteudo
    assert a["status"] == "added" and b["status"] == "duplicate"

    with TestClient(app) as client:
        c = _wait(client, _upload(client, "livro.pdf", fixtures_dir["receitas.docx"].read_bytes())["id"])
    # extensao .pdf com conteudo docx: corrompido — o nome repetido nao sobrescreve nada
    assert c["status"] == "error" and c["reason_code"] == "corrupt"
    assert sorted(p.name for p in tmp_cfg.books_raw.iterdir()) == ["livro.pdf"]


def test_same_name_two_real_books_coexist(app, tmp_cfg):
    first, second = (book_text(recipe_book(seed)).encode("utf-8") for seed in (20, 21))
    with TestClient(app) as client:
        a = _wait(client, _upload(client, "livro.txt", first)["id"])
        b = _wait(client, _upload(client, "livro.txt", second)["id"])
    assert a["status"] == b["status"] == "added"
    names = sorted(p.name for p in tmp_cfg.books_raw.iterdir())
    assert len(names) == 2 and "livro.txt" in names
    store = ChunkStore.load(tmp_cfg)
    assert len(store.slugs()) == 2
    assert sorted(store.book_titles()) == ["livro", "livro [livro.txt]"]
