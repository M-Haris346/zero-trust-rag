-- 002: schema-level privileges and role sanity checks.
--
-- Roles themselves are created by db/bootstrap/00_bootstrap.sh (creating a
-- BYPASSRLS role needs superuser, and passwords must not live in SQL files).
-- This migration refuses to continue if the roles are misconfigured, so a
-- dangerous setup fails at deploy time instead of silently weakening RLS.
DO $$
BEGIN
    IF (SELECT count(*) FROM pg_roles
        WHERE rolname IN ('app_migrator', 'app_rls_helper', 'app_user')) <> 3 THEN
        RAISE EXCEPTION 'roles missing: run db/bootstrap/00_bootstrap.sh as superuser first';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'app_user' AND (rolsuper OR rolbypassrls)) THEN
        RAISE EXCEPTION 'app_user must be NOSUPERUSER NOBYPASSRLS';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'app_rls_helper' AND (rolsuper OR rolcanlogin)) THEN
        RAISE EXCEPTION 'app_rls_helper must be NOSUPERUSER NOLOGIN';
    END IF;
END
$$;

-- Nobody gets anything in this schema by default; access is granted explicitly.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_user, app_rls_helper;
