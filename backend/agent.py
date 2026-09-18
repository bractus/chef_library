"""Agente culinario sobre o RAG, via OpenRouter (Claude Sonnet 5 / Haiku 4.5).

Usa o endpoint compativel com OpenAI do OpenRouter. Para trocar para a API
nativa da Anthropic, so o cliente em `_client()` muda; o resto continua igual.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Iterator, Sequence

from openai import OpenAI

from .config import CONFIG, Config
from .store import Hit

_SYSTEM_PT = textwrap.dedent(
    """\
    Voce e um chef pesquisador. Responde perguntas usando EXCLUSIVAMENTE os
    trechos de livros de cozinha fornecidos no contexto.

    Regras:
    - Cada trecho vem numerado como [1], [2], ... Cite a fonte usada assim: [1].
      Nunca cite um numero que nao esteja no contexto.
    - Se o contexto nao responde a pergunta, diga isso claramente e diga o que
      voce encontrou de mais proximo. Nunca invente receita, quantidade,
      temperatura ou tempo que nao esteja no trecho.
    - Quando o usuario pergunta por um ingrediente, priorize receitas que
      realmente usam esse ingrediente e diga em que quantidade.
    - Quando o usuario pergunta por uma tecnica, explique o metodo e cite os
      livros que o descrevem.
    - Os livros estao em varias linguas (portugues, ingles, frances, italiano,
      espanhol). Responda SEMPRE em portugues do Brasil, traduzindo o que for
      preciso, mas preservando os termos tecnicos originais entre parenteses.
    - Os textos vem de OCR e podem ter pequenos erros. Se um trecho estiver
      corrompido a ponto de nao dar para confiar, diga isso em vez de adivinhar.
    - Seja concreto: quantidades, tempos, temperaturas e ordem dos passos.
    """
)

_SYSTEM_EN = textwrap.dedent(
    """\
    You are a research chef. Answer questions using EXCLUSIVELY the cookbook
    excerpts provided in the context.

    Rules:
    - Each excerpt is numbered as [1], [2], ... Cite the source you used like
      this: [1]. Never cite a number that is not in the context.
    - If the context does not answer the question, say so clearly and say
      what you found that comes closest. Never invent a recipe, quantity,
      temperature, or time that is not in the excerpt.
    - When the user asks about an ingredient, prioritize recipes that
      actually use that ingredient and state the quantity.
    - When the user asks about a technique, explain the method and cite the
      books that describe it.
    - The books are in several languages (Portuguese, English, French,
      Italian, Spanish). Always answer in English, translating what is
      needed, but keep the original technical terms in parentheses.
    - The texts come from OCR and may have small errors. If an excerpt is
      corrupted beyond trust, say so instead of guessing.
    - Be concrete: quantities, times, temperatures, and order of steps.
    """
)

_SYSTEM_BY_LANG = {"pt": _SYSTEM_PT, "en": _SYSTEM_EN}


@dataclass
class Answer:
    text: str
    hits: Sequence[Hit]
    model: str
    usage: dict | None = None


def _client(cfg: Config) -> OpenAI:
    return OpenAI(
        api_key=cfg.require_openrouter(),
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": cfg.app_url,
            "X-Title": cfg.app_name,
        },
    )


def build_context(hits: Sequence[Hit], max_chars: int = 60_000) -> str:
    """Monta o bloco de contexto numerado, respeitando um teto de caracteres."""
    parts, total = [], 0
    for i, h in enumerate(hits, 1):
        head = f"[{i}] {h.book} — {h.title or 'trecho'}"
        if h.kind:
            head += f"  ({h.kind})"
        body = h.text.strip()
        block = f"{head}\n{body}\n"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n---\n".join(parts)


def _messages(question: str, hits: Sequence[Hit], lang: str = "pt") -> list[dict]:
    context = build_context(hits)
    if not context:
        context = "(nenhum trecho encontrado)" if lang == "pt" else "(no excerpts found)"
    label = "CONTEXTO — trechos recuperados do acervo" if lang == "pt" else "CONTEXT — excerpts retrieved from the archive"
    question_label = "PERGUNTA" if lang == "pt" else "QUESTION"
    user = f"{label}:\n\n{context}\n\n---\n{question_label}: {question}"
    system = _SYSTEM_BY_LANG.get(lang, _SYSTEM_PT)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def ask(question: str, hits: Sequence[Hit], cfg: Config = CONFIG, max_tokens: int = 2000, lang: str = "pt") -> Answer:
    resp = _client(cfg).chat.completions.create(
        model=cfg.openrouter_model,
        messages=_messages(question, hits, lang),
        max_tokens=max_tokens,
        temperature=0.2,
    )
    usage = resp.usage.model_dump() if resp.usage else None
    return Answer(resp.choices[0].message.content or "", hits, cfg.openrouter_model, usage)


def ask_stream(
    question: str, hits: Sequence[Hit], cfg: Config = CONFIG, max_tokens: int = 2000, lang: str = "pt",
) -> Iterator[str]:
    """Versao em streaming, usada pelo frontend para mostrar a resposta ao vivo."""
    stream = _client(cfg).chat.completions.create(
        model=cfg.openrouter_model,
        messages=_messages(question, hits, lang),
        max_tokens=max_tokens,
        temperature=0.2,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and (delta := chunk.choices[0].delta.content):
            yield delta


_FALLBACK_SYSTEM_PT = textwrap.dedent(
    """\
    Voce e um chef pesquisador. Para esta pergunta, NENHUM trecho relevante
    foi encontrado no acervo de livros. Responda mesmo assim, usando seu
    conhecimento geral e, se voce tiver acesso a busca na internet, os
    resultados dessa busca.

    Regras:
    - Deixe CLARO logo na primeira frase que esta resposta NAO vem do acervo
      de livros (ex.: "Nao encontrei isso no acervo, mas...").
    - Diga de onde veio a informacao: conhecimento geral do modelo, ou o
      site/fonte da busca na web quando voce tiver usado uma.
    - Nao invente numeros, fontes ou fatos especificos que voce nao tenha
      certeza — prefira ser generico a inventar precisao falsa.
    - Responda SEMPRE em portugues do Brasil, MESMO que a pergunta ou as
      paginas web encontradas estejam em outra lingua — traduza o conteudo,
      nunca troque de lingua para "espelhar" a fonte.
    """
)

_FALLBACK_SYSTEM_EN = textwrap.dedent(
    """\
    You are a research chef. For this question, NO relevant excerpt was
    found in the book archive. Answer anyway, using your general knowledge
    and, if you have access to web search, the results of that search.

    Rules:
    - Make CLEAR in the first sentence that this answer does NOT come from
      the book archive (e.g., "I couldn't find this in the archive, but...").
    - State where the information came from: the model's general knowledge,
      or the site/source of the web search when you used one.
    - Do not invent numbers, sources, or specific facts you are not sure
      of — prefer being generic over inventing false precision.
    - Always answer in English, EVEN IF the question or the web pages you
      found are in another language — translate the content, never switch
      language to "mirror" the source.
    """
)

_FALLBACK_SYSTEM_BY_LANG = {"pt": _FALLBACK_SYSTEM_PT, "en": _FALLBACK_SYSTEM_EN}


def _fallback_messages(question: str, lang: str = "pt") -> list[dict]:
    system = _FALLBACK_SYSTEM_BY_LANG.get(lang, _FALLBACK_SYSTEM_PT)
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def ask_fallback_stream(
    question: str, cfg: Config = CONFIG, max_tokens: int = 2000, lang: str = "pt",
) -> Iterator[str]:
    """Quando a busca no acervo nao encontra nada: cai para o conhecimento
    geral do modelo, tentando primeiro a variante `:online` do OpenRouter
    (busca na web via o plugin embutido); se o modelo/plugin nao aceitar
    esse sufixo, tenta de novo sem ele — ainda com o mesmo aviso de que a
    resposta nao vem do acervo."""
    client = _client(cfg)
    messages = _fallback_messages(question, lang)
    # o try cobre so a chamada que abre o stream (onde um sufixo ":online"
    # nao suportado da erro) — nunca a iteracao, para nao arriscar misturar
    # tokens de duas tentativas numa mesma resposta.
    try:
        stream = client.chat.completions.create(
            model=f"{cfg.openrouter_model}:online",
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
            stream=True,
        )
    except Exception:
        stream = client.chat.completions.create(
            model=cfg.openrouter_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
            stream=True,
        )
    for chunk in stream:
        if chunk.choices and (delta := chunk.choices[0].delta.content):
            yield delta
