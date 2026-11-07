"""Application settings, read from the environment (and .env in development)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
