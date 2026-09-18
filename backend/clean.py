"""Limpeza de markdown extraido de PDF/EPUB: remove ruido de OCR e layout,
preserva conteudo util (receitas, tecnicas, historia).

O material vem de conversoes de PDF de qualidade variavel. O padrao de ruido
mais comum e paginas em multiplas colunas viradas, pelo conversor, em tabelas
markdown (`| ... | ... |`) que embaralham a ordem das frases. Nao ha como
recuperar a ordem original com certeza, entao tratamos cada linha de tabela
como um saco de celulas e as rejuntamos: perde-se a formatacao fina mas o
vocabulario (ingredientes, tecnicas, nomes) sobrevive para a busca semantica.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Padroes de ruido puro (nunca aparecem dentro de uma receita de verdade)
# ---------------------------------------------------------------------------

_SEP_ROW = re.compile(r'^\s*\|?[\s|:\-]{3,}\|?\s*$')                  # | --- | --- |
_PAGE_NUM = re.compile(r'^\s*[\-–—]?\s*\d{1,4}\s*[\-–—]?\s*$')        # "42", "- 42 -"
_ROMAN = re.compile(r'^\s*[ivxlcdmIVXLCDM]{1,7}\s*$')
_DOT_LEADER = re.compile(r'^[.\s]{10,}$')
_DIGIT_RUN = re.compile(r'^\s*[\d\s.\-–—/]{6,}\s*$')                  # "1 2 3 4 5", "12.34.56"
_URL_ONLY = re.compile(r'^\s*(https?://|www\.)\S+\s*$', re.I)
_ISBN = re.compile(r'^\s*ISBN\b', re.I)

# Uma linha de tabela markdown: comeca e/ou termina com "|", tem >=1 "|" no meio
_TABLE_ROW = re.compile(r'^\s*\|.*\|\s*$|^\s*[^|\n]*\|[^|\n]*\|')

# Frases de marketing / boilerplate que se repetem em ebooks de receita gratis
_SPAM_LINE = re.compile(
    r'(?i)(direitos\s+reservados|todos\s+os\s+direitos|copyright\s*©|'
    r'instagram\s*:?\s*@|@[a-z0-9_.]+(receitas|cook|chef|food)|'
    r'siga.{0,20}(instagram|facebook)|compartilhe\s+esta\s+receita|'
    r'baixe\s+(o\s+)?e-?book|clique\s+aqui|acesse\s+o\s+link)'
)

# Cabecalhos de receita legitimos que NUNCA devem ser removidos mesmo
# repetindo dezenas de vezes ao longo do livro (uma vez por receita)
_SCAFFOLD_WHITELIST = re.compile(
    r'(?im)^\s*[\*\-#>\s]*('
    r'ingredientes?|modo\s+de\s+(preparo|fazer)|preparo|preparaç[ãa]o|'
    r'rendimento|rende|porç[õo]es|tempo\s+de\s+preparo|dica[s]?|observaç[õo]es?|'
    r'ingredients?|instructions?|directions?|method|preparation|yield|serves?|'
    r'ingr[ée]dients?|pr[ée]paration|ingredienti|preparazione|'
    r'ingredientes|preparaci[óo]n|proportions?|proc[ée]d[ée]'
    r')\s*[:\-–]?\s*[\*\s]*$'
)


def _strip_table_row(line: str) -> str | None:
    """Rejunta as celulas de uma linha de tabela markdown em texto corrido.

    Retorna None se a linha nao sobra nada util (todas as celulas vazias ou
    so pontuacao), sinalizando para descarta-la.
    """
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    cells = [c for c in cells if c and not _SEP_ROW.match(f"|{c}|") and re.search(r'[A-Za-zÀ-ÿ0-9]', c)]
    if len(cells) < 1:
        return None
    joined = " ".join(cells)
    # celula unica de 1-2 chars (lixo tipo "A", "*", "1") não vale a pena
    if len(joined) < 3:
        return None
    return joined


def _is_noise_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False  # linhas em branco tratadas a parte
    if _PAGE_NUM.match(s) or _ROMAN.match(s) or _DOT_LEADER.match(s):
        return True
    if _DIGIT_RUN.match(s) and not re.search(r'[A-Za-zÀ-ÿ]', s):
        return True
    if _URL_ONLY.match(s) or _ISBN.match(s):
        return True
    if _SPAM_LINE.search(s) and len(s) < 200:
        return True
    return False


_SENTENCE_END = re.compile(r'[.!?:;)"”’»]\s*$')
_LIST_START = re.compile(r'^\s*([\-*•▪◦]|\d{1,2}[.)]|\(\d{1,2}\))\s+\S')
_TITLE_LIKE = re.compile(r'^[A-ZÀ-Ü0-9][A-ZÀ-Ü0-9\s\'’«»\-,.&()/ºª%]{2,72}$')  # linha toda em maiusculas


def _looks_like_title(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 80:
        return False
    if _SCAFFOLD_WHITELIST.match(s):
        return True
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and _TITLE_LIKE.match(s) is not None and sum(c.isupper() for c in letters) / len(letters) > 0.8


def _unwrap_paragraphs(lines: list[str], max_len: int = 2000) -> list[str]:
    """Rejunta linhas curtas que sao continuacao do paragrafo anterior.

    Extratores de PDF frequentemente quebram uma linha a cada ~40-60
    caracteres em vez de a cada frase. Isso destroi tanto a legibilidade
    quanto a deteccao de boilerplate repetido (uma frase de propaganda
    fragmentada em 4 linhas nunca bate 4 vezes identica). Junta linha[i]
    a linha[i-1] quando a anterior nao termina em pontuacao terminal e a
    atual nao comeca um novo bloco (titulo, lista, linha em branco).
    """
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            out.append("")
            continue
        prev = out[-1] if out else ""
        can_merge = (
            bool(prev)
            and not _SENTENCE_END.search(prev)
            and not _looks_like_title(prev)
            and not _looks_like_title(s)
            and not _LIST_START.match(s)
            and len(prev) < max_len
        )
        if can_merge:
            out[-1] = f"{prev} {s}"
        else:
            out.append(s)
    return out


def _dehyphenate(text: str) -> str:
    """Junta palavras quebradas por hifen de fim de linha: 'coz-\\ninha' -> 'cozinha'."""
    return re.sub(r'(\w)-\n(\w)', r'\1\2', text)


_WORD_OR_PUNCT = re.compile(r'[A-Za-zÀ-ÿ]+|\S')
_ALPHA_WORD = re.compile(r'^[A-Za-zÀ-ÿ]+$')


def garbage_run_ratio(text: str, sample: int = 40_000, min_run: int = 4) -> float:
    """Fracao de palavras que fazem parte de uma sequencia de `min_run`+
    tokens consecutivos de 1 caractere — a assinatura de um extrator de PDF
    que transpos caracteres em vez de extrair palavras (ex.: um titulo em
    fonte decorativa virando "s t r e e s t e p s e e P"). Isso e raro e
    localizado, entao aplicamos por chunk, nao so por livro inteiro: um
    unico paragrafo ilegivel nao deve descartar o livro todo, mas nao pode
    ir parar no indice como se fosse uma receita de verdade.
    """
    t = text[:sample]
    tokens = _WORD_OR_PUNCT.findall(t)
    words = [w for w in tokens if _ALPHA_WORD.match(w)]
    if len(words) < 20:
        return 0.0
    in_run = run_len = 0
    for w in words:
        if len(w) == 1:
            run_len += 1
        else:
            if run_len >= min_run:
                in_run += run_len
            run_len = 0
    if run_len >= min_run:
        in_run += run_len
    return in_run / len(words)


def _fold(s: str) -> str:
    n = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in n if not unicodedata.combining(c))


def clean_text(raw: str, *, repeat_threshold: int = 5) -> str:
    """Aplica todas as regras de limpeza e devolve o texto pronto para chunking.

    Ordem importa: de-hifenizacao primeiro (opera sobre quebras de linha cruas),
    depois tratamento linha a linha de tabelas/ruido, por ultimo remocao de
    boilerplate repetido (precisa ver o arquivo inteiro) e normalizacao de
    linhas em branco.
    """
    if not raw:
        return ""

    text = _dehyphenate(raw)
    lines = text.split("\n")

    # 1) linha a linha: tabelas viram texto corrido, ruido puro cai fora
    out_lines: list[str] = []
    for line in lines:
        if _SEP_ROW.match(line):
            continue
        if _TABLE_ROW.match(line):
            salvaged = _strip_table_row(line)
            if salvaged is not None:
                out_lines.append(salvaged)
            continue
        if _is_noise_line(line):
            continue
        out_lines.append(line.rstrip())

    # 2) rejunta paragrafos quebrados linha a linha pelo extrator de PDF
    #    ("Todos os direitos\nreservados..." -> "Todos os direitos reservados...").
    #    Sem isso, frases de boilerplate ficam fragmentadas demais para o
    #    filtro de spam e para o repeat-count do passo seguinte reconhecer.
    out_lines = _unwrap_paragraphs(out_lines)

    # 3) boilerplate repetido (cabecalho/rodape de pagina, propaganda) —
    #    nunca remove linhas do whitelist de scaffolding de receita
    from collections import Counter

    counts = Counter(l.strip() for l in out_lines if len(l.strip()) > 3)
    n = max(len(out_lines), 1)
    dyn_threshold = max(repeat_threshold, n // 150)
    noisy_repeats = {
        l for l, c in counts.items()
        if c >= dyn_threshold and not _SCAFFOLD_WHITELIST.match(l)
    }
    if noisy_repeats:
        out_lines = [l for l in out_lines if l.strip() not in noisy_repeats]
    # spam pego so depois do unwrap (frases inteiras, nao fragmentos)
    out_lines = [l for l in out_lines if not (l.strip() and _SPAM_LINE.search(l) and len(l.strip()) < 220)]

    # 4) normaliza linhas em branco (no maximo 1 linha vazia seguida)
    cleaned: list[str] = []
    blank_run = 0
    for l in out_lines:
        if not l.strip():
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        cleaned.append(l)

    return "\n".join(cleaned).strip()


@dataclass
class CleanStats:
    raw_chars: int
    clean_chars: int
    raw_lines: int
    clean_lines: int

    @property
    def kept_ratio(self) -> float:
        return round(self.clean_chars / max(self.raw_chars, 1), 3)


def clean_with_stats(raw: str) -> tuple[str, CleanStats]:
    cleaned = clean_text(raw)
    stats = CleanStats(len(raw), len(cleaned), raw.count("\n") + 1, cleaned.count("\n") + 1)
    return cleaned, stats
