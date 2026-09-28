# Chef Library 🍳

A personal collection of nearly 750 gastronomy books — recipes, technique,
and history — that you can consult like talking to a research chef. Ask
about an ingredient, a technique, or the origin of a dish, in Portuguese or
English, and get an answer that always points to which book each piece of
information came from. When the collection has nothing on the topic, the
chef says so and looks the answer up elsewhere (general knowledge or the
web), making it clear it didn't come from the books.

## Built with

- **[Claude](https://claude.com)** (Anthropic) — the agent that reads the retrieved excerpts and answers, and also who wrote the project's code.
- **[Impeccable](https://impeccable.style)** — the design skill used to design the interface.

## How to run

You only need [Docker](https://www.docker.com/) installed.

1. Copy `.env.example` to `.env` and paste your [OpenRouter](https://openrouter.ai/keys) key into `OPENROUTER_API_KEY`.

2. Download the data folder to the local folder: https://drive.google.com/drive/folders/1eVC778JCQRmXJA8lawSlQdZlZOwFqqan?usp=sharing

3. Bring the project up:

   ```bash
   docker compose up --build
   ```

4. Open **http://localhost:5173** in your browser.

This starts the backend (search + agent) and the frontend together,
reusing the index already built in `data/` — nothing needs to be rebuilt.

## Adding books

Open the **Add books** tab and drop files in (several at once is fine).
Accepted formats: PDF, EPUB, DOCX, ODT, RTF, HTML, TXT and Markdown, with no
size limit. You can also pick or drop a whole folder. Uploads and processing
can be cancelled. Each book goes through the same cleaning and classification as
the rest of the collection and becomes searchable as soon as it shows
**Added** — no restart needed. You can leave the tab; processing continues
in the background.

- Files that aren't cooking books, that are already in the collection (even
  in another format), or that are password/DRM-protected are not added; the
  tab says why for each one.
- Scanned PDFs (images with no selectable text) stop at **No extractable
  text** and offer **Try with OCR**. OCR is slow (minutes to tens of minutes
  per book) and its quality depends on the scan.
- Adding books needs `OPENROUTER_API_KEY`: excerpt embeddings always go
  through OpenRouter.
- Books can't be removed or renamed from the tab. There is a maintenance
  command for that, to run with the backend stopped:

  ```bash
  docker compose stop backend
  docker compose run --rm backend python -m backend.ingest.remove_books --title "Exact Title" --dry-run
  docker compose run --rm backend python -m backend.ingest.remove_books --title "Exact Title"
  docker compose start backend
  ```

  Also delete the book's original from `books/`, or a `--rebuild` brings it back.
- OCR runs `OCR_WORKERS` Tesseract processes at once (default 2): each takes
  hundreds of MB, and with Docker Desktop's default memory more of them got
  the backend killed.

Where things live: the original files go to `books/`; excerpts, embeddings
and metadata go to `data/index/`; uploads in progress and the extracted-text
cache go to `data/ingest/`; the upload queue and history live in PostgreSQL 18
(the `db` service, stored in the `pgdata` Docker volume). `books/`, `data/`
and `pgdata` all survive restarts and image rebuilds. An older
`data/ingest/jobs.sqlite3` history is migrated to Postgres automatically on
startup and kept as `jobs.sqlite3.migrated`.

Don't edit `data/index/` from the host while the backend is running — do it
inside the container (`docker compose exec backend ...`) or with the backend
stopped. To look at the queue: `docker compose exec db psql -U chef chef_library`.

## Rebuilding the index

```bash
docker compose exec backend python -m backend.ingest.build_index
```

This processes any new file in `books/` (all the formats above) and then
re-generates every embedding. Useful flags: `--no-llm` (heuristic metadata,
free), `--skip-embed`, `--ocr` (OCR scanned PDFs, slow), `--rebuild`
(reprocess everything from scratch).

`--rebuild` only knows about the originals in `books/`. The downloaded
collection comes already indexed in `data/`, without its originals, so a
rebuild would wipe it: the command refuses to run while `books/` holds fewer
than 90% of the indexed books. Put the originals in `books/` first, or pass
`--force` if you really mean it.

## Development

```bash
pip install -r requirements-dev.txt
docker compose up -d db          # Postgres on localhost:5432, used by the app and by the tests
pytest tests/                    # each test runs in its own throwaway schema
uvicorn backend.api.main:app --reload --port 8000   # single process: the upload queue runs inside it
cd frontend && npm install && npm run dev
```

OCR outside Docker needs Tesseract (`brew install tesseract tesseract-lang`
on macOS).
