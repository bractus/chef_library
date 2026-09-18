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
2. Bring the project up:

   ```bash
   docker compose up --build
   ```

3. Open **http://localhost:8000** in your browser.

This starts the backend (search + agent) and the frontend together,
reusing the index already built in `data/` — nothing needs to be rebuilt.
