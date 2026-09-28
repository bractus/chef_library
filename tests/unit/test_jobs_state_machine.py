import pytest

from backend.ingest.jobs import InvalidTransition, JobRepo, to_api


def _new(repo, **kw):
    base = dict(filename="livro.pdf", format="pdf", size_bytes=10, file_md5="abc", staged_path="/x")
    base.update(kw)
    return repo.create(**base)


def _processing(repo, **kw):
    job = _new(repo, **kw)
    assert repo.claim_next()["id"] == job["id"]
    return job


def test_happy_path_to_added(repo):
    job = _processing(repo)
    repo.set_stage(job["id"], "embedding", 1, 4)
    api = to_api(repo.get(job["id"]), ocr_available=True)
    assert api["progress"] == {"current": 1, "total": 4} and api["stage"] == "embedding"
    done = repo.finish(job["id"], "added", book_title="Livro", lang="pt", n_chunks=5)
    api = to_api(done, ocr_available=True)
    assert api["status"] == "added" and api["result"]["n_chunks"] == 5
    assert api["actions"] == [] and api["finished_at"]


def test_claim_next_takes_oldest(repo):
    first = _new(repo, filename="a.pdf")
    _new(repo, filename="b.pdf")
    assert repo.claim_next()["id"] == first["id"]


@pytest.mark.parametrize("reason,retryable", [
    ("embeddings_unavailable", True), ("interrupted", True), ("internal", True),
    ("protected", False), ("corrupt", False),
])
def test_retryable_errors(repo, reason, retryable):
    job = _processing(repo)
    err = repo.finish(job["id"], "error", reason_code=reason)
    assert bool(err["retryable"]) is retryable
    assert to_api(err, True)["actions"] == (["retry"] if retryable else [])
    if retryable:
        assert repo.request(job["id"], "retry")["status"] == "queued"
    else:
        with pytest.raises(InvalidTransition):
            repo.request(job["id"], "retry")


def test_no_text_actions(repo):
    scanned = _processing(repo, filename="a.pdf")
    repo.finish(scanned["id"], "no_text", reason_code="no_text", pages_total=3, pages_without_text=3)
    partial = _processing(repo, filename="b.pdf")
    repo.finish(partial["id"], "no_text", reason_code="partial_text", pages_total=9, pages_without_text=3)

    assert to_api(repo.get(scanned["id"]), True)["actions"] == ["ocr", "cancel"]
    assert to_api(repo.get(scanned["id"]), False)["actions"] == ["cancel"]
    assert to_api(repo.get(partial["id"]), True)["actions"] == ["ocr", "continue_without_ocr", "cancel"]

    with pytest.raises(InvalidTransition):
        repo.request(scanned["id"], "continue_without_ocr")
    assert repo.request(scanned["id"], "ocr")["ocr_mode"] == "ocr"
    assert repo.request(partial["id"], "continue_without_ocr")["ocr_mode"] == "skip_ocr"


def test_invalid_transitions(repo):
    job = _processing(repo)
    repo.finish(job["id"], "added", book_title="x")
    with pytest.raises(InvalidTransition):
        repo.request(job["id"], "retry")
    with pytest.raises(InvalidTransition):
        repo.finish(job["id"], "discarded", reason_code="non_culinary")


def test_duplicate_created_directly(repo):
    job = _new(repo, status="duplicate", reason_code="duplicate_file", staged_path=None)
    assert job["status"] == "duplicate" and job["finished_at"]
    assert repo.claim_next() is None


def test_mark_interrupted(repo):
    job = _processing(repo)
    assert repo.mark_interrupted() == 1
    got = repo.get(job["id"])
    assert got["status"] == "error" and got["reason_code"] == "interrupted"
    assert to_api(got, True)["actions"] == ["retry"]


def test_repo_survives_reopen(repo, tmp_cfg):
    job = _new(repo)
    other = JobRepo(tmp_cfg)
    try:
        assert other.get(job["id"])["filename"] == "livro.pdf"
    finally:
        other.close()
