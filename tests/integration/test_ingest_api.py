import dataclasses

import pytest
from fastapi.testclient import TestClient

from backend.search.store import HOLDER
from conftest import wait_for
from make_fixtures import recipe_book

ACTIVE = {"queued", "processing"}


@pytest.fixture
def make_client(fake_embed, no_llm):
    from backend.api.main import app

    def _make(cfg):
        app.state.cfg = cfg
        return TestClient(app)

    yield _make
    if hasattr(app.state, "cfg"):
        del app.state.cfg


@pytest.fixture
def client(make_client, tmp_cfg):
    with make_client(tmp_cfg) as c:
        yield c


def upload(client, *paths_or_pairs):
    files = []
    for item in paths_or_pairs:
        name, data = (item.name, item.read_bytes()) if hasattr(item, "read_bytes") else item
        files.append(("files", (name, data, "application/octet-stream")))
    res = client.post("/api/ingest/jobs", files=files)
    assert res.status_code == 201, res.text
    return res.json()


def wait_done(client, job_id):
    def done():
        job = client.get(f"/api/ingest/jobs/{job_id}").json()
        return job if job["status"] not in ACTIVE else None
    return wait_for(done)


def test_config(client, make_client, tmp_cfg):
    body = client.get("/api/ingest/config").json()
    assert body["enabled"] is True and body["disabled_reason"] is None
    assert "max_file_bytes" not in body  # sem limite de tamanho
    assert {f["ext"] for f in body["formats"]} == {"pdf", "epub", "docx", "odt", "rtf", "html", "txt", "md"}
    assert next(f for f in body["formats"] if f["ext"] == "html")["aliases"] == ["htm"]


def test_disabled_without_key(make_client, tmp_cfg, fixtures_dir):
    with make_client(dataclasses.replace(tmp_cfg, openrouter_key="")) as c:
        body = c.get("/api/ingest/config").json()
        assert body["enabled"] is False and body["disabled_reason"] == "embeddings_unavailable"
        res = c.post("/api/ingest/jobs", files=[("files", ("a.txt", b"x", "text/plain"))])
        assert res.status_code == 409 and res.json()["detail"] == "embeddings_unavailable"


def test_upload_without_files_is_422(client):
    assert client.post("/api/ingest/jobs").status_code == 422


def test_add_pdf_and_txt_then_search(client, fixtures_dir):
    body = upload(client, fixtures_dir["receitas.pdf"], fixtures_dir["receitas.txt"])
    assert [j["status"] for j in body["jobs"]] == ["queued", "queued"]
    for job in body["jobs"]:
        done = wait_done(client, job["id"])
        assert done["status"] == "added", done
        assert done["result"]["n_chunks"] == 6 and done["result"]["lang"] == "pt"
        assert done["actions"] == []

    stats = client.get("/api/stats").json()
    assert stats["books"] == 2 and stats["chunks"] == 12
    filters = client.get("/api/filters").json()
    assert len(filters["books"]) == 2

    pdf_recipe = recipe_book(2)[0]
    query = f"{pdf_recipe['title']} {pdf_recipe['method']}"
    top = HOLDER.get().search(query, k=1)[0]
    pdf_job = wait_done(client, body["jobs"][0]["id"])
    assert top.book == pdf_job["result"]["book_title"]
    assert top.title.upper() == pdf_recipe["title"]


def test_scanned_pdf_stops_at_no_text(client, fixtures_dir):
    job = upload(client, fixtures_dir["escaneado.pdf"])["jobs"][0]
    done = wait_done(client, job["id"])
    assert done["status"] == "no_text" and done["reason_code"] == "no_text"
    assert done["pages_total"] == 3 and done["pages_without_text"] == 3


def test_non_culinary_is_discarded(client, fixtures_dir):
    job = upload(client, fixtures_dir["manual.txt"])["jobs"][0]
    done = wait_done(client, job["id"])
    assert (done["status"], done["reason_code"]) == ("discarded", "non_culinary")


def test_unsupported_extension_is_rejected(client):
    body = upload(client, ("planilha.xlsx", b"PK\x03\x04"))
    assert body["jobs"] == [] and body["rejected"] == [{"filename": "planilha.xlsx", "reason_code": "unsupported_format"}]


def test_path_traversal_filename_is_contained(client, tmp_cfg, fixtures_dir):
    body = upload(client, ("../../etc/passwd.txt", fixtures_dir["manual.txt"].read_bytes()))
    job = body["jobs"][0]
    assert job["filename"] == "passwd.txt"
    assert not (tmp_cfg.ingest_dir.parent.parent / "etc").exists()
    wait_done(client, job["id"])


def test_same_file_twice_is_duplicate_file(client, fixtures_dir):
    first = upload(client, fixtures_dir["receitas.txt"])["jobs"][0]
    assert wait_done(client, first["id"])["status"] == "added"
    again = upload(client, fixtures_dir["receitas.txt"])["jobs"][0]
    assert again["status"] == "duplicate" and again["reason_code"] == "duplicate_file"
    assert again["duplicate_of"]


def test_same_file_in_one_batch(client, fixtures_dir):
    body = upload(client, fixtures_dir["receitas.docx"], fixtures_dir["receitas.docx"])
    assert [j["status"] for j in body["jobs"]] == ["queued", "duplicate"]
    wait_done(client, body["jobs"][0]["id"])


def test_same_content_other_format_is_duplicate_content(client, fixtures_dir, no_llm):
    first = upload(client, fixtures_dir["receitas.txt"])["jobs"][0]
    assert wait_done(client, first["id"])["status"] == "added"
    llm_calls = len(no_llm)
    second = upload(client, fixtures_dir["receitas.md"])["jobs"][0]
    done = wait_done(client, second["id"])
    assert (done["status"], done["reason_code"]) == ("duplicate", "duplicate_content")
    assert done["duplicate_of"] == wait_done(client, first["id"])["result"]["book_title"]
    assert len(no_llm) == llm_calls  # nao pagou metadados de uma duplicata


def test_mixed_batch(client, fixtures_dir):
    body = upload(client, fixtures_dir["receitas.docx"], fixtures_dir["manual.txt"], fixtures_dir["quebrado.pdf"])
    results = [wait_done(client, j["id"]) for j in body["jobs"]]
    assert [(r["status"], r["reason_code"]) for r in results] == [
        ("added", None), ("discarded", "non_culinary"), ("error", "corrupt"),
    ]
    assert results[2]["retryable"] is False and results[2]["actions"] == []


def test_history_pagination(client):
    ids = [upload(client, (f"nota{i}.txt", f"texto curto {i}".encode()))["jobs"][0]["id"] for i in range(7)]
    for job_id in ids:
        wait_done(client, job_id)
    seen, before, pages = [], None, 0
    while True:
        params = {"limit": 3, **({"before": before} if before else {})}
        page = client.get("/api/ingest/jobs", params=params).json()
        seen += [j["id"] for j in page["jobs"]]
        pages += 1
        before = page["next_before"]
        if before is None:
            break
    assert pages == 3 and sorted(seen) == sorted(ids) and len(seen) == 7
    assert client.get("/api/ingest/jobs", params={"limit": 0}).status_code == 422
