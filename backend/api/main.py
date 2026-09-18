"""API leve sobre o pipeline backend/* — serve o frontend novo (React).

Não reimplementa busca nem geração: so encapsula ChunkStore.search e
agent.ask_stream/build_context em endpoints HTTP + streaming (SSE).

Rodar em dev:
    uvicorn backend.api.main:app --reload --port 8000
"""
from __future__ import annotations

import json
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from ..agent import ask_fallback_stream, ask_stream
from ..clean import garbage_run_ratio
from ..config import CONFIG
from ..store import ChunkStore, Hit

app = FastAPI(title="Chef Library API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

KINDS = ["receita", "tecnica", "texto"]


@lru_cache(maxsize=1)
def get_store() -> ChunkStore:
    return ChunkStore.load(CONFIG)


def _store_or_503() -> ChunkStore:
    try:
        return get_store()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


def _hit_to_dict(h: Hit) -> dict:
    d = asdict(h)
    # marca trechos cujo OCR esta ilegivel demais para confiar — mesma
    # heuristica usada no build (backend/clean.py) para descartar chunks,
    # aqui exposta como sinal na UI em vez de descartar silenciosamente.
    d["low_quality"] = garbage_run_ratio(h.text) > 0.15
    return d


@app.get("/api/stats")
def stats() -> dict:
    return _store_or_503().stats()


@app.get("/api/filters")
def filters() -> dict:
    store = _store_or_503()
    return {
        "regions": store.regions(),
        "dish_types": store.dish_type_options(),
        "books": store.book_titles(),
        "kinds": KINDS,
        "agent_model": CONFIG.openrouter_model,
        "embed_model": CONFIG.embed_model,
    }


@app.get("/api/ask")
async def ask(
    q: str = Query(..., min_length=1),
    k: int = Query(8, ge=1, le=20),
    lang: Literal["pt", "en"] = "pt",
    kind: Optional[str] = None,
    ingredient: Optional[str] = None,
    book: Optional[str] = None,
    region: Optional[str] = None,
    dish_type: Optional[str] = None,
):
    store = _store_or_503()

    hits = store.search(
        q, k=k, kind=kind or None, ingredient=ingredient or None,
        book=book or None, region=region or None, dish_type=dish_type or None,
    )

    async def events():
        if not hits:
            # nada no acervo: cai para conhecimento geral / busca na web do
            # modelo em vez de so devolver "nada encontrado".
            yield {"event": "fallback", "data": "{}"}
            try:
                for token in ask_fallback_stream(q, lang=lang):
                    yield {"event": "token", "data": token}
            except RuntimeError as e:
                yield {"event": "error", "data": str(e)}
                return
            except Exception as e:
                yield {"event": "error", "data": f"Erro ao chamar o agente via OpenRouter: {e}"}
                return
            yield {"event": "done", "data": "{}"}
            return

        yield {"event": "sources", "data": json.dumps([_hit_to_dict(h) for h in hits], ensure_ascii=False)}

        try:
            for token in ask_stream(q, hits, lang=lang):
                yield {"event": "token", "data": token}
        except RuntimeError as e:
            yield {"event": "error", "data": str(e)}
            return
        except Exception as e:
            yield {"event": "error", "data": f"Erro ao chamar o agente via OpenRouter: {e}"}
            return

        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(events())


_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
