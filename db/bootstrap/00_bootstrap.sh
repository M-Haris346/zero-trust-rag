#!/usr/bin/env bash
# One-time cluster bootstrap. Must run as a Postgres SUPERUSER because it
# creates roles (one with BYPASSRLS), databases, and the pgvector extension.
#
# Runs automatically via /docker-entrypoint-initdb.d on the first start of the
# compose container. In CI it is invoked directly with PGHOST/PGPASSWORD set.
#
# Everything here is idempotent. Passwords come from the environment so no
# secret is ever written into a SQL file.
set -euo pipefail

: "${APP_MIGRATOR_PASSWORD:?APP_MIGRATOR_PASSWORD must be set}"
: "${APP_USER_PASSWORD:?APP_USER_PASSWORD must be set}"

PSQL=(psql -v ON_ERROR_STOP=1 --no-psqlrc --username "${POSTGRES_USER:-postgres}")

"${PSQL[@]}" --dbname postgres \
  -v migrator_pw="$APP_MIGRATOR_PASSWORD" \
  -v user_pw="$APP_USER_PASSWORD" <<'SQL'
-- app_migrator: owns the schema, runs migrations, seed and test fixtures.
-- BYPASSRLS because FORCE RLS would otherwise apply to it as table owner and
-- block seeding. The API never connects as this role.
SELECT format(
  'CREATE ROLE app_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS PASSWORD %L',
  :'migrator_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_migrator') \gexec

-- app_rls_helper: cannot log in. Owns the SECURITY DEFINER helper functions
-- used inside policies, and is granted SELECT on exactly two tables.
SELECT 'CREATE ROLE app_rls_helper NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rls_helper') \gexec

-- app_user: the API's runtime role. Subject to every RLS policy.
SELECT format(
  'CREATE ROLE app_user LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
  :'user_pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') \gexec

-- Lets app_migrator run `ALTER FUNCTION ... OWNER TO app_rls_helper` in
-- migrations, without inheriting the helper's privileges.
GRANT app_rls_helper TO app_migrator WITH INHERIT FALSE, SET TRUE;

SELECT 'CREATE DATABASE ztrag OWNER app_migrator'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ztrag') \gexec
SELECT 'CREATE DATABASE ztrag_test OWNER app_migrator'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ztrag_test') \gexec
SQL

for db in ztrag ztrag_test; do
  "${PSQL[@]}" --dbname "$db" <<SQL
REVOKE CONNECT, TEMPORARY ON DATABASE $db FROM PUBLIC;
GRANT CONNECT ON DATABASE $db TO app_migrator, app_user;
-- pgvector is not a trusted extension, so it must be created by a superuser.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;
SQL
done

echo "bootstrap complete: roles app_migrator, app_rls_helper, app_user; databases ztrag, ztrag_test"
