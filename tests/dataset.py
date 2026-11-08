"""The canonical RLS test dataset. Loaded fresh (as app_migrator) before each test.

    Tenant Acme:   alice (admin), bob (member), carol (member)
    Tenant Globex: dave (member)
    eve:           user with no memberships at all

    T1  Acme    visibility=tenant      created by bob
    R1  Acme    visibility=restricted  created by alice, granted to carol
    R2  Acme    visibility=restricted  created by bob, no grants
    G1  Globex  visibility=tenant      created by dave

Every document has CHUNKS_PER_DOC chunks.

Who can read what (the matrix every read test checks):
    alice  -> T1, R1, R2   (admin sees everything in Acme)
    bob    -> T1, R2       (R2 because he created it)
    carol  -> T1, R1       (R1 through her grant)
    dave   -> G1
    eve    -> nothing
"""

from __future__ import annotations

import math
import random
from uuid import UUID

import psycopg

ACME = UUID("00000000-0000-4000-8000-0000000000a1")
GLOBEX = UUID("00000000-0000-4000-8000-0000000000b1")

ALICE = UUID("00000000-0000-4000-8000-000000000001")
BOB = UUID("00000000-0000-4000-8000-000000000002")
CAROL = UUID("00000000-0000-4000-8000-000000000003")
DAVE = UUID("00000000-0000-4000-8000-000000000004")
EVE = UUID("00000000-0000-4000-8000-000000000005")

T1 = UUID("00000000-0000-4000-8000-00000000d001")
R1 = UUID("00000000-0000-4000-8000-00000000d002")
R2 = UUID("00000000-0000-4000-8000-00000000d003")
G1 = UUID("00000000-0000-4000-8000-00000000d004")

DOC_TENANT = {T1: ACME, R1: ACME, R2: ACME, G1: GLOBEX}
CHUNKS_PER_DOC = 2
EMBEDDING_DIM = 384

ALL_TABLES = ("tenants", "users", "memberships", "documents", "document_grants", "chunks")

EMAILS = {
    ALICE: "alice@acme.test",
    BOB: "bob@acme.test",
    CAROL: "carol@acme.test",
    DAVE: "dave@globex.test",
    EVE: "eve@nowhere.test",
}
TITLES = {
    T1: "T1 tenant-wide",
    R1: "R1 restricted",
    R2: "R2 restricted",
    G1: "G1 tenant-wide",
}
# Strings that must never be sent to an LLM by the application (ADR 0007).
IDENTITY_STRINGS = (
    [str(u) for u in (ACME, GLOBEX, ALICE, BOB, CAROL, DAVE, EVE)]
    + list(EMAILS.values())
    + list(TITLES.values())
)

READABLE: dict[tuple[UUID, UUID], set[UUID]] = {
    (ALICE, ACME): {T1, R1, R2},
    (BOB, ACME): {T1, R2},
    (CAROL, ACME): {T1, R1},
    (DAVE, GLOBEX): {G1},
    (EVE, ACME): set(),
}


def vector_literal(values: list[float]) -> str:
    """pgvector's text format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


def random_unit_vector(rng: random.Random) -> list[float]:
    raw = [rng.gauss(0.0, 1.0) for _ in range(EMBEDDING_DIM)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def load(conn: psycopg.Connection) -> None:
    """Wipe all tables and insert the dataset. `conn` must be app_migrator."""
    rng = random.Random(42)  # deterministic embeddings
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(ALL_TABLES)} CASCADE")
        cur.executemany(
            "INSERT INTO tenants (id, name) VALUES (%s, %s)",
            [(ACME, "Acme"), (GLOBEX, "Globex")],
        )
        cur.executemany(
            "INSERT INTO users (id, email, display_name) VALUES (%s, %s, %s)",
            [
                (ALICE, EMAILS[ALICE], "Alice"),
                (BOB, EMAILS[BOB], "Bob"),
                (CAROL, EMAILS[CAROL], "Carol"),
                (DAVE, EMAILS[DAVE], "Dave"),
                (EVE, EMAILS[EVE], "Eve"),
            ],
        )
        cur.executemany(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, %s, %s)",
            [
                (ACME, ALICE, "admin"),
                (ACME, BOB, "member"),
                (ACME, CAROL, "member"),
                (GLOBEX, DAVE, "member"),
            ],
        )
        cur.executemany(
            "INSERT INTO documents (id, tenant_id, title, visibility, created_by)"
            " VALUES (%s, %s, %s, %s, %s)",
            [
                (T1, ACME, TITLES[T1], "tenant", BOB),
                (R1, ACME, TITLES[R1], "restricted", ALICE),
                (R2, ACME, TITLES[R2], "restricted", BOB),
                (G1, GLOBEX, TITLES[G1], "tenant", DAVE),
            ],
        )
        cur.execute(
            "INSERT INTO document_grants (document_id, tenant_id, user_id) VALUES (%s, %s, %s)",
            (R1, ACME, CAROL),
        )
        cur.executemany(
            "INSERT INTO chunks (document_id, tenant_id, chunk_index, content, embedding)"
            " VALUES (%s, %s, %s, %s, %s::vector)",
            [
                (doc, tenant, i, f"chunk {i} of {doc}", vector_literal(random_unit_vector(rng)))
                for doc, tenant in DOC_TENANT.items()
                for i in range(CHUNKS_PER_DOC)
            ],
        )
