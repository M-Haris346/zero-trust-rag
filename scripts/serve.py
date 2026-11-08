"""Run the API locally.

Usage:
    uv run python -m scripts.serve [--host 127.0.0.1] [--port 8000]

Run it with -m (as a module) so the repo root is on sys.path and `app` imports.

Why a script instead of plain `uvicorn`: psycopg's async mode cannot use
Windows' default Proactor event loop, so on Windows we run uvicorn on a
SelectorEventLoop ourselves. On Linux/macOS this is the normal asyncio loop.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn
from dotenv import load_dotenv
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from app.rag.embedder import EmbedderUnavailableError


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the zero-trust-rag API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    try:
        app = create_app(Settings())
    except ValidationError as exc:
        print(f"configuration error (check .env):\n{exc}", file=sys.stderr)
        return 2
    except EmbedderUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port))
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(server.serve(), loop_factory=loop_factory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
