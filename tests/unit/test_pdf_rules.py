import pytest
from fastapi.testclient import TestClient

from backend.ingest.extract.ocr import ocr_available
from conftest import wait_for
from make_fixtures import book_text, recipe_book

ACTIVE = {"queued", "processing"}


@pytest.fixture
def ocr_calls(monkeypatch):
    """OCR simulado: cada pagina pedida vira uma receita diferente."""
    import backend.api.ingest as api_ingest
    import backend.ingest.extract.ocr as ocr

    calls: list[list[int]] = []
    recipes = recipe_book(8, n=12)

    def fake_ocr(path, pages, progress=None, workers=None):
        calls.append(list(pages))
        out = {}
        for n, i in enumerate(pages, 1):
            out[i] = book_text([recipes[i % len(recipes)]])
            if progress:
                progress(n, len(pages))
        return out

    monkeypatch.setattr(ocr, "ocr_pages", fake_ocr)
    monkeypatch.setattr(api_ingest, "ocr_available", lambda: True)
    return calls


@pytest.fixture
def client(tmp_cfg, fake_embed, no_llm):
    from backend.api.main import app

    app.state.cfg = tmp_cfg
    with TestClient(app) as c:
        yield c
    del app.state.cfg


def _upload(client, path):
    res = client.post("/api/ingest/jobs", files=[("files", (path.name, path.read_bytes(), "application/pdf"))])
    return res.json()["jobs"][0]


def _wait(client, job_id):
    return wait_for(lambda: (j := client.get(f"/api/ingest/jobs/{job_id}").json())["status"] not in ACTIVE and j)


def test_scanned_pdf_offers_only_ocr(client, fixtures_dir, ocr_calls):
    job = _wait(client, _upload(client, fixtures_dir["escaneado.pdf"])["id"])
    assert (job["status"], job["reason_code"]) == ("no_text", "no_text")
    assert job["actions"] == ["ocr", "cancel"]
    res = client.post(f"/api/ingest/jobs/{job['id']}/continue-without-ocr")
    assert res.status_code == 409 and res.json()["detail"] == "invalid_transition"


def test_ocr_indexes_scanned_pdf(client, fixtures_dir, ocr_calls):
    job = _wait(client, _upload(client, fixtures_dir["escaneado.pdf"])["id"])
    res = client.post(f"/api/ingest/jobs/{job['id']}/ocr")
    assert res.status_code == 200 and res.json()["status"] == "queued"
    done = _wait(client, job["id"])
    assert done["status"] == "added", done
    assert ocr_calls == [[0, 1, 2]]
    assert done["result"]["pages_without_text"] == 0


def test_partial_pdf_offers_both_and_can_skip_ocr(client, fixtures_dir, ocr_calls):
    job = _wait(client, _upload(client, fixtures_dir["parcial.pdf"])["id"])
    assert (job["status"], job["reason_code"]) == ("no_text", "partial_text")
    assert job["actions"] == ["ocr", "continue_without_ocr", "cancel"]
    assert (job["pages_total"], job["pages_without_text"]) == (9, 3)
    client.post(f"/api/ingest/jobs/{job['id']}/continue-without-ocr")
    done = _wait(client, job["id"])
    assert done["status"] == "added" and done["result"]["pages_without_text"] == 3
    assert ocr_calls == []


def test_partial_pdf_ocr_only_empty_pages(client, fixtures_dir, ocr_calls):
    job = _wait(client, _upload(client, fixtures_dir["parcial.pdf"])["id"])
    client.post(f"/api/ingest/jobs/{job['id']}/ocr")
    assert _wait(client, job["id"])["status"] == "added"
    assert ocr_calls == [[1, 3, 5]]


def test_ocr_endpoint_when_tesseract_missing(client, fixtures_dir, monkeypatch):
    import backend.api.ingest as api_ingest

    monkeypatch.setattr(api_ingest, "ocr_available", lambda: False)
    job = _wait(client, _upload(client, fixtures_dir["escaneado.pdf"])["id"])
    assert job["actions"] == ["cancel"]
    res = client.post(f"/api/ingest/jobs/{job['id']}/ocr")
    assert res.status_code == 409 and res.json()["detail"] == "ocr_unavailable"


@pytest.mark.skipif(not ocr_available(), reason="Tesseract nao instalado")
def test_real_tesseract(tmp_path):
    from backend.ingest.extract.ocr import ocr_pages
    from make_fixtures import write_image_pdf

    pdf = tmp_path / "scan.pdf"
    write_image_pdf(pdf, 1, text="BOLO DE CENOURA\nIngredientes\n3 cenouras medias\n2 xicaras de farinha")
    text = ocr_pages(pdf, [0])[0].upper()
    assert "CENOURA" in text and "FARINHA" in text
