# ADR 0004: HNSW index scans and RLS

**Status:** accepted (the retrieval code that uses it lands in the retrieval phase)

## Context
`chunks` has an HNSW index (`chunks_embedding_hnsw_idx`). For
`ORDER BY embedding <=> $q LIMIT k`, pgvector walks the graph and returns
about `hnsw.ef_search` (default 40) nearest candidates. RLS policies are then
applied to those candidates as a filter.

If most of the nearest candidates belong to documents the user cannot read,
the filter removes them and the user gets **fewer than k rows, possibly zero**,
even though readable chunks exist further away. No data leaks, but retrieval
quietly gets worse for exactly the users with the least access.

## Decision
Retrieval queries run with, inside the request transaction:

```sql
SET LOCAL hnsw.iterative_scan = relaxed_order;
```

pgvector 0.8+ then keeps scanning the index until `k` rows pass the filter (or
`hnsw.max_scan_tuples` is reached). `relaxed_order` can return rows slightly out
of distance order, so the retrieval query re-sorts the results by distance, for
example in an outer query over a materialised CTE.

`SET LOCAL` is used for the same reason as ADR 0003: it must not stick to a
pooled connection.

## Evidence
`tests/db/test_hnsw_rls.py` builds a skewed corpus: 2,000 chunks right next to
the query in a document Bob cannot read, and 20 readable chunks further away.

- With `iterative_scan = off`: Bob gets fewer than `k` rows (the problem).
- With `iterative_scan = relaxed_order`: Bob gets exactly `k`, all readable.

Both tests assert the plan really uses the HNSW index.

## Consequences
- Requires pgvector >= 0.8; `001_extensions.sql` fails otherwise.
- Users with very sparse access may make the index scan more tuples, which is
  slower. `hnsw.max_scan_tuples` caps that cost.
- Alternative not chosen for now: per-tenant partial indexes or partitioning.
  They fix the cross-tenant case but not restricted documents inside a tenant.
