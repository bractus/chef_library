"""Memoria de curto prazo do agente: as ultimas trocas de cada conversa.

Efemera de proposito — so em memoria, some quando a conversa fica parada
(TTL) ou quando o processo reinicia; nada vai para disco nem para o banco.
Serve para perguntas de continuacao ("e a versao de chocolate?").
"""
from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

MAX_TURNS = 6                  # trocas (pergunta + resposta) lembradas por conversa
TTL_SECONDS = 30 * 60          # conversa parada por mais que isso e esquecida
MAX_CONVERSATIONS = 500        # teto de conversas simultaneas (as mais antigas saem)
MAX_ANSWER_CHARS = 1_500       # resposta guardada e resumida ao comeco

# citacoes [n] de uma resposta antiga apontam para fontes daquela rodada, nao
# da atual — guarda-las confundiria o modelo na proxima
_CITATION = re.compile(r"\s?\[\d+\]")


@dataclass
class Turn:
    question: str
    answer: str


class ConversationMemory:
    def __init__(self, max_turns: int = MAX_TURNS, ttl: float = TTL_SECONDS,
                 max_conversations: int = MAX_CONVERSATIONS, clock=time.monotonic):
        self.max_turns = max_turns
        self.ttl = ttl
        self.max_conversations = max_conversations
        self._clock = clock
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[float, deque[Turn]]] = OrderedDict()

    def _expire(self, now: float) -> None:
        while self._data:
            cid, (seen, _) = next(iter(self._data.items()))
            if now - seen <= self.ttl and len(self._data) <= self.max_conversations:
                break
            self._data.pop(cid)

    def history(self, conversation_id: str | None) -> list[Turn]:
        if not conversation_id:
            return []
        with self._lock:
            now = self._clock()
            self._expire(now)
            entry = self._data.get(conversation_id)
            return list(entry[1]) if entry else []

    def add(self, conversation_id: str | None, question: str, answer: str) -> None:
        if not conversation_id or not answer.strip():
            return
        answer = _CITATION.sub("", answer).strip()
        if len(answer) > MAX_ANSWER_CHARS:
            answer = answer[:MAX_ANSWER_CHARS].rsplit(" ", 1)[0] + " …"
        with self._lock:
            now = self._clock()
            _, turns = self._data.pop(conversation_id, (now, deque(maxlen=self.max_turns)))
            turns.append(Turn(question, answer))
            self._data[conversation_id] = (now, turns)  # vai para o fim: mais recente
            self._expire(now)

    def forget(self, conversation_id: str) -> None:
        with self._lock:
            self._data.pop(conversation_id, None)


MEMORY = ConversationMemory()
