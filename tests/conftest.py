from __future__ import annotations

import hashlib
import os
import re
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import psycopg
import pytest
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

from backend.core.config import Config  # noqa: E402
from make_fixtures import build_all  # noqa: E402

EMBED_DIM = 512
_TOKEN = re.compile(r"\w+", re.UNICODE)


def fake_vectors(texts) -> np.ndarray:
    """Embedding deterministico: bag-of-words com hash. Textos iguais dao
    cosseno 1; textos diferentes (mesma estrutura de receita) ficam bem abaixo."""
    out = np.zeros((len(texts), EMBED_DIM), dtype="float32")
    for row, text in enumerate(texts):
        for tok in _TOKEN.findall(text.lower()):
            h = hashlib.sha1(tok.encode()).digest()
            out[row, int.from_bytes(h[:4], "little") % EMBED_DIM] += 1.0 if h[4] % 2 else -1.0
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return out / norms


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("books"))


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "postgresql://chef:chef@localhost:5432/chef_library")


@pytest.fixture(scope="session")
def pg_available() -> None:
    try:
        psycopg.connect(TEST_DATABASE_URL, connect_timeout=3).close()
    except psycopg.OperationalError as e:
        pytest.fail(f"Postgres indisponivel em {TEST_DATABASE_URL} — suba com `docker compose up -d db`.\n{e}")


@pytest.fixture
def pg_schema(pg_available) -> str:
    """Schema isolado por teste; apagado no fim."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {name}")
    yield name
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def tmp_cfg(tmp_path, pg_schema) -> Config:
    return Config(
        books_raw=tmp_path / "books",
        books_clean=tmp_path / "books_md",
        index_dir=tmp_path / "data" / "index",
        ingest_dir=tmp_path / "data" / "ingest",
        openrouter_key="sk-or-v1-test",
        database_url=make_conninfo(TEST_DATABASE_URL, options=f"-c search_path={pg_schema}"),
    )


@pytest.fixture
def repo(tmp_cfg):
    from backend.ingest.jobs import JobRepo

    r = JobRepo(tmp_cfg, max_connections=4)
    yield r
    r.close()


@pytest.fixture
def fake_embed(monkeypatch):
    import backend.search.embed as embed
    import backend.search.store as store

    def passages(texts, cfg=None, batch_size=None, show_progress=False):
        return fake_vectors(list(texts))

    def queries(texts, cfg=None):
        return fake_vectors(list(texts))

    monkeypatch.setattr(embed, "embed_passages", passages)
    monkeypatch.setattr(embed, "embed_queries", queries)
    monkeypatch.setattr(store, "embed_queries", queries)
    return fake_vectors


@pytest.fixture
def no_llm(monkeypatch):
    import backend.ingest.metadata as metadata
    import backend.ingest.pipeline as pipeline

    calls = []
    original = metadata.classify_book

    def heuristic_only(*args, **kwargs):
        calls.append(args[0])
        kwargs["use_llm"] = False
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "classify_book", heuristic_only)
    return calls


def wait_for(predicate, timeout: float = 20.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condicao nao atingida no tempo limite")
