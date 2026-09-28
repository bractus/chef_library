"""Chamadas ao modelo que respondem a pergunta do usuario.

Um fluxo so: o modelo recebe os trechos do acervo (se houver), as ultimas
trocas da conversa (memoria curta) e a ferramenta `web_search` (Tavily, quando
configurada), e decide sozinho quando complementar com a web ou com o proprio
conhecimento. O provedor e escolhido em `core.providers.resolve_provider`:
OpenRouter > OpenAI > Anthropic > Ollama local.
"""
from __future__ import annotations

import threading
from typing import Iterator, Sequence

from ..core.config import CONFIG, Config
from ..core.providers import Tool, chat, resolve_provider, stream_with_tools
from ..search.store import Hit
from . import websearch
from .memory import Turn
from .prompts import condense_messages, messages

ANSWER_TEMPERATURE = 0.3
MAX_ANSWER_TOKENS = 2_500
WEB_RESULTS_PER_SEARCH = 5

WEB_SEARCH_TOOL = Tool(
    name="web_search",
    description=(
        "Search the web for gastronomy information not covered (or only partly covered) by the "
        "archive excerpts: recipes, techniques, ingredients, chefs, restaurants, food history, "
        "current facts. Returns numbered results to cite as [n]."
    ),
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Search query, in the most useful language"}},
        "required": ["query"],
    },
)


def standalone_query(question: str, history: Sequence[Turn], cfg: Config = CONFIG) -> str:
    """Pergunta de continuacao reescrita como pergunta completa, para buscar no
    acervo. Sem historico, ou se a reescrita falhar, usa a pergunta como veio."""
    if not history:
        return question
    try:
        text, _ = chat(resolve_provider(cfg), condense_messages(question, history), 80, 0.0, cfg)
    except Exception:
        return question
    text = text.strip().strip('"').strip()
    return text if 0 < len(text) <= 400 else question


def ask_stream(
    question: str, hits: Sequence[Hit], history: Sequence[Turn] = (), lang: str = "pt",
    filters: str = "", cfg: Config = CONFIG,
) -> Iterator[tuple[str, object]]:
    """Resposta em streaming. Emite ("token", str) e ("web_sources", [..]) quando
    o modelo pesquisa na web — com a numeracao que continua a dos trechos."""
    web_enabled = bool(cfg.tavily_key.strip())
    next_number = len(hits) + 1
    numbering = threading.Lock()  # buscas de uma mesma rodada rodam em paralelo

    def run_tool(name: str, args: dict) -> tuple[str, object]:
        nonlocal next_number
        if name != WEB_SEARCH_TOOL.name:
            return f"Unknown tool: {name}", []
        query = str(args.get("query") or question)[:300]
        results = websearch.search(query, cfg, max_results=WEB_RESULTS_PER_SEARCH)
        if not results:
            return "The web search returned nothing (or is unavailable). Answer without it.", []
        with numbering:
            numbered = [(next_number + i, r) for i, r in enumerate(results)]
            next_number += len(results)
        text = websearch.build_web_context(numbered)
        payload = [{"index": n, "title": r.title, "url": r.url, "content": r.content[:600]} for n, r in numbered]
        return text, payload

    tools = [WEB_SEARCH_TOOL] if web_enabled else []
    stream = stream_with_tools(
        resolve_provider(cfg), messages(question, hits, history, lang, web_enabled, filters),
        tools, run_tool, MAX_ANSWER_TOKENS, ANSWER_TEMPERATURE, cfg,
    )
    for kind, payload in stream:
        if kind == "tool_start":
            yield "searching", str(payload["args"].get("query") or question)
        elif kind == "tool":
            if payload:
                yield "web_sources", payload
        else:
            yield kind, payload
