"""Embeddings via OpenRouter (endpoint compativel com OpenAI /v1/embeddings).

Local embedding (sentence-transformers/MPS) foi trocado por isso porque, em
hardware com pouca RAM (Apple Silicon 8GB), a inferencia local do
multilingual-e5-base empacava em ~10 chunks/s (quase 4h para o acervo
inteiro). O endpoint de embeddings do OpenRouter entrega ~85 chunks/s em
lotes de 500 e custa frações de centavo para o acervo inteiro.
"""
from __future__ import annotations

import time
from typing import Sequence

import numpy as np
from openai import APIConnectionError, APIStatusError, OpenAI

from .config import CONFIG, Config

_CLIENT: OpenAI | None = None


def _client(cfg: Config) -> OpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OpenAI(
            api_key=cfg.require_openrouter(),
            base_url="https://openrouter.ai/api/v1",
            default_headers={"HTTP-Referer": cfg.app_url, "X-Title": cfg.app_name},
        )
    return _CLIENT


def _is_request_too_large(err: Exception) -> bool:
    msg = str(err).lower()
    return "maximum request size" in msg or "400" in msg and "token" in msg


def _embed_batch(texts: Sequence[str], cfg: Config, retries: int = 4) -> list[list[float]]:
    """Chama a API em um lote. Erros transitorios (conexao, 429, 5xx) levam
    a novo retry com backoff; um 400 de "request grande demais" nao se
    resolve tentando de novo com o mesmo payload — o lote e partido ao meio
    e cada metade e enviada recursivamente."""
    if not texts:
        return []
    if len(texts) == 1:
        retries = max(retries, 6)  # um unico texto gigante ainda pode estourar — mais tentativas antes de desistir
    last_err = None
    for attempt in range(retries):
        try:
            resp = _client(cfg).embeddings.create(model=cfg.embed_model, input=list(texts))
            # a API preserva a ordem, mas .index deixa isso explicito/auditavel
            ordered = sorted(resp.data, key=lambda d: d.index)
            return [d.embedding for d in ordered]
        except APIStatusError as e:
            if _is_request_too_large(e) and len(texts) > 1:
                mid = len(texts) // 2
                return _embed_batch(texts[:mid], cfg, retries) + _embed_batch(texts[mid:], cfg, retries)
            last_err = e
            time.sleep(min(2 ** attempt, 20))
        except APIConnectionError as e:
            last_err = e
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"Falha ao gerar embeddings via OpenRouter apos {retries} tentativas: {last_err}")


_CHARS_PER_TOKEN_ESTIMATE = 3.5   # conservador (PT/acentos tendem a tokenizar mais que EN)
_MAX_TOKENS_PER_REQUEST = 120_000  # bem abaixo do teto de 300k da API, com folga


def _batches_by_token_budget(texts: Sequence[str], max_count: int) -> list[list[str]]:
    """Agrupa textos respeitando um teto de contagem E um orcamento estimado
    de tokens por requisicao — um lote de 500 chunks curtos e barato, mas
    500 chunks proximos do teto de MAX_CHUNK_CHARS podem passar de 300k
    tokens somados, que e o limite rigido da API de embeddings."""
    batches: list[list[str]] = []
    buf: list[str] = []
    budget = 0.0
    for t in texts:
        est = len(t) / _CHARS_PER_TOKEN_ESTIMATE
        if buf and (len(buf) >= max_count or budget + est > _MAX_TOKENS_PER_REQUEST):
            batches.append(buf)
            buf, budget = [], 0.0
        buf.append(t)
        budget += est
    if buf:
        batches.append(buf)
    return batches


def _encode(texts: Sequence[str], cfg: Config, batch_size: int | None, show_progress: bool) -> np.ndarray:
    if not texts:
        return np.zeros((0, 0), dtype="float32")
    bs = batch_size or cfg.embed_batch_size
    out: list[list[float]] = []
    done = 0
    for batch in _batches_by_token_budget(texts, bs):
        out.extend(_embed_batch(batch, cfg))
        done += len(batch)
        if show_progress:
            print(f"  embed {done}/{len(texts)}", flush=True)
    arr = np.asarray(out, dtype="float32")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms  # normalizado => produto interno == cosseno


def dimension(cfg: Config = CONFIG) -> int:
    return len(_embed_batch(["dimensão"], cfg)[0])


def embed_passages(texts: Sequence[str], cfg: Config = CONFIG, batch_size: int | None = None,
                   show_progress: bool = False) -> np.ndarray:
    """Embeddings dos trechos do acervo."""
    return _encode(texts, cfg, batch_size, show_progress)


def embed_queries(texts: Sequence[str], cfg: Config = CONFIG) -> np.ndarray:
    """Embeddings de pergunta (mesmo modelo — a API da OpenRouter/OpenAI nao
    distingue papel de query/passage como o E5 local distinguia)."""
    return _encode(texts, cfg, None, False)
