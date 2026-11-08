"""scripts/demo.py step logic, run against the real test app (dev mode, fakes)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest

from app.auth import Principal, issue_token
from scripts import demo
from scripts.seed import seed
from tests.api.client import build_app, make_settings, serve
from tests.dataset import ALL_TABLES
from tests.env import TEST_MIGRATOR_DATABASE_URL


@pytest.fixture
async def seeded_client(migrator: psycopg.Connection) -> AsyncIterator[httpx.AsyncClient]:
    # The demo expects the scripts/seed.py data, not the test dataset.
    migrator.execute(f"TRUNCATE {', '.join(ALL_TABLES)} CASCADE")
    seed(TEST_MIGRATOR_DATABASE_URL)
    async with serve(build_app(make_settings(app_env="dev"))) as client:
        yield client


def _forge(user: object, tenant: object) -> str:
    return issue_token(make_settings(), Principal(user_id=user, tenant_id=tenant))  # type: ignore[arg-type]


async def test_every_demo_step_behaves_as_expected(
    seeded_client: httpx.AsyncClient, migrator: psycopg.Connection
) -> None:
    lines: list[str] = []
    run = demo.Demo(client=seeded_client, out=lines.append, forge=_forge)
    failures = await run.run()

    output = "\n".join(lines)
    assert failures == 0, output
    assert "UNEXPECTED" not in output
    assert output.count("PASS") >= 7 and output.count("DENIED") >= 8
    for step in ("0)", "a)", "b)", "c)", "d)", "e)", "f)"):
        assert step in output

    # Tokens are never printed in full.
    assert len(run.tokens) == 4
    for token in run.tokens.values():
        assert token not in output
        assert f"{token[:12]}..." in output

    # Cleanup removed everything the demo created.
    titles = {r[0] for r in migrator.execute("SELECT title FROM documents")}
    assert not {t for t in titles if "(demo)" in t}


async def test_demo_reports_unexpected_outcome(
    seeded_client: httpx.AsyncClient, migrator: psycopg.Connection
) -> None:
    # Control: if access control were broken (here: Bob granted the salary
    # bands), the demo must say UNEXPECTED, not PASS.
    migrator.execute(
        "INSERT INTO document_grants (document_id, tenant_id, user_id) VALUES (%s, %s, %s)",
        (demo.SALARY_BANDS, demo.ACME, demo.BOB),
    )
    lines: list[str] = []
    failures = await demo.Demo(client=seeded_client, out=lines.append, forge=_forge).run()
    assert failures >= 1
    assert "UNEXPECTED" in "\n".join(lines)


async def test_without_secret_the_signed_token_check_is_skipped_not_faked(
    seeded_client: httpx.AsyncClient,
) -> None:
    lines: list[str] = []
    failures = await demo.Demo(client=seeded_client, out=lines.append).run()
    output = "\n".join(lines)
    assert failures == 0, output
    assert "dev-token refuses to issue it -> 403" in output
    assert "signed-token check skipped" in output


async def test_api_not_running_gives_clear_message(capsys: pytest.CaptureFixture[str]) -> None:
    code = await demo._main("http://127.0.0.1:9", color=False)
    assert code == 2
    err = capsys.readouterr().err
    assert "not reachable at http://127.0.0.1:9" in err
    assert "python -m scripts.serve" in err


async def test_api_without_dev_mode_gives_clear_message(
    migrator: psycopg.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    async with serve(build_app(make_settings(app_env="prod"))) as client:
        with pytest.raises(demo.DemoSetupError, match="APP_ENV=dev"):
            await demo.Demo(client=client, out=lambda _: None).run()
