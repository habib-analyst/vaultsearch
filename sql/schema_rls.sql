-- vaultsearch schema — RLS configuration.
-- Isolation is enforced by the database: FORCE ROW LEVEL SECURITY with a
-- per-transaction tenant context set via set_config('app.tenant_id', ...).
-- The application role (vault_app) is neither owner nor superuser and has
-- no BYPASSRLS, so there is no query the app can write that escapes the policy.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   text PRIMARY KEY,
    display_name text NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id     text NOT NULL,
    tenant_id  text NOT NULL REFERENCES tenants(tenant_id),
    topic      text NOT NULL,
    cluster_   text NOT NULL,
    title      text NOT NULL,
    body       text NOT NULL,
    content    text GENERATED ALWAYS AS (title || ' ' || body) STORED,
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED,
    embedding  vector(384) NOT NULL,
    PRIMARY KEY (tenant_id, doc_id)
);

CREATE INDEX IF NOT EXISTS documents_tsv_gin ON documents USING gin (tsv);
-- Shared HNSW index across all tenants: the leakage suite explicitly probes
-- whether approximate-nearest-neighbour candidate generation bleeds rows
-- across the tenant boundary. (RLS is applied to heap rows after index
-- candidate generation; a correct setup returns fewer rows, never wrong ones.)
CREATE INDEX IF NOT EXISTS documents_embedding_hnsw
    ON documents USING hnsw (embedding vector_cosine_ops);

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON documents;
CREATE POLICY tenant_isolation ON documents
    USING      (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO vault_app;
GRANT SELECT ON tenants TO vault_app;
