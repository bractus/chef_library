"""Configuracao central, lida do .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
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

    # --- embeddings (via OpenRouter /v1/embeddings, mesma key do agente) ---
    embed_model: str = field(default_factory=lambda: os.getenv("EMBED_MODEL", "openai/text-embedding-3-small"))
    embed_batch_size: int = field(default_factory=lambda: _int("EMBED_BATCH_SIZE", 500))

    # --- agente (OpenRouter) ---
    openrouter_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-5"))
    app_url: str = field(default_factory=lambda: os.getenv("OPENROUTER_APP_URL", "http://localhost:8501"))
    app_name: str = field(default_factory=lambda: os.getenv("OPENROUTER_APP_NAME", "Chef Library"))

    @property
    def faiss_path(self) -> Path:
        return self.index_dir / "chunks.faiss"

    @property
    def meta_path(self) -> Path:
        return self.index_dir / "chunks.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    def require_openrouter(self) -> str:
        key = self.openrouter_key.strip()
        if not key or key.startswith("sk-or-v1-xxx"):
            raise RuntimeError(
                "OPENROUTER_API_KEY nao configurada. Copie .env.example para .env "
                "e coloque sua key de https://openrouter.ai/keys"
            )
        return key


CONFIG = Config()
