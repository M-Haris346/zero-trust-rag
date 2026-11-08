"""Deterministic terminal demo of the access-control story, for screen recordings.

Usage (API must already be running in dev mode with fake models):
    APP_ENV=dev LLM_PROVIDER=fake EMBEDDER=fake uv run python -m scripts.serve
    uv run python -m scripts.demo [--base-url http://127.0.0.1:8000]

Expects the seeded demo data (scripts/seed.py). Every step prints PASS (an
allowed action worked) or DENIED (a forbidden action was blocked, as it should
be). Anything else prints UNEXPECTED and the script exits 1. Documents the demo
creates are deleted at the end, even on failure. Tokens are never printed in
full (first 12 characters only).

Uses httpx, which is a dev dependency: this is a development tool, run via
`uv run`, which installs the dev group.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from app.auth import Principal, issue_token
from app.config import Settings
from scripts.seed import ACME, ALICE, BOB, CAROL, GLOBEX, HANDBOOK, SALARY_BANDS

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
SALARY_QUESTION = "What are the salary bands?"


def _colors(enabled: bool) -> dict[str, str]:
    codes = {"pass": "\033[32m", "denied": "\033[33m", "bad": "\033[31m", "dim": "\033[2m",
             "bold": "\033[1m", "reset": "\033[0m"}  # fmt: skip
    return codes if enabled else dict.fromkeys(codes, "")


@dataclass
class Demo:
    client: httpx.AsyncClient
    out: Callable[[str], None] = print
    color: bool = False
    # Signs a token for any (user, tenant), bypassing the dev endpoint's
    # membership check: simulates an issuer bug or a stolen secret. None = skip.
    forge: Callable[[UUID, UUID], str] | None = None
    tokens: dict[str, str] = field(default_factory=dict)
    created: list[tuple[str, UUID]] = field(default_factory=list)  # (owner, document id)
    failures: int = 0

    # --- output ------------------------------------------------------------

    def _c(self, name: str) -> str:
        return _colors(self.color)[name]

    def step(self, number: str, title: str) -> None:
        self.out(f"\n{self._c('bold')}{number}) {title}{self._c('reset')}")

    def check(self, expected: str, ok: bool, detail: str) -> None:
        """expected is "PASS" (should be allowed) or "DENIED" (should be blocked)."""
        if ok:
            tag = self._c("pass" if expected == "PASS" else "denied") + f"{expected:<10}"
        else:
            self.failures += 1
            tag = self._c("bad") + "UNEXPECTED"
        self.out(f"   {tag}{self._c('reset')} {detail}")

    def note(self, text: str) -> None:
        self.out(f"   {self._c('dim')}{text}{self._c('reset')}")

    # --- API helpers -------------------------------------------------------

    async def login(self, name: str, user: UUID, tenant: UUID) -> None:
        response = await self.client.post(
            "/auth/dev-token", json={"user_id": str(user), "tenant_id": str(tenant)}
        )
        if response.status_code == 404:
            raise DemoSetupError("the API has no /auth/dev-token: start it with APP_ENV=dev")
        response.raise_for_status()
        token = response.json()["access_token"]
        self.tokens[name] = token
        self.note(f"{name}: token {token[:12]}...")

    def auth(self, name: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[name]}"}

    async def doc_ids(self, name: str) -> set[UUID]:
        response = await self.client.get("/documents", headers=self.auth(name))
        response.raise_for_status()
        return {UUID(d["id"]) for d in response.json()["documents"]}

    async def can_get(self, name: str, document_id: UUID) -> int:
        response = await self.client.get(f"/documents/{document_id}", headers=self.auth(name))
        return response.status_code

    async def upload(self, name: str, title: str, content: str) -> UUID:
        response = await self.client.post(
            "/documents",
            headers=self.auth(name),
            json={"title": title, "visibility": "restricted", "content": content},
        )
        response.raise_for_status()
        document_id = UUID(response.json()["id"])
        self.created.append((name, document_id))
        return document_id

    async def delete(self, name: str, document_id: UUID) -> int:
        response = await self.client.delete(f"/documents/{document_id}", headers=self.auth(name))
        if response.status_code == 204:
            self.created.remove((name, document_id))
        return response.status_code

    async def ask(self, name: str, question: str) -> dict[str, Any]:
        response = await self.client.post(
            "/query", headers=self.auth(name), json={"question": question, "k": 8}
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    # --- the story ---------------------------------------------------------

    async def run(self) -> int:
        """Run every step. Returns the number of unexpected outcomes (0 = success)."""
        try:
            await self._steps()
        finally:
            await self.cleanup()
        return self.failures

    async def _steps(self) -> None:
        self.step("0", "Log in (dev tokens; identity comes only from the token)")
        await self.login("bob", BOB, ACME)
        await self.login("carol", CAROL, ACME)
        await self.login("alice", ALICE, ACME)

        self.step("a", "Listing documents: RLS decides, the query has no WHERE clause")
        bob, carol = await self.doc_ids("bob"), await self.doc_ids("carol")
        self.check("PASS", HANDBOOK in bob, "Bob sees the tenant-wide handbook")
        self.check(
            "DENIED", SALARY_BANDS not in bob, "Bob does not see the restricted salary bands"
        )
        self.check(
            "PASS", {HANDBOOK, SALARY_BANDS} <= carol, "Carol sees handbook + salary bands (grant)"
        )

        self.step("b", "Bob asks for the salary-bands document by id")
        status = await self.can_get("bob", SALARY_BANDS)
        self.check(
            "DENIED",
            status == 404,
            f"GET /documents/{SALARY_BANDS} -> {status} (not 403: no hint it exists)",
        )

        self.step("c", "Bob with a token claiming tenant Globex (he is not a member)")
        refused = await self.client.post(
            "/auth/dev-token", json={"user_id": str(BOB), "tenant_id": str(GLOBEX)}
        )
        self.check(
            "DENIED",
            refused.status_code == 403,
            f"dev-token refuses to issue it -> {refused.status_code}",
        )
        if self.forge is None:
            self.note("signed-token check skipped (needs JWT_SECRET); proven by")
            self.note("tests/api/test_documents.py::test_forged_tenant_claim_sees_nothing")
        else:
            token = self.forge(BOB, GLOBEX)
            self.tokens["bob@globex"] = token
            self.note(
                f"bob@globex: correctly signed token {token[:12]}... (issuer bug / stolen secret)"
            )
            forged = await self.doc_ids("bob@globex")
            self.check(
                "DENIED",
                forged == set(),
                f"GET /documents -> {len(forged)} documents (RLS: not a member)",
            )

        self.step("d", "Grants: Bob shares a restricted document with Carol, then revokes")
        notes = await self.upload(
            "bob", "Bob's private notes (demo)", "Draft plans for the Q3 offsite."
        )
        self.check(
            "PASS",
            await self.can_get("bob", notes) == 200,
            "Bob uploads a restricted document and can read it",
        )
        self.check("DENIED", await self.can_get("carol", notes) == 404, "Carol cannot read it")
        granted = await self.client.post(
            f"/documents/{notes}/grants/{CAROL}", headers=self.auth("bob")
        )
        self.check("PASS", granted.status_code == 204, f"Bob grants Carol -> {granted.status_code}")
        self.check("PASS", await self.can_get("carol", notes) == 200, "Carol can read it now")
        revoked = await self.client.delete(
            f"/documents/{notes}/grants/{CAROL}", headers=self.auth("bob")
        )
        self.check("PASS", revoked.status_code == 204, f"Bob revokes -> {revoked.status_code}")
        self.check(
            "DENIED",
            await self.can_get("carol", notes) == 404,
            "Carol has lost access on the very next request",
        )
        deleted = await self.delete("bob", notes)
        self.check("PASS", deleted == 204, f"Bob deletes his document -> {deleted}")

        self.step("e", "/query: answers only from documents the caller may read")
        salaries = await self.upload(
            "alice",
            "Salary bands 2026 (demo)",
            "Salary bands 2026: band L1 pays 60k to 80k, band L2 pays 80k to 110k.",
        )
        grant = await self.client.post(
            f"/documents/{salaries}/grants/{CAROL}", headers=self.auth("alice")
        )
        grant.raise_for_status()
        self.note("Alice (admin) uploads restricted 'Salary bands 2026' and grants Carol")
        bob_answer = await self.ask("bob", SALARY_QUESTION)
        self.check(
            "DENIED",
            bob_answer["status"] == "refused"
            and bob_answer["refusal_reason"] == "no_relevant_context",
            f"Bob: {bob_answer['status']} ({bob_answer['refusal_reason']}), "
            f"{len(bob_answer['citations'])} citations",
        )
        carol_answer = await self.ask("carol", SALARY_QUESTION)
        cited_docs = {UUID(c["document_id"]) for c in carol_answer["citations"]}
        self.check(
            "PASS",
            carol_answer["status"] == "answered" and cited_docs == {salaries},
            f"Carol: {carol_answer['status']}, cites {len(carol_answer['citations'])} chunk(s) "
            "from the granted salary document",
        )

        self.step("f", "No token at all")
        anonymous = await self.client.get("/documents")
        self.check(
            "DENIED", anonymous.status_code == 401, f"GET /documents -> {anonymous.status_code}"
        )

    async def cleanup(self) -> None:
        if not self.created:
            return
        self.step("-", "Cleanup")
        for owner, document_id in reversed(self.created):
            response = await self.client.delete(
                f"/documents/{document_id}", headers=self.auth(owner)
            )
            self.note(f"deleted demo document {str(document_id)[:8]}... -> {response.status_code}")
        self.created.clear()


class DemoSetupError(RuntimeError):
    pass


def _local_forger() -> Callable[[UUID, UUID], str] | None:
    """Uses the same .env as a locally running API. The secret is never printed."""
    load_dotenv()
    try:
        settings = Settings()
    except ValidationError:
        return None
    return lambda user, tenant: issue_token(settings, Principal(user_id=user, tenant_id=tenant))


async def _main(
    base_url: str, color: bool, forge: Callable[[UUID, UUID], str] | None = None
) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        try:
            (await client.get("/healthz")).raise_for_status()
        except httpx.HTTPError:
            print(
                f"error: the API is not reachable at {base_url}.\n"
                "Start it first:\n"
                "  APP_ENV=dev LLM_PROVIDER=fake EMBEDDER=fake uv run python -m scripts.serve",
                file=sys.stderr,
            )
            return 2
        demo = Demo(client=client, color=color, forge=forge)
        try:
            failures = await demo.run()
        except DemoSetupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    summary = "all steps behaved as expected" if failures == 0 else f"{failures} UNEXPECTED"
    print(f"\n{summary}")
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="zero-trust-rag access-control demo")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(_main(args.base_url, color, _local_forger()), loop_factory=loop_factory)


if __name__ == "__main__":
    sys.exit(main())
