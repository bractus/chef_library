"""/api/ask de ponta a ponta com o modelo e o Tavily falsos."""
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from backend.agent import chef, websearch
from backend.agent.memory import MEMORY
from backend.core.providers import Provider
from backend.ingest import commit
from backend.search.store import Chunk, StoreHolder
from conftest import fake_vectors


def _events(body: str) -> list[tuple[str, str]]:
    out, event = [], None
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:") and event:
            out.append((event, line[5:].strip()))
    return out


@pytest.fixture
def library(tmp_cfg, fake_embed):
    chunks = [Chunk(id=f"b::{i}", book="Livro de Tempura", title=f"Tempura {i}", kind="receita", lang="pt",
                    text=f"tempura de legumes massa gelada receita {i}", ingredients=[], n_chars=40) for i in range(3)]
    commit.commit_book(StoreHolder(tmp_cfg), fake_vectors([c.text for c in chunks]), chunks, md5="m", job_id="j",
                       cfg=tmp_cfg, processed_entry={"n_chunks": 3, "tier": "receitas", "slug": "b"})
    return tmp_cfg


@pytest.fixture
def agent(monkeypatch):
    """Modelo falso: pesquisa na web uma vez e responde citando o que viu."""
    seen = {"messages": [], "condense": []}

    def fake_stream(provider, messages, tools, run_tool, max_tokens, temperature, cfg):
        seen["messages"].append(messages)
        seen["tools"] = [t.name for t in tools]
        if tools:
            yield "tool_start", {"name": "web_search", "args": {"query": "tempura historia"}}
            _, payload = run_tool("web_search", {"query": "tempura historia"})
            yield "tool", payload
        yield "token", "Resposta com livro [1] e web [4]."

    def fake_chat(provider, messages, max_tokens, temperature, cfg):
        seen["condense"].append(messages[-1]["content"])
        return "tempura de camarão", None

    monkeypatch.setattr(chef, "stream_with_tools", fake_stream)
    monkeypatch.setattr(chef, "chat", fake_chat)
    monkeypatch.setattr(chef, "resolve_provider", lambda cfg: Provider("openrouter", "fake"))
    monkeypatch.setattr(websearch, "search", lambda q, cfg, max_results=5: [
        websearch.WebResult("Historia da tempura", "https://ex.com/a", "Portugueses levaram ao Japao."),
        websearch.WebResult("Tempura hoje", "https://ex.com/b", "Massa com agua gelada."),
    ])
    yield seen
    MEMORY.forget("conv1")


@pytest.fixture
def client(library, agent):
    from backend.api.main import app

    app.state.cfg = library
    with TestClient(app) as c:
        yield c
    del app.state.cfg


def _ask(client, q, **params):
    res = client.get("/api/ask", params={"q": q, "k": 3, **params})
    assert res.status_code == 200
    return _events(res.text)


def test_web_sources_continue_numbering_after_excerpts(client, agent):
    events = _ask(client, "como fazer tempura")
    kinds = [e for e, _ in events]
    assert kinds[0] == "sources" and "web_sources" in kinds and kinds[-1] == "done"
    web = json.loads(dict(events)["web_sources"])
    assert [w["index"] for w in web] == [4, 5] and web[0]["url"] == "https://ex.com/a"
    assert json.loads(dict(events)["searching"]) == {"query": "tempura historia"}
    assert kinds.index("searching") < kinds.index("web_sources") < kinds.index("token")
    assert agent["tools"] == ["web_search"]
    assert "[1] Livro de Tempura" in agent["messages"][0][-1]["content"]


def test_no_excerpts_still_answers_with_fallback_flag(client, agent):
    events = _ask(client, "tempura", ingredient="chocolate")  # filtro que nada no acervo satisfaz
    assert events[0] == ("fallback", "{}")
    assert "(no excerpts found in the archive" in agent["messages"][0][-1]["content"]
    assert "ACTIVE FILTERS" in agent["messages"][0][-1]["content"]


def test_short_term_memory_across_questions(client, agent):
    _ask(client, "como fazer tempura", conversation_id="conv1")
    assert agent["condense"] == []  # primeira pergunta nao precisa ser reescrita
    events = _ask(client, "e com camarão?", conversation_id="conv1")
    assert json.loads(events[-1][1]) == {"turns": 2}
    assert "e com camarão?" in agent["condense"][0]
    second = agent["messages"][1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == "como fazer tempura"
    assert "[1]" not in second[2]["content"]  # citacoes da rodada anterior nao voltam

    assert client.delete("/api/conversations/conv1").status_code == 204
    _ask(client, "outra pergunta", conversation_id="conv1")
    assert [m["role"] for m in agent["messages"][2]] == ["system", "user"]


def test_web_tool_not_offered_without_tavily_key(client, agent, library, monkeypatch):
    monkeypatch.setattr(client.app.state.ingest, "cfg", dataclasses.replace(library, tavily_key=""))
    events = _ask(client, "tempura")
    assert agent["tools"] == [] and "web_sources" not in [e for e, _ in events]
    assert "not available in this installation" in agent["messages"][-1][0]["content"]


def test_invalid_conversation_id_is_rejected(client):
    assert client.get("/api/ask", params={"q": "x", "conversation_id": "../x"}).status_code == 422
