from types import SimpleNamespace as NS

import pytest

from backend.agent.memory import ConversationMemory
from backend.core import providers
from backend.core.providers import Provider, Tool, stream_with_tools

TOOL = Tool("web_search", "busca", {"type": "object", "properties": {"query": {"type": "string"}}})


# ---------- memoria curta ----------
def test_memory_keeps_last_turns_and_strips_citations():
    mem = ConversationMemory(max_turns=2)
    for i in range(3):
        mem.add("c1", f"pergunta {i}", f"resposta {i} com fonte [{i + 1}] e outra [12].")
    turns = mem.history("c1")
    assert [t.question for t in turns] == ["pergunta 1", "pergunta 2"]
    assert turns[-1].answer == "resposta 2 com fonte e outra."
    assert mem.history("outra") == [] and mem.history(None) == []


def test_memory_expires_and_forgets():
    now = [0.0]
    mem = ConversationMemory(ttl=60, clock=lambda: now[0])
    mem.add("c1", "q", "a")
    now[0] = 59
    assert len(mem.history("c1")) == 1
    now[0] = 200
    assert mem.history("c1") == []
    mem.add("c2", "q", "a")
    mem.forget("c2")
    assert mem.history("c2") == []


def test_memory_caps_conversations():
    mem = ConversationMemory(max_conversations=2)
    for cid in ("a", "b", "c"):
        mem.add(cid, "q", "a")
    assert mem.history("a") == [] and mem.history("c")


# ---------- ferramentas: formato OpenAI (OpenRouter/OpenAI/Ollama) ----------
def _chunk(content=None, tool_calls=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls))])


def _tc(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


class FakeOpenAI:
    def __init__(self, rounds):
        self.rounds, self.calls = list(rounds), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.rounds.pop(0))


def test_openai_tool_round_then_answer(monkeypatch):
    fake = FakeOpenAI([
        [_chunk(tool_calls=[_tc(0, id="c1", name="web_search", args='{"que')]),
         _chunk(tool_calls=[_tc(0, args='ry": "tempura"}')])],
        [_chunk("Tempura "), _chunk("leve [3].")],
    ])
    monkeypatch.setattr(providers, "_openai_compatible_client", lambda p, c: fake)
    ran = []

    def run(name, args):
        ran.append((name, args))
        return "[3] resultado", [{"index": 3}]

    events = list(stream_with_tools(Provider("openrouter", "m"), [{"role": "system", "content": "s"}],
                                    [TOOL], run, 100, 0.2))
    assert ran == [("web_search", {"query": "tempura"})]
    assert events == [("tool_start", {"name": "web_search", "args": {"query": "tempura"}}),
                      ("tool", [{"index": 3}]), ("token", "Tempura "), ("token", "leve [3].")]
    assert fake.calls[0]["tools"][0]["function"]["name"] == "web_search"
    second = fake.calls[1]["messages"]
    assert second[-2]["tool_calls"][0]["id"] == "c1" and second[-1] == {
        "role": "tool", "tool_call_id": "c1", "content": "[3] resultado"}


def test_openai_without_tool_call_streams_directly(monkeypatch):
    fake = FakeOpenAI([[_chunk("so "), _chunk("texto")]])
    monkeypatch.setattr(providers, "_openai_compatible_client", lambda p, c: fake)
    events = list(stream_with_tools(Provider("ollama", "m"), [], [TOOL], lambda n, a: ("", None), 100, 0.2))
    assert events == [("token", "so "), ("token", "texto")]


def test_last_round_offers_no_tools(monkeypatch):
    call = [_chunk(tool_calls=[_tc(0, id="x", name="web_search", args='{"query": "a"}')])]
    fake = FakeOpenAI([call, call, [_chunk("fim")]])
    monkeypatch.setattr(providers, "_openai_compatible_client", lambda p, c: fake)
    events = list(stream_with_tools(Provider("openai", "m"), [], [TOOL], lambda n, a: ("r", None), 100, 0.2))
    assert events[-1] == ("token", "fim")
    assert "tools" in fake.calls[0] and "tools" in fake.calls[1] and "tools" not in fake.calls[2]


# ---------- ferramentas: Anthropic nativo ----------
class FakeAnthropicStream:
    def __init__(self, texts, blocks):
        self.text_stream, self._blocks = iter(texts), blocks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return NS(content=self._blocks)


def _block(**kw):
    return NS(model_dump=lambda: dict(kw), **kw)


def test_anthropic_tool_round(monkeypatch):
    rounds = [
        FakeAnthropicStream(["Vou pesquisar. "], [
            _block(type="text", text="Vou pesquisar. "),
            _block(type="tool_use", id="tu1", name="web_search", input={"query": "noma"}),
        ]),
        FakeAnthropicStream(["Resposta [2]."], [_block(type="text", text="Resposta [2].")]),
    ]
    sent = []

    def stream(**kwargs):
        sent.append(kwargs)
        return rounds.pop(0)

    monkeypatch.setattr(providers, "_anthropic_client", lambda cfg: NS(messages=NS(stream=stream)))
    events = list(stream_with_tools(Provider("anthropic", "m"), [{"role": "system", "content": "sys"}],
                                    [TOOL], lambda n, a: ("[2] web", ["w"]), 100, 0.2))
    assert events == [("token", "Vou pesquisar. "), ("tool_start", {"name": "web_search", "args": {"query": "noma"}}),
                      ("tool", ["w"]), ("token", "Resposta [2].")]
    assert sent[0]["system"] == "sys" and sent[0]["tools"][0]["input_schema"]["type"] == "object"
    assert sent[1]["messages"][-1]["content"][0] == {"type": "tool_result", "tool_use_id": "tu1", "content": "[2] web"}


@pytest.mark.parametrize("raw,expected", [('{"query": "x"}', {"query": "x"}), ("", {}), ("nao json", {}), ("[1]", {})])
def test_parse_args(raw, expected):
    assert providers._parse_args(raw) == expected


def test_parallel_tool_calls_in_one_round(monkeypatch):
    import threading
    import time

    fake = FakeOpenAI([
        [_chunk(tool_calls=[_tc(0, id="a", name="web_search", args='{"query": "um"}'),
                            _tc(1, id="b", name="web_search", args='{"query": "dois"}')])],
        [_chunk("ok")],
    ])
    monkeypatch.setattr(providers, "_openai_compatible_client", lambda p, c: fake)
    running, peak = [0], [0]
    lock = threading.Lock()

    def run(name, args):
        with lock:
            running[0] += 1; peak[0] = max(peak[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        return f"r-{args['query']}", args["query"]

    events = list(stream_with_tools(Provider("openai", "m"), [], [TOOL], run, 100, 0.2))
    assert peak[0] == 2  # as duas buscas ao mesmo tempo
    assert [e for e in events if e[0] == "tool"] == [("tool", "um"), ("tool", "dois")]  # ordem preservada
    assert [m["tool_call_id"] for m in fake.calls[1]["messages"] if m.get("role") == "tool"] == ["a", "b"]
