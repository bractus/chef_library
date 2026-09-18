"""Classifica um livro (ja limpo) como receitas / tecnica / nao-culinario.

Roda DEPOIS da limpeza de texto — classificar sobre o markdown cru
(tabelas embaralhadas por OCR) sub-conta sinal em livros classicos como
Larousse Gastronomique ou Japan: The Cookbook, cujo texto so fica legivel
apos a reconstrucao de paragrafos em clean.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .clean import garbage_run_ratio

# cabecalhos explicitos de receita (varias linguas)
_ING_HDR = re.compile(
    r'(?im)^\s*[*\-#>\s]*(ingredientes?|ingredients?|ingr[ée]dients?|ingredienti)\s*[:\-–]?\s*[*\s]*$'
)
_MET_HDR = re.compile(
    r'(?im)^\s*[*\-#>\s]*(modo\s+de\s+(preparo|fazer)|preparo|preparaç[ãa]o|preparation|'
    r'method|directions?|instructions?|pr[ée]paration|preparazione|procedimento|preparaci[óo]n)'
    r'\s*[:\-–]?\s*[*\s]*$'
)
# formato classico (Escoffier, livros franceses/italianos do sec. XIX-XX)
_CLASSIC_HDR = re.compile(
    r'(?im)^\s*(proportions?|proc[ée]d[ée])\s*[:.]', re.M
)
# linha "2 xicaras de farinha", "1/2 cup sugar", "200 g de manteiga"
_QTY_LINE = re.compile(
    r'(?im)^\s*[\*\-•]?\s*\d+[\d/,.\s]*\s*'
    r'(x[ií]caras?|colheres?\s+de|c\.?\s?s\.?|c\.?\s?ch\.?|gramas?|g\b|kg|ml|l\b|litros?|'
    r'cups?|tbsp|tsp|tablespoons?|teaspoons?|ounces?|oz\b|lb\b|pounds?|'
    r'cucchia\w+|cuiller\w+|taza|cucharad\w+|unidades?|dentes?\s+de|latas?|pitadas?)\b'
)
_YIELD = re.compile(
    r'(?im)^\s*(rendimento|rende|porç[õo]es|serves?|yield|makes|pour\s+\d+\s+personnes?|'
    r'per\s+\d+\s+persone|rinde)\b'
)
_CULINARY_WORD = re.compile(
    r'(?i)\b(receita|recipe|recette|ricetta|cozinh\w*|cook\w*|culin[aá]r\w*|chef|forno|oven|'
    r'fritar|assar|refogar|saut[eé]|massa|dough|molho|sauce|tempero|season\w*|panela|skillet)\b'
)
_NON_CULINARY = re.compile(
    r'(?i)\b(administraç[ãa]o de marketing|teoria geral da administraç|kotler|chiavenato|'
    r'segmentaç[ãa]o de mercado|vantagem competitiva|demonstraç[õo]es financeiras|fluxo de caixa|'
    r'balanced scorecard|spectrophotomet\w*|chromatograph\w*|titration|standard deviation|'
    r'regression analysis|p\s*<\s*0[.,]05|hplc|sds-page|plano de neg[oó]cios|'
    r'gest[ãa]o financeira|precificaç[ãa]o)\b'
)

TIER_RECIPES = "receitas"
TIER_TECHNIQUE = "tecnica"
TIER_NONCULINARY = "descartado"

# Acima deste tamanho, um livro sem NENHUMA estrutura procedural (cabecalho
# de ingrediente/preparo, formato classico "Proportions:/Procede:", ou linhas
# de quantidade) e tratado como obra de referencia enciclopedica (Larousse
# Gastronomique, McGee, Fennema...) e descartada — mesmo tendo vocabulario
# culinario denso, pois e feita para consulta pontual, nao para virar chunks
# de receita/tecnica coerentes.
_TOME_CHAR_FLOOR = 1_000_000
_TOME_QTY_P10K_CEIL = 0.5


@dataclass
class BookProfile:
    chars: int
    ing_headers: int
    met_headers: int
    classic_headers: int
    qty_lines: int
    yield_lines: int
    culinary_hits: int
    noncul_hits: int
    tier: str
    has_recipe_signal: bool


def profile_book(clean_text_: str) -> BookProfile:
    n = max(len(clean_text_) / 10_000.0, 1e-6)
    ing = len(_ING_HDR.findall(clean_text_))
    met = len(_MET_HDR.findall(clean_text_))
    classic = len(_CLASSIC_HDR.findall(clean_text_))
    qty = len(_QTY_LINE.findall(clean_text_))
    yld = len(_YIELD.findall(clean_text_))
    culin = len(_CULINARY_WORD.findall(clean_text_))
    noncul = len(_NON_CULINARY.findall(clean_text_))

    culin_p10k = culin / n
    noncul_p10k = noncul / n
    qty_p10k = qty / n
    classic_p10k = classic / n
    ing_p10k = ing / n
    met_p10k = met / n

    recipe_signal = (
        (ing >= 5 and met >= 3)
        or (ing >= 8 and met >= 4)
        or classic_p10k >= 1.0
        or qty_p10k >= 6
        or (ing >= 3 and qty_p10k >= 3)
    )

    # densidades, nao contagens brutas: num livro de milhoes de caracteres,
    # meia duzia de linhas que por acaso batem com um cabecalho de receita
    # (ex.: "Preparation" como legenda de coluna numa tabela de temperaturas)
    # sao ruido estatistico, nao evidencia de estrutura de receita real.
    is_reference_tome = (
        len(clean_text_) >= _TOME_CHAR_FLOOR
        and ing_p10k < 0.5 and met_p10k < 0.5 and classic_p10k < 0.3
        and qty_p10k < _TOME_QTY_P10K_CEIL
    )
    # livro majoritariamente ilegivel (OCR com caracteres transpostos) —
    # ver garbage_run_ratio em clean.py
    is_unreadable = garbage_run_ratio(clean_text_) > 0.25

    if is_unreadable or (noncul_p10k >= 0.5 and culin_p10k < 3):
        tier = TIER_NONCULINARY
    elif recipe_signal and not is_reference_tome:
        tier = TIER_RECIPES
    elif culin_p10k >= 1.5 and not is_reference_tome:
        tier = TIER_TECHNIQUE
    else:
        tier = TIER_NONCULINARY

    return BookProfile(
        chars=len(clean_text_), ing_headers=ing, met_headers=met, classic_headers=classic,
        qty_lines=qty, yield_lines=yld, culinary_hits=culin, noncul_hits=noncul,
        tier=tier, has_recipe_signal=recipe_signal,
    )
