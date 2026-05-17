"""Application configuration loaded from environment variables.

We use pydantic-settings so values can come from .env files in dev or from
docker-compose environment in production. Validation fails fast at boot.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # OpenAI
    openai_api_key: str | None = Field(default=None)
    extraction_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # HTTP
    host: str = "0.0.0.0"
    port: int = 8080
    memory_auth_token: str | None = None
    max_request_bytes: int = 1_000_000  # 1 MiB

    # Storage
    db_path: Path = Path("/data/memory.db")

    # Recall tuning
    reranker_enabled: bool = False
    # RRF naturally produces small scores (~0.016 per rank-1 contribution),
    # so the noise threshold lives below that floor. Multi-hop boosts move
    # legitimately relevant items well above 0.5.
    min_recall_score: float = 0.01
    rrf_k: int = 60
    bm25_top_k: int = 30
    vector_top_k: int = 30
    # Cosine-similarity floor for vector hits. text-embedding-3-small puts
    # genuinely unrelated text well below 0.4; the floor keeps off-topic
    # queries from leaking weak vector matches into recall.
    vector_score_floor: float = 0.35

    # Logging
    log_level: str = "INFO"

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Lazy singleton so tests can monkeypatch env vars before first access."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Test helper: clear the settings singleton."""
    global _settings
    _settings = None
