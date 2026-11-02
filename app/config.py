"""Application configuration.

All settings are read from environment variables prefixed with ``LLMFORGE_``
(or from a local ``.env`` file during development). Nothing secret has a
default value.
"""

from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_API_KEY_LENGTH = 16


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLMFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- Service -----------------------------------------------------------
    app_name: str = "LLMForge"
    environment: str = "local"
    host: str = "0.0.0.0"  # noqa: S104 - bound inside the container; host port mapping is localhost-only
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    docs_enabled: bool = True

    # --- Model provider ----------------------------------------------------
    provider: str = "ollama"
    ollama_base_url: str = "http://ollama:11434"
    ollama_keep_alive: str = "10m"
    default_model: str = "llama3.2:1b"
    # Comma-separated allowlist. Empty means "only the default model".
    allowed_models_csv: str = Field(default="", alias="LLMFORGE_ALLOWED_MODELS")
    model_probe_interval_seconds: float = Field(default=30.0, ge=0)

    # --- Timeouts ----------------------------------------------------------
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    generation_timeout_seconds: float = Field(default=120.0, gt=0)
    probe_timeout_seconds: float = Field(default=5.0, gt=0)

    # --- Request limits ----------------------------------------------------
    max_prompt_chars: int = Field(default=8000, ge=1, le=100_000)
    max_request_body_bytes: int = Field(default=65_536, ge=1024)
    default_max_tokens: int = Field(default=256, ge=1)
    max_output_tokens: int = Field(default=1024, ge=1)

    # --- Authentication ----------------------------------------------------
    auth_enabled: bool = True
    # Comma-separated list of accepted API keys.
    api_keys: SecretStr = SecretStr("")

    # --- Rate limiting and concurrency --------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = Field(default=30, ge=1)
    rate_limit_burst: int = Field(default=10, ge=1)
    max_concurrent_generations: int = Field(default=2, ge=1)
    queue_timeout_seconds: float = Field(default=10.0, ge=0)

    @cached_property
    def allowed_models(self) -> list[str]:
        models = _split_csv(self.allowed_models_csv)
        if self.default_model not in models:
            models.insert(0, self.default_model)
        return models

    @cached_property
    def api_key_list(self) -> list[str]:
        return _split_csv(self.api_keys.get_secret_value())

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if self.auth_enabled:
            keys = _split_csv(self.api_keys.get_secret_value())
            if not keys:
                raise ValueError(
                    "LLMFORGE_AUTH_ENABLED is true but LLMFORGE_API_KEYS is empty. "
                    "Generate a key (e.g. `openssl rand -hex 32`) or run scripts/setup.sh."
                )
            if any(len(k) < MIN_API_KEY_LENGTH for k in keys):
                raise ValueError(f"Every API key must be at least {MIN_API_KEY_LENGTH} characters long.")
        if self.default_max_tokens > self.max_output_tokens:
            raise ValueError("LLMFORGE_DEFAULT_MAX_TOKENS must not exceed LLMFORGE_MAX_OUTPUT_TOKENS.")
        return self


def get_settings() -> Settings:
    return Settings()
