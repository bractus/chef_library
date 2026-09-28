"""Utilitarios de texto compartilhados pelo pipeline e pela busca.

Estavam duplicados em clean/chunk/store/metadata/build_index; qualquer
divergencia entre as copias (ex.: mudar o folding so na busca) quebraria
silenciosamente o casamento entre o que foi indexado e o que e procurado.
"""
from __future__ import annotations

import re
import unicodedata

# linha inteira em caixa alta — candidata a titulo de secao/receita
TITLE_LIKE = re.compile(r'^[A-ZÀ-Ü0-9][A-ZÀ-Ü0-9\s\'’«»\-,.&()/ºª%]{2,72}$')

MAX_TITLE_CHARS = 80
UPPERCASE_TITLE_RATIO = 0.8


def fold(s: str) -> str:
    """Minuscula sem acento — para casar 'acucar' com 'açúcar'."""
    n = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in n if not unicodedata.combining(c))


def uppercase_ratio(s: str) -> float:
    """Fracao de letras maiusculas; 0.0 se nao houver letra alguma."""
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c.isupper() for c in letters) / len(letters)
