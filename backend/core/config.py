"""Configuracao central, lida do .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# backend/core/config.py -> backend/core -> backend -> raiz do repositorio
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _path(env: str, default: str) -> Path:
    raw = os.getenv(env, default)
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def _int(env: str, default: int) -> int:
    try:
        return int(os.getenv(env, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # --- caminhos ---
    books_raw: Path = field(default_factory=lambda: _path("BOOKS_RAW_DIR", "books"))
    books_clean: Path = field(default_factory=lambda: _path("BOOKS_CLEAN_DIR", "books_md"))
    index_dir: Path = field(default_factory=lambda: _path("INDEX_DIR", "data/index"))
    # staging dos uploads e cache do texto extraido da aba de ingestao
    ingest_dir: Path = field(default_factory=lambda: _path("INGEST_DIR", "data/ingest"))
    # processos do Tesseract em paralelo: cada um ocupa centenas de MB a 300 DPI,
    # e com 4 (um por nucleo) a VM do Docker (6 GB) matou o backend por falta de memoria
    ocr_workers: int = field(default_factory=lambda: _int("OCR_WORKERS", 2))
    # fila/historico da ingestao; no Docker o compose aponta para o servico "db"
    database_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL", "postgresql://chef:chef@localhost:5432/chef_library"))

    # --- embeddings (via OpenRouter /v1/embeddings, mesma key do agente) ---
    embed_model: str = field(default_factory=lambda: os.getenv("EMBED_MODEL", "openai/text-embedding-3-small"))
    embed_batch_size: int = field(default_factory=lambda: _int("EMBED_BATCH_SIZE", 500))

    # --- agente: OpenRouter (tambem usado pelos embeddings, sempre) ---
    openrouter_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-5"))
    app_url: str = field(default_factory=lambda: os.getenv("OPENROUTER_APP_URL", "http://localhost:8501"))
    app_name: str = field(default_factory=lambda: os.getenv("OPENROUTER_APP_NAME", "Chef Library"))

    # --- agente: provedores alternativos, nesta ordem de prioridade quando
    # mais de uma key estiver configurada: OpenRouter > OpenAI > Anthropic >
    # Ollama local (ver backend/core/providers.py) ---
    openai_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    anthropic_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"))
    # default aponta pro host via Docker (ver extra_hosts no docker-compose);
    # rodando o backend fora de container, sobrescreva para localhost:11434
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1"))
    # qwen2.5:7b-instruct, nao qwen2.5:3b: o 3B ignorava a instrucao de
    # idioma e classificava mal perguntas culinarias inequivocas (testado
    # nesta maquina); 7B segue instrucoes com bem mais confiabilidade — e
    # uma propriedade conhecida de escala de modelo, nao retestada aqui.
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct"))

    # --- busca web do agente, ferramenta web_search (ver backend/agent/websearch.py) ---
    # independente do provedor do agente: o truque ":online" do OpenRouter
    # so funciona nele mesmo, entao a busca de verdade para os outros tres
    # provedores (openai/anthropic/ollama) depende desta key.
    tavily_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))

    @property
    def faiss_path(self) -> Path:
        return self.index_dir / "chunks.faiss"

    @property
    def meta_path(self) -> Path:
        return self.index_dir / "chunks.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    @property
    def processed_path(self) -> Path:
        return self.index_dir / "cache" / "processed.json"

    @property
    def chunks_raw_path(self) -> Path:
        return self.index_dir / "chunks_raw.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.index_dir / ".lock"

    @property
    def commit_journal_path(self) -> Path:
        return self.index_dir / "commit.json"

    @property
    def legacy_jobs_db_path(self) -> Path:
        """Banco SQLite da fila antes do Postgres — so lido pela migracao."""
        return self.ingest_dir / "jobs.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.ingest_dir / "uploads"

    @property
    def text_cache_dir(self) -> Path:
        return self.ingest_dir / "text"

    @property
    def ingest_enabled(self) -> bool:
        """Embeddings sempre passam pelo OpenRouter, entao sem key nao ha ingestao."""
        try:
            self.require_openrouter()
        except RuntimeError:
            return False
        return True

    def require_openrouter(self) -> str:
        key = self.openrouter_key.strip()
        if not key or key.startswith("sk-or-v1-xxx"):
            raise RuntimeError(
                "OPENROUTER_API_KEY nao configurada. Copie .env.example para .env "
                "e coloque sua key de https://openrouter.ai/keys"
            )
        return key


CONFIG = Config()
