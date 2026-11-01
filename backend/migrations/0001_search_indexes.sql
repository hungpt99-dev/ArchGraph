-- Full-text and trigram indexes for symbol/document search.
-- Applied once by app.migrate (tracked in schema_migrations).

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS ix_nodes_name_trgm
    ON nodes USING gin (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_nodes_qualified_trgm
    ON nodes USING gin (qualified_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_documents_title_trgm
    ON documents USING gin (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_documents_content_fts
    ON documents USING gin (to_tsvector('english', coalesce(content, '')));
