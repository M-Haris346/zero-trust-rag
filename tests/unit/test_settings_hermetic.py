"""Guard: tests must not depend on the developer's local .env.

Constructing the Settings class directly, or calling get_settings, reads .env
and the process environment. Such a test passes locally (where .env sets
JWT_SECRET, EMBEDDER, ...) and fails in CI. Tests build settings through
tests/api/client.py:make_settings(), which passes every value explicitly and
disables the .env file.
"""

from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
# Assembled from parts so this file does not match its own scan.
FORBIDDEN = ("Settings" + "(", "get_settings" + "(")
ALLOWED = {
    TESTS_DIR / "api" / "client.py",  # defines make_settings() itself
    TESTS_DIR / "unit" / "test_serve.py",  # tests env loading on purpose
}


def test_no_test_builds_settings_from_the_environment() -> None:
    files = sorted(TESTS_DIR.rglob("*.py"))
    assert len(files) > 20, "control: the scan must actually see the test files"
    offenders = [
        f"{path.relative_to(TESTS_DIR)}:{lineno}: {line.strip()}"
        for path in files
        if path.resolve() not in ALLOWED
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(pattern in line for pattern in FORBIDDEN)
    ]
    assert offenders == [], "use tests.api.client.make_settings() instead:\n" + "\n".join(offenders)
