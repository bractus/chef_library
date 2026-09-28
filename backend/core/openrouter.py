"""Cliente unico do OpenRouter (endpoint compativel com OpenAI).

Agente, embeddings e classificacao de metadados montavam cada um o seu
cliente com a mesma base_url e os mesmos headers. Centralizar deixa a troca
de provedor (ex.: para a API nativa da Anthropic) num lugar so.
"""
from __future__ import annotations

from openai import OpenAI

from .config import CONFIG, Config

BASE_URL = "https://openrouter.ai/api/v1"

_CLIENTS: dict[tuple[str, str, str], OpenAI] = {}


def client(cfg: Config = CONFIG) -> OpenAI:
    """Cliente autenticado, memoizado por credencial/identificacao."""
    key = cfg.require_openrouter()
    cache_key = (key, cfg.app_url, cfg.app_name)
    if cache_key not in _CLIENTS:
        _CLIENTS[cache_key] = OpenAI(
            api_key=key,
            base_url=BASE_URL,
            default_headers={"HTTP-Referer": cfg.app_url, "X-Title": cfg.app_name},
        )
    return _CLIENTS[cache_key]
