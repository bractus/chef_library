# Contract: `python -m backend.ingest.build_index` (mudanças)

O comportamento atual é mantido para `.md`. As mudanças servem a FR-025 e FR-026 (research R8).

## Entrada

| Antes | Depois |
|-------|--------|
| Lê só `books/*.md`. Outros formatos são ignorados com aviso. | Lê `books/*.{pdf,epub,docx,odt,rtf,html,htm,txt,md}`. Os demais continuam ignorados com aviso. |
| `path.read_text()` | `extract(path)`, que usa primeiro o cache `data/ingest/text/<md5>.txt` |

## Flags

| Flag | Status | Efeito |
|------|--------|--------|
| `--no-llm`, `--limit N`, `--skip-embed`, `--rebuild` | Inalterados | — |
| `--force` | **Novo** | Ignora a trava de segurança do `--rebuild` (ver abaixo) |
| `--ocr` | **Novo** | PDFs sem texto e sem cache passam por OCR (R5) e o texto vai para o cache. Sem o flag, esses PDFs são pulados com a linha `[ocr] <arquivo>: sem texto extraível, pulado (use --ocr)` e **não** entram em `processed.json`, para que uma execução futura com `--ocr` os pegue. |

## Concorrência e recuperação

- Ao iniciar, executa a recuperação do diário de commit (`data/index/commit.json`) e pega o lock exclusivo `data/index/.lock` até terminar. Se a API estiver fazendo um commit, o CLI espera o lock e imprime `[lock] aguardando a API terminar um commit...`.
- Ao terminar a fase 2, grava `manifest.json`. A API em execução detecta a mudança e recarrega o índice.

## Trava de segurança do `--rebuild` (novo)

O `--rebuild` apaga `chunks_raw.jsonl` e reconstrói só a partir do que estiver em `books/`. Neste repositório, o acervo original (~580 livros) veio pronto em `data/` e os originais **não** estão em `books/`. Agora que `books/` passa a existir com os envios da aba, um `--rebuild` descuidado apagaria o acervo original.

Regra: se o número de arquivos suportados em `books/` for menor que 90% do número de livros com `n_chunks > 0` em `processed.json`, o `--rebuild` para antes de apagar qualquer coisa, com:

```
[rebuild] books/ tem 3 arquivos, mas o acervo tem 580 livros indexados.
          Um rebuild agora apagaria 577 livros cujos originais nao estao em books/.
          Coloque os originais em books/ ou use --force para continuar mesmo assim.
```

Novo flag `--force`: ignora a trava.

## Garantia de rebuild (FR-025)

`--rebuild` reprocessa todo arquivo de `books/`, inclusive os enviados pela aba. Para livros que passaram por OCR, o texto vem do cache, sem refazer o OCR. Os trechos e metadados resultantes seguem as mesmas regras da ingestão pela aba, porque as etapas de processamento são compartilhadas.
