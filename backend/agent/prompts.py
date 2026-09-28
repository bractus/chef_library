"""Monta as mensagens enviadas ao modelo.

O texto do prompt fica em backend/prompts/chef_system.md, nao aqui — edita-lo
e uma decisao editorial (como o chef deve responder), nao tecnica.

O .md e escrito em ingles, com placeholders: `{LANG}` (idioma da resposta) e
`{WEB_TOOL}` (se a busca na web esta disponivel nesta instalacao).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Sequence

from ..search.store import Hit
from .memory import Turn

# backend/agent/prompts.py -> backend/agent -> backend -> backend/prompts
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_LANG_NAME = {"pt": "Brazilian Portuguese", "en": "English"}

_WEB_ON = (
    "the `web_search` tool. Use it when the excerpts are not enough, to "
    "complement or check them, or for current facts. Its results are numbered "
    "after the excerpts; cite them the same way."
)
_WEB_OFF = "not available in this installation; rely on the excerpts and your own knowledge."


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def system_prompt(lang: str, web_enabled: bool) -> str:
    return (_load("chef_system")
            .replace("{LANG}", _LANG_NAME.get(lang, _LANG_NAME["pt"]))
            .replace("{WEB_TOOL}", _WEB_ON if web_enabled else _WEB_OFF))


def build_context(hits: Sequence[Hit], max_chars: int = 60_000) -> str:
    """Monta o bloco de contexto numerado, respeitando um teto de caracteres."""
    parts, total = [], 0
    for i, h in enumerate(hits, 1):
        head = f"[{i}] {h.book} — {h.title or 'trecho'}"
        if h.kind:
            head += f"  ({h.kind})"
        block = f"{head}\n{h.text.strip()}\n"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n---\n".join(parts)


def messages(question: str, hits: Sequence[Hit], history: Sequence[Turn], lang: str,
             web_enabled: bool, filters: str = "") -> list[dict]:
    # rotulos de estrutura ficam em ingles — sao marcacao para o modelo, nao
    # texto de resposta; a lingua da resposta vem so de {LANG} no system prompt
    msgs = [{"role": "system", "content": system_prompt(lang, web_enabled)}]
    for turn in history:
        msgs.append({"role": "user", "content": turn.question})
        msgs.append({"role": "assistant", "content": turn.answer})
    context = build_context(hits) or "(no excerpts found in the archive for this question)"
    filters_line = f"\nACTIVE FILTERS (chosen by the user in the interface): {filters}" if filters else ""
    msgs.append({"role": "user", "content": (
        f"CONTEXT — excerpts retrieved from the archive:\n\n{context}\n\n---{filters_line}\n"
        f"QUESTION: {question}"
    )})
    return msgs


def condense_messages(question: str, history: Sequence[Turn]) -> list[dict]:
    """Reescreve uma pergunta de continuacao como pergunta completa, so para a busca no acervo."""
    convo = "\n".join(f"User: {t.question}\nChef: {t.answer[:400]}" for t in history[-3:])
    return [
        {"role": "system", "content": (
            "Rewrite the user's last message as a standalone search query for a cookbook archive, "
            "resolving references to the conversation (\"it\", \"that recipe\", \"and the chocolate "
            "version?\"). Keep the user's language. If the message is already standalone, return it "
            "unchanged. Reply with the query only, no quotes, no explanation."
        )},
        {"role": "user", "content": f"Conversation:\n{convo}\n\nLast message: {question}"},
    ]
