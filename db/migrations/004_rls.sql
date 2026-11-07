-- 004: row-level security.
--
-- Request identity arrives as two transaction-local settings, written by
-- app/db.py:tenant_transaction() with set_config(..., true):
--     app.user_id    app.tenant_id
-- If either is missing, the helpers return NULL, every comparison is NULL,
-- and every policy returns zero rows (fail closed).
--
-- Policies are written TO app_user. app_migrator has BYPASSRLS, and no other
-- role has table privileges, so no other role can reach these rows.

-------------------------------------------------------------------------------
-- 1. Request-context readers (SECURITY INVOKER: they only read a setting).
-------------------------------------------------------------------------------

-- nullif(..., ''): once a custom setting has been used on a connection,
-- current_setting(name, true) returns '' (not NULL) after the transaction
-- that set it ends. ''::uuid would raise; NULL cleanly denies.
CREATE FUNCTION current_user_id() RETURNS uuid
    LANGUAGE sql STABLE
    SET search_path = pg_catalog, public
AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;

CREATE FUNCTION current_tenant_id() RETURNS uuid
    LANGUAGE sql STABLE
    SET search_path = pg_catalog, public
AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;

REVOKE ALL ON FUNCTION current_user_id(), current_tenant_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION current_user_id(), current_tenant_id() TO app_user, app_rls_helper;

-------------------------------------------------------------------------------
-- 2. Lookup helpers (SECURITY DEFINER, owned by app_rls_helper).
--
-- Why SECURITY DEFINER: the documents policy needs to look at memberships and
-- document_grants. Querying those tables directly from a policy would apply
-- *their* RLS too, and a grants policy that looks back at documents would
-- recurse. These functions run as app_rls_helper (BYPASSRLS, SELECT on just
-- these two tables), and only ever answer about the *current* user.
--
-- Fixed search_path + schema-qualified names stop a caller from shadowing
-- `memberships` with their own object and hijacking the check.
-------------------------------------------------------------------------------

CREATE FUNCTION is_member(p_tenant_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.tenant_id = p_tenant_id
          AND m.user_id = public.current_user_id()
    )
$$;

CREATE FUNCTION is_tenant_admin(p_tenant_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.memberships m
        WHERE m.tenant_id = p_tenant_id
          AND m.user_id = public.current_user_id()
          AND m.role = 'admin'
    )
$$;

CREATE FUNCTION has_grant(p_document_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.document_grants g
        WHERE g.document_id = p_document_id
          AND g.user_id = public.current_user_id()
    )
$$;

-- Set the ACL while app_migrator still owns the functions; ownership transfer
-- carries the ACL across.
REVOKE ALL ON FUNCTION is_member(uuid), is_tenant_admin(uuid), has_grant(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION is_member(uuid), is_tenant_admin(uuid), has_grant(uuid) TO app_user;

GRANT SELECT ON memberships, document_grants TO app_rls_helper;

-- Postgres requires the new owner to have CREATE on the schema. Grant it only
-- for the duration of the transfer.
GRANT CREATE ON SCHEMA public TO app_rls_helper;
ALTER FUNCTION is_member(uuid) OWNER TO app_rls_helper;
ALTER FUNCTION is_tenant_admin(uuid) OWNER TO app_rls_helper;
ALTER FUNCTION has_grant(uuid) OWNER TO app_rls_helper;
REVOKE CREATE ON SCHEMA public FROM app_rls_helper;

-------------------------------------------------------------------------------
-- 3. Enable + FORCE on every table. FORCE makes the table owner subject to
--    policies too, so only an explicit BYPASSRLS role can skip them.
-------------------------------------------------------------------------------
ALTER TABLE tenants         ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants         FORCE  ROW LEVEL SECURITY;
ALTER TABLE users           ENABLE ROW LEVEL SECURITY;
ALTER TABLE users           FORCE  ROW LEVEL SECURITY;
ALTER TABLE memberships     ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships     FORCE  ROW LEVEL SECURITY;
ALTER TABLE documents       ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents       FORCE  ROW LEVEL SECURITY;
ALTER TABLE document_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_grants FORCE  ROW LEVEL SECURITY;
ALTER TABLE chunks          ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks          FORCE  ROW LEVEL SECURITY;

-------------------------------------------------------------------------------
-- 4. Policies.
--
-- (SELECT current_tenant_id()) is wrapped in a sub-select so the planner
-- evaluates it once per query (InitPlan) instead of once per row.
-------------------------------------------------------------------------------

-- tenants: only the tenant you are acting in, and only if you belong to it.
CREATE POLICY tenants_select ON tenants
    FOR SELECT TO app_user
    USING (id = (SELECT current_tenant_id()) AND is_member(id));

-- users: only yourself.
CREATE POLICY users_select ON users
    FOR SELECT TO app_user
    USING (id = (SELECT current_user_id()));

-- memberships: only your own membership in the current tenant.
CREATE POLICY memberships_select ON memberships
    FOR SELECT TO app_user
    USING (tenant_id = (SELECT current_tenant_id())
           AND user_id = (SELECT current_user_id()));

-- documents: THE read rule. Everything else (chunks) defers to this one.
-- is_member() matters even though the JWT names the tenant: it means a token
-- claiming a tenant the user does not belong to still sees nothing.
CREATE POLICY documents_select ON documents
    FOR SELECT TO app_user
    USING (
        tenant_id = (SELECT current_tenant_id())
        AND is_member(tenant_id)
        AND (
            visibility = 'tenant'
            OR created_by = (SELECT current_user_id())
            OR has_grant(id)
            OR is_tenant_admin(tenant_id)
        )
    );

-- Any member may create a document in their tenant, but only as themselves.
CREATE POLICY documents_insert ON documents
    FOR INSERT TO app_user
    WITH CHECK (
        tenant_id = (SELECT current_tenant_id())
        AND is_member(tenant_id)
        AND created_by = (SELECT current_user_id())
    );

-- Only the creator or a tenant admin may delete. Chunks and grants cascade.
CREATE POLICY documents_delete ON documents
    FOR DELETE TO app_user
    USING (
        tenant_id = (SELECT current_tenant_id())
        AND is_member(tenant_id)
        AND (created_by = (SELECT current_user_id()) OR is_tenant_admin(tenant_id))
    );

-- document_grants: you see grants made to you, and grants on documents you
-- manage (admin, or creator of the document). Managing = insert/delete.
-- The EXISTS sub-query on documents runs under documents' own RLS.
CREATE POLICY document_grants_select ON document_grants
    FOR SELECT TO app_user
    USING (
        tenant_id = (SELECT current_tenant_id())
        AND is_member(tenant_id)
        AND (
            user_id = (SELECT current_user_id())
            OR is_tenant_admin(tenant_id)
            OR EXISTS (SELECT 1 FROM documents d
                       WHERE d.id = document_grants.document_id
                         AND d.created_by = (SELECT current_user_id()))
        )
    );

CREATE POLICY document_grants_insert ON document_grants
    FOR INSERT TO app_user
    WITH CHECK (
        tenant_id = (SELECT current_tenant_id())
        AND is_member(tenant_id)
        AND (
            is_tenant_admin(tenant_id)
            OR EXISTS (SELECT 1 FROM documents d
                       WHERE d.id = document_grants.document_id
                         AND d.created_by = (SELECT current_user_id()))
        )
    );

CREATE POLICY document_grants_delete ON document_grants
    FOR DELETE TO app_user
    USING (
        tenant_id = (SELECT current_tenant_id())
        AND is_member(tenant_id)
        AND (
            is_tenant_admin(tenant_id)
            OR EXISTS (SELECT 1 FROM documents d
                       WHERE d.id = document_grants.document_id
                         AND d.created_by = (SELECT current_user_id()))
        )
    );

-- chunks: readable iff the parent document is readable. The EXISTS runs as
-- app_user, so documents_select is applied inside it: one source of truth.
CREATE POLICY chunks_select ON chunks
    FOR SELECT TO app_user
    USING (
        tenant_id = (SELECT current_tenant_id())
        AND EXISTS (SELECT 1 FROM documents d
                    WHERE d.id = chunks.document_id
                      AND d.tenant_id = chunks.tenant_id)
    );

-- Writing chunks is stricter than reading: creator or admin only. A user who
-- can read a document through a grant cannot add content to it.
CREATE POLICY chunks_insert ON chunks
    FOR INSERT TO app_user
    WITH CHECK (
        tenant_id = (SELECT current_tenant_id())
        AND EXISTS (SELECT 1 FROM documents d
                    WHERE d.id = chunks.document_id
                      AND d.tenant_id = chunks.tenant_id
                      AND (d.created_by = (SELECT current_user_id())
                           OR is_tenant_admin(d.tenant_id)))
    );

-------------------------------------------------------------------------------
-- 5. Table privileges for app_user. Minimal on purpose: no UPDATE anywhere
--    (content is immutable; re-ingest = delete + insert), no TRUNCATE.
-------------------------------------------------------------------------------
GRANT SELECT                 ON tenants, users, memberships TO app_user;
GRANT SELECT, INSERT, DELETE ON documents, document_grants  TO app_user;
GRANT SELECT, INSERT         ON chunks                      TO app_user;
