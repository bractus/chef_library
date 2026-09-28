"""Escolhe qual provedor de LLM o agente usa, por ordem de prioridade:

    OpenRouter > OpenAI > Anthropic > Ollama local (padrao)

A primeira key de API configurada em .env vence; sem nenhuma, cai para um
modelo local via Ollama — sem custo, sem depender de rede. OpenRouter,
OpenAI e Ollama falam o mesmo protocolo (endpoint compativel com a API da
OpenAI), entao dividem um unico client; Anthropic usa o SDK nativo porque o
formato de mensagem e de streaming dela e diferente (system e paramentro
separado, os eventos de stream nao tem `.choices[0].delta.content`).

Trocar de provedor so muda de onde a resposta vem — os prompts (`prompts.py`)
e as regras de citacao continuam os mesmos para todos.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterator

from .config import CONFIG, Config

# prefixos de placeholder que .env.example deixa como exemplo — uma key
# assim presente nao conta como "configurada"
_PLACEHOLDER_PREFIXES = ("sk-or-v1-xxx", "sk-xxx", "sk-ant-xxx")


def _is_real_key(key: str) -> bool:
    key = key.strip()
    return bool(key) and not key.startswith(_PLACEHOLDER_PREFIXES)


@dataclass(frozen=True)
class Provider:
    name: str   # "openrouter" | "openai" | "anthropic" | "ollama"
    model: str


def resolve_provider(cfg: Config = CONFIG) -> Provider:
    if _is_real_key(cfg.openrouter_key):
        return Provider("openrouter", cfg.openrouter_model)
    if _is_real_key(cfg.openai_key):
        return Provider("openai", cfg.openai_model)
    if _is_real_key(cfg.anthropic_key):
        return Provider("anthropic", cfg.anthropic_model)
    return Provider("ollama", cfg.ollama_model)


# ---------- clientes compativeis com a API da OpenAI (3 dos 4 provedores) --

_OAI_CLIENTS: dict[tuple[str, str], "object"] = {}


def _openai_compatible_client(provider: Provider, cfg: Config):
    from openai import OpenAI

    if provider.name == "openrouter":
        from .openrouter import client as openrouter_client
        return openrouter_client(cfg)
    if provider.name == "openai":
        base_url, api_key = None, cfg.openai_key
    elif provider.name == "ollama":
        # Ollama nao exige auth; o SDK da OpenAI exige uma api_key nao-vazia
        base_url, api_key = cfg.ollama_base_url, "ollama"
    else:
        raise ValueError(f"provedor nao compativel com o client OpenAI: {provider.name}")

    cache_key = (provider.name, base_url or "")
    if cache_key not in _OAI_CLIENTS:
        _OAI_CLIENTS[cache_key] = OpenAI(api_key=api_key, base_url=base_url)
    return _OAI_CLIENTS[cache_key]


# ---------- Anthropic nativo -----------------------------------------------

def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """A API da Anthropic recebe o prompt de sistema como parametro proprio,
    nao como mensagem role=system na lista."""
    if messages and messages[0]["role"] == "system":
        return messages[0]["content"], messages[1:]
    return "", messages


_ANTHROPIC_CLIENT = None


def _anthropic_client(cfg: Config):
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        from anthropic import Anthropic
        _ANTHROPIC_CLIENT = Anthropic(api_key=cfg.anthropic_key)
    return _ANTHROPIC_CLIENT


# ---------- interface unica usada por backend/agent/chef.py ----------------

def _extra_body(provider: Provider) -> dict:
    """Modelos "reasoning" (ex.: openai/gpt-5-nano) gastam o max_tokens
    inteiro pensando por dentro e devolvem content=null se o esforco de
    raciocinio nao for reduzido — testado: com isso, um "diga oi" com
    max_tokens=50 voltava vazio (finish_reason="length", 0 tokens de
    conteudo). So o OpenRouter aceita esse parametro deste jeito; e
    inofensivo la para modelos sem essa opcao (confirmado com Claude via
    OpenRouter), mas a API direta da OpenAI usa outro nome de parametro e
    pode rejeitar em modelos que nao suportam — por isso so aqui."""
    if provider.name == "openrouter":
        return {"reasoning": {"effort": "minimal"}}
    return {}


def stream_chat(
    provider: Provider, messages: list[dict], max_tokens: int, temperature: float, cfg: Config = CONFIG,
) -> Iterator[str]:
    if provider.name == "anthropic":
        system, rest = _split_system(messages)
        with _anthropic_client(cfg).messages.stream(
            model=provider.model, system=system, messages=rest,
            max_tokens=max_tokens, temperature=temperature,
        ) as stream:
            yield from stream.text_stream
        return

    client = _openai_compatible_client(provider, cfg)
    stream = client.chat.completions.create(
        model=provider.model, messages=messages,
        max_tokens=max_tokens, temperature=temperature, stream=True,
        extra_body=_extra_body(provider),
    )
    for chunk in stream:
        if chunk.choices and (delta := chunk.choices[0].delta.content):
            yield delta


@dataclass(frozen=True)
class Tool:
    """Ferramenta oferecida ao modelo, num formato neutro convertido para cada API."""
    name: str
    description: str
    parameters: dict  # JSON Schema dos argumentos


# executa a ferramenta: (nome, argumentos) -> (texto que volta ao modelo, payload para o chamador)
ToolRunner = Callable[[str, dict], tuple[str, object]]
MAX_TOOL_ROUNDS = 2


def stream_with_tools(
    provider: Provider, messages: list[dict], tools: list[Tool], run_tool: ToolRunner,
    max_tokens: int, temperature: float, cfg: Config = CONFIG,
) -> Iterator[tuple[str, object]]:
    """Streaming com ferramentas: o modelo pode chamar uma ferramenta antes (ou no
    meio) da resposta; o resultado volta para ele e a resposta continua. Emite
    ("token", str) e ("tool", payload). Depois de MAX_TOOL_ROUNDS rodadas com
    ferramenta, a ultima vai sem ferramentas, para garantir uma resposta."""
    messages = list(messages)
    for round_ in range(MAX_TOOL_ROUNDS + 1):
        offered = tools if round_ < MAX_TOOL_ROUNDS else []
        if provider.name == "anthropic":
            calls = yield from _anthropic_round(provider, messages, offered, max_tokens, temperature, cfg)
        else:
            calls = yield from _openai_round(provider, messages, offered, max_tokens, temperature, cfg)
        if not calls:
            return
        for _, name, args in calls:
            yield "tool_start", {"name": name, "args": args}
        # o modelo costuma pedir varias buscas de uma vez: em paralelo, nao uma apos a outra
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            outcomes = list(pool.map(lambda c: run_tool(c[1], c[2]), calls))
        results = []
        for (call_id, _, _), (text, payload) in zip(calls, outcomes):
            yield "tool", payload
            results.append((call_id, text))
        if provider.name == "anthropic":
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid, "content": text} for cid, text in results
            ]})
        else:
            messages.extend({"role": "tool", "tool_call_id": cid, "content": text} for cid, text in results)


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _openai_round(provider, messages, tools, max_tokens, temperature, cfg):
    kwargs = {}
    if tools:
        kwargs["tools"] = [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
        kwargs["tool_choice"] = "auto"
    stream = _openai_compatible_client(provider, cfg).chat.completions.create(
        model=provider.model, messages=messages, max_tokens=max_tokens, temperature=temperature,
        stream=True, extra_body=_extra_body(provider), **kwargs,
    )
    text, pending = [], {}
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            text.append(delta.content)
            yield "token", delta.content
        for tc in delta.tool_calls or []:
            entry = pending.setdefault(tc.index, {"id": "", "name": "", "args": ""})
            entry["id"] = tc.id or entry["id"]
            if tc.function:
                entry["name"] += tc.function.name or ""
                entry["args"] += tc.function.arguments or ""
    if not pending:
        return []
    calls = [(e["id"] or f"call_{i}", e["name"], _parse_args(e["args"])) for i, e in sorted(pending.items())]
    messages.append({"role": "assistant", "content": "".join(text) or None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        for cid, name, args in calls
    ]})
    return calls


def _anthropic_round(provider, messages, tools, max_tokens, temperature, cfg):
    system, rest = _split_system(messages)
    kwargs = {}
    if tools:
        kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                           for t in tools]
    with _anthropic_client(cfg).messages.stream(
        model=provider.model, system=system, messages=rest,
        max_tokens=max_tokens, temperature=temperature, **kwargs,
    ) as stream:
        for text in stream.text_stream:
            yield "token", text
        final = stream.get_final_message()
    calls = [(b.id, b.name, _parse_args(b.input)) for b in final.content if b.type == "tool_use"]
    if calls:
        messages.append({"role": "assistant", "content": [b.model_dump() for b in final.content]})
    return calls


def chat(
    provider: Provider, messages: list[dict], max_tokens: int, temperature: float, cfg: Config = CONFIG,
) -> tuple[str, dict | None]:
    if provider.name == "anthropic":
        system, rest = _split_system(messages)
        resp = _anthropic_client(cfg).messages.create(
            model=provider.model, system=system, messages=rest,
            max_tokens=max_tokens, temperature=temperature,
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return text, usage

    client = _openai_compatible_client(provider, cfg)
    resp = client.chat.completions.create(
        extra_body=_extra_body(provider),
        model=provider.model, messages=messages, max_tokens=max_tokens, temperature=temperature,
    )
    usage = resp.usage.model_dump() if resp.usage else None
    return resp.choices[0].message.content or "", usage
