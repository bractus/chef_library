"""Metadados por livro: regiao/cozinha, tipos de prato e outros atributos.

Duas camadas:
  1. Heuristica offline (regex sobre nome do arquivo + amostra do texto) —
     sempre disponivel, sem custo, usada como fallback e como primeiro
     palpite antes de chamar o agente.
  2. Classificacao por LLM (Claude Haiku 4.5 via OpenRouter) — refina a
     heuristica lendo titulos de receita + um trecho real do livro. Barata
     (Haiku, poucos tokens) e roda uma vez por livro, com o resultado salvo
     em JSON para nao pagar de novo em cada reconstrucao do indice.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from ..core.config import CONFIG, Config
from ..core.openrouter import client as _client
from ..core.text import fold

REGIONS = [
    "brasileira", "francesa", "italiana", "portuguesa", "espanhola",
    "mexicana", "latino_americana", "japonesa", "chinesa", "outras_asiaticas",
    "indiana", "do_oriente_medio", "africana", "nordica", "mediterranea",
    "americana", "internacional", "nao_especificado",
]

DISH_TYPES = [
    "entrada", "prato_principal", "sobremesa", "panificacao", "confeitaria",
    "bebida", "molho_e_conserva", "salgado_e_lanche", "sopa_e_caldo",
    "acompanhamento", "tecnica_e_fundamento", "historia_e_cultura",
]

_REGION_KEYWORDS: dict[str, list[str]] = {
    "francesa": ["frances", "française", "la cuisine", "paris", "escoffier", "bordeaux", "provence", "lyon"],
    "italiana": ["italian", "toscana", "roma", "sicilia", "pasta", "risotto", "parmigiano"],
    "portuguesa": ["portugu", "lisboa", "porto", "bacalhau", "algarve"],
    "espanhola": ["espanhol", "español", "tapas", "paella", "madrid"],
    "mexicana": ["mexican", "méxico", "taco", "tortilla", "mole "],
    "japonesa": ["japan", "japon", "sushi", "sashimi", "miso", "dashi"],
    "chinesa": ["chinese", "chines", "wok", "sichuan", "cantones"],
    "indiana": ["indian", "índia", "india", "curry", "masala", "tandoori"],
    "do_oriente_medio": ["oriente medio", "libanes", "turco", "marroquin", "hummus", "kebab", "tahine"],
    "africana": ["africana", "africano", "etiop", "magreb"],
    "nordica": ["nordic", "nordica", "escandinav", "noma", "redzepi", "dinamarqu", "sueco", "norueg"],
    "latino_americana": ["peruan", "argentin", "chilen", "colombian", "latino-americ", "latino americ"],
    "mediterranea": ["mediterran", "grega", "grego", "levante"],
    "americana": ["american", "usa", "texas", "bbq", "estados unidos"],
    "brasileira": ["brasil", "bahia", "baiana", "mineir", "nordestin", "feijão", "feijao",
                   "farofa", "tapioca", "acaraje", "acarajé", "moqueca", "cachaça", "cachaca"],
}

_DISH_KEYWORDS: dict[str, list[str]] = {
    "sobremesa": ["sobremesa", "doce", "bolo", "torta doce", "pudim", "brigadeiro", "cookie", "mousse", "sorvete"],
    "panificacao": ["pão", "pao ", "padaria", "fermenta", "focaccia", "brioche", "baguete", "pizza"],
    "confeitaria": ["confeitaria", "macaron", "petit gateau", "bombom", "trufa", "chocolate"],
    "bebida": ["drink", "coquetel", "cocktail", "suco", "vinho", "cerveja", "destilado", "licor"],
    "molho_e_conserva": ["molho", "sauce", "conserva", "geleia", "picles", "vinagrete"],
    "salgado_e_lanche": ["salgado", "lanche", "sanduiche", "sanduíche", "petisco", "aperitivo"],
    "sopa_e_caldo": ["sopa", "caldo", "creme de", "canja"],
    "acompanhamento": ["acompanhamento", "guarniç", "purê", "pure de"],
    "tecnica_e_fundamento": ["tecnica", "técnica", "fundamentos", "fermentação", "sous vide", "defumação", "molecular"],
    "historia_e_cultura": ["historia", "história", "cultura", "tradição", "origem do", "antropologia"],
}

_LANG_HINT = {
    "pt": "portugues", "en": "ingles", "fr": "frances", "it": "italiano", "es": "espanhol",
}


@dataclass
class BookMeta:
    book: str
    title: str
    lang: str
    tier: str
    region: str
    dish_types: list[str]
    tags: list[str]
    n_recipes: int
    source: str = "heuristica"   # "heuristica" | "llm"
    origin: str = "collection"   # "collection" (acervo original) | "upload" (aba Adicionar livros)
    added_at: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def heuristic_meta(book: str, clean_text_: str, lang: str, tier: str, n_recipes: int) -> BookMeta:
    """Primeiro palpite, so com regex — sem custo, sempre disponivel."""
    hay = fold(f"{book}\n{clean_text_[:8000]}")

    region = "nao_especificado"
    for r, kws in _REGION_KEYWORDS.items():
        if any(fold(kw) in hay for kw in kws):
            region = r
            break
    if region == "nao_especificado":
        region = "brasileira" if lang == "pt" else "internacional"

    dish_types = [d for d, kws in _DISH_KEYWORDS.items() if any(fold(kw) in hay for kw in kws)]
    if tier == "tecnica" and "tecnica_e_fundamento" not in dish_types:
        dish_types.append("tecnica_e_fundamento")
    if not dish_types:
        dish_types = ["prato_principal"]

    title = Path(book).stem.replace("Copy of ", "").replace("_", " ").strip()
    return BookMeta(book, title, lang, tier, region, dish_types, [], n_recipes, source="heuristica")


_SCHEMA = {
    "type": "object",
    "properties": {
        "region": {"type": "string", "enum": REGIONS},
        "dish_types": {"type": "array", "items": {"type": "string", "enum": DISH_TYPES}, "maxItems": 6},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
            "description": "outros atributos livres: dieta (vegano, sem_gluten, low_carb), estilo (classico, autoral, infantil), sub-regiao (baiana, mineira, nordestina), autor se identificavel",
        },
        "title_guess": {"type": "string", "description": "titulo real do livro, sem lixo de nome de arquivo"},
    },
    "required": ["region", "dish_types", "tags", "title_guess"],
    "additionalProperties": False,
}

_SYSTEM = (
    "Voce classifica livros de culinaria a partir de titulos de receita e um trecho de texto. "
    "Responda somente com o JSON pedido. 'region' e 'dish_types' devem vir exatamente das opcoes "
    "dadas no schema (nunca invente uma categoria nova nesses dois campos); 'tags' e livre para "
    "qualquer outro atributo util (dieta, sub-regiao, estilo, publico)."
)


def llm_meta(book: str, clean_text_: str, recipe_titles: list[str], lang: str, tier: str,
             n_recipes: int, cfg: Config = CONFIG, model: str | None = None) -> BookMeta:
    """Classificacao refinada via Claude Haiku 4.5 (barato) no OpenRouter.

    Levanta a excecao original se a chamada falhar — quem chama decide se
    cai para `heuristic_meta` (ver `classify_book`).
    """
    titles_block = "\n".join(f"- {t}" for t in recipe_titles[:25]) or "(sem titulos de receita detectados)"
    user = (
        f"Arquivo: {book}\n"
        f"Lingua predominante: {_LANG_HINT.get(lang, lang)}\n"
        f"Titulos de receita/secao encontrados:\n{titles_block}\n\n"
        f"Trecho do texto:\n{clean_text_[:2500]}"
    )
    resp = _client(cfg).chat.completions.create(
        model=model or "anthropic/claude-haiku-4.5",
        max_tokens=400,
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": {"name": "book_meta", "strict": True, "schema": _SCHEMA}},
    )
    data = json.loads(resp.choices[0].message.content)
    return BookMeta(
        book=book, title=data.get("title_guess") or Path(book).stem, lang=lang, tier=tier,
        region=data["region"], dish_types=data["dish_types"] or ["prato_principal"],
        tags=data.get("tags", []), n_recipes=n_recipes, source="llm",
    )


def classify_book(book: str, clean_text_: str, recipe_titles: list[str], lang: str, tier: str,
                   n_recipes: int, cfg: Config = CONFIG, use_llm: bool = True) -> BookMeta:
    """Tenta o LLM; se a key nao estiver configurada ou a chamada falhar, usa a heuristica."""
    if use_llm:
        try:
            cfg.require_openrouter()
            return llm_meta(book, clean_text_, recipe_titles, lang, tier, n_recipes, cfg)
        except Exception:
            pass
    return heuristic_meta(book, clean_text_, lang, tier, n_recipes)


def save_book_meta(meta: BookMeta, out_dir: Path, stem: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[^\w\-. ]', "_", stem or Path(meta.book).stem)[:150]
    path = out_dir / f"{safe}.json"
    path.write_text(meta.to_json(), encoding="utf-8")
    return path
