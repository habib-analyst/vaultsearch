-- vaultsearch schema — NAIVE configuration (the honest contrast).
-- Identical tables, indexes and grants, but NO row-level security.
-- Isolation here depends entirely on application code remembering to add
-- WHERE tenant_id = ... to every query it ever writes. The database itself
-- will happily hand any tenant's rows to the app role.
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
CREATE INDEX IF NOT EXISTS documents_embedding_hnsw
    ON documents USING hnsw (embedding vector_cosine_ops);

GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO vault_app;
GRANT SELECT ON tenants TO vault_app;
