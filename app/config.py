"""Application settings, read from the environment (and .env in development)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Defaults to "prod" so dev-only features stay off unless explicitly enabled.
    app_env: Literal["dev", "test", "prod"] = "prod"

    # Must connect as app_user (NOBYPASSRLS). SecretStr keeps the password out
    # of reprs and logs. No default: a missing value is a startup error.
    app_database_url: SecretStr

    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    # HS256 shared secret. Demo only; see ADR 0005. No default.
    jwt_secret: SecretStr
    jwt_issuer: str = "zero-trust-rag"
    jwt_audience: str = "zero-trust-rag-api"
    jwt_ttl_seconds: int = Field(default=3600, gt=0)
    # Accepted clock difference between token issuer and this server.
    jwt_leeway_seconds: int = Field(default=30, ge=0)

    # "fastembed" needs `uv sync --extra embeddings`. "fake" is deterministic
    # and has no semantic meaning: for tests and offline demos only.
    embedder: Literal["fastembed", "fake"] = "fastembed"

    @field_validator("jwt_secret")
    @classmethod
    def _secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required values come from the environment
