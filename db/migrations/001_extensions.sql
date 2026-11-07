-- 001: extensions this schema depends on.
--
-- pgvector is not a "trusted" extension, so app_migrator cannot create it.
-- db/bootstrap/00_bootstrap.sh creates both as superuser; here IF NOT EXISTS
-- is a no-op that documents the dependency and fails loudly if it is missing.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;

-- hnsw.iterative_scan (needed for HNSW + RLS, see ADR 0004) arrived in 0.8.0.
DO $$
DECLARE
    v text;
BEGIN
    SELECT extversion INTO v FROM pg_extension WHERE extname = 'vector';
    IF string_to_array(v, '.')::int[] < ARRAY[0, 8] THEN
        RAISE EXCEPTION 'pgvector >= 0.8.0 required, found %', v;
    END IF;
END
$$;
