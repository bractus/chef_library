"""Busca web do agente (ferramenta `web_search`), via Tavily.

Tavily em vez do plugin ":online" do OpenRouter porque aquele so funciona com
aquele provedor; assim a busca e real e igual para os quatro (OpenRouter,
OpenAI, Anthropic, Ollama). O modelo decide quando pesquisar.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..core.config import CONFIG, Config

TAVILY_URL = "https://api.tavily.com/search"
TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_RESULTS = 5


@dataclass
class WebResult:
    title: str
    url: str
    content: str


def search(query: str, cfg: Config = CONFIG, max_results: int = DEFAULT_MAX_RESULTS) -> list[WebResult]:
    """Busca no Tavily; NUNCA levanta — devolve lista vazia se a key nao
    estiver configurada ou a chamada falhar (rede, quota, timeout). A resposta
    deve seguir com os trechos e o conhecimento do modelo, nao quebrar por
    causa de um provedor de busca fora do ar."""
    key = cfg.tavily_key.strip()
    if not key:
        return []
    try:
        resp = httpx.post(
            TAVILY_URL,
            json={"api_key": key, "query": query, "max_results": max_results, "search_depth": "basic"},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    return [
        WebResult(r.get("title", ""), r.get("url", ""), r.get("content", ""))
        for r in data.get("results", [])
        if r.get("content")
    ]


def build_web_context(numbered: list[tuple[int, WebResult]], max_chars: int = 6_000) -> str:
    """Bloco numerado no mesmo formato do build_context de prompts.py, com a
    numeracao continuando a dos trechos do acervo, para o modelo citar a
    fonte web do mesmo jeito que citaria um trecho de livro."""
    parts, total = [], 0
    for i, r in numbered:
        block = f"[{i}] {r.title} — {r.url}\n{r.content.strip()}\n"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n---\n".join(parts)
