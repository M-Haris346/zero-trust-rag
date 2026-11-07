"""Test database URLs, from the environment or .env."""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.exit(f"{name} is not set (copy .env.example to .env)", returncode=2)
    return value


TEST_APP_DATABASE_URL = _require_env("TEST_APP_DATABASE_URL")
TEST_MIGRATOR_DATABASE_URL = _require_env("TEST_MIGRATOR_DATABASE_URL")
