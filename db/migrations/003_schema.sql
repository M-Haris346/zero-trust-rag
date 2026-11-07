-- 003: tables.
--
-- tenant_id is copied onto every child table and tied to the parent with a
-- composite foreign key, so a child row can never claim a different tenant
-- than its parent. RLS policies can then check tenant_id on the row itself.

CREATE TABLE tenants (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email        citext NOT NULL UNIQUE,
    display_name text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE memberships (
    tenant_id uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    user_id   uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role      text NOT NULL CHECK (role IN ('admin', 'member')),
    PRIMARY KEY (tenant_id, user_id)
);
CREATE INDEX memberships_user_id_idx ON memberships (user_id);

CREATE TABLE documents (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    title      text NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
    visibility text NOT NULL CHECK (visibility IN ('tenant', 'restricted')),
    created_by uuid NOT NULL REFERENCES users (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    -- Target for the composite FKs below. Redundant with the PK on its own,
    -- but it is what lets children reference (id, tenant_id) together.
    UNIQUE (id, tenant_id)
);
CREATE INDEX documents_tenant_id_idx ON documents (tenant_id);

CREATE TABLE document_grants (
    document_id uuid NOT NULL,
    tenant_id   uuid NOT NULL,
    user_id     uuid NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, user_id),
    FOREIGN KEY (document_id, tenant_id)
        REFERENCES documents (id, tenant_id) ON DELETE CASCADE,
    -- A grant can only name a member of the same tenant, and removing the
    -- membership removes the grant. Enforced by the schema, not by app code.
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES memberships (tenant_id, user_id) ON DELETE CASCADE
);
CREATE INDEX document_grants_tenant_user_idx ON document_grants (tenant_id, user_id);

CREATE TABLE chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL,
    tenant_id   uuid NOT NULL,
    chunk_index int NOT NULL CHECK (chunk_index >= 0),
    content     text NOT NULL,
    embedding   vector(384) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index),
    FOREIGN KEY (document_id, tenant_id)
        REFERENCES documents (id, tenant_id) ON DELETE CASCADE
);
CREATE INDEX chunks_tenant_document_idx ON chunks (tenant_id, document_id);
-- Approximate nearest-neighbour index. RLS filters rows *after* the index
-- returns candidates; see ADR 0004 for why that matters.
CREATE INDEX chunks_embedding_hnsw_idx ON chunks USING hnsw (embedding vector_cosine_ops);
