-- Shared PostgreSQL serving metadata and access objects (shared_postgres_serving.v1).
-- Applied only by `python -m data_platform.postgres_cli init --apply` inside one
-- transaction, after a collision check. Creates only these platform namespaces;
-- never touches public.* or other schemas, default privileges or search_path.

CREATE SCHEMA platform_meta;
CREATE SCHEMA platform_access;
CREATE SCHEMA platform_serving;

CREATE TABLE platform_meta.schema_version (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    interface_version TEXT NOT NULL,
    installed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per release candidate; payload lives in platform_release_<release_id>.
-- payload_schema_oid is written in the same transaction that creates the schema:
-- a schema counts as this release's payload only while its OID still matches.
CREATE TABLE platform_meta.releases (
    release_id TEXT PRIMARY KEY CHECK (release_id ~ '^r[0-9a-f]{24}$'),
    content_key TEXT NOT NULL UNIQUE CHECK (content_key ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('Loading', 'Imported', 'Validated', 'Failed')),
    source_artifact_id TEXT NOT NULL,
    source_database_sha256 TEXT NOT NULL,
    source_run_sha256 TEXT NOT NULL,
    source_quality_sha256 TEXT NOT NULL,
    contract_sha256 TEXT NOT NULL,
    payload_schema_oid OID,
    manifest JSONB NOT NULL,
    views JSONB NOT NULL,
    row_counts JSONB,
    content_digests JSONB,
    checks JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    imported_at TIMESTAMPTZ,
    validated_at TIMESTAMPTZ,
    last_verified_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ
);

-- Statement trigger installed on every imported payload table: release payloads
-- are immutable after import (an owner can still bypass it; verification detects that).
CREATE FUNCTION platform_meta.reject_release_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Shared serving release payloads are immutable (%.%)', TG_TABLE_SCHEMA, TG_TABLE_NAME;
END
$$;
REVOKE ALL ON FUNCTION platform_meta.reject_release_write() FROM PUBLIC;

-- Append-only publication history; rows are never updated or deleted.
CREATE TABLE platform_meta.publications (
    publication_id TEXT PRIMARY KEY CHECK (publication_id ~ '^[0-9a-f]{32}$'),
    release_id TEXT NOT NULL REFERENCES platform_meta.releases (release_id),
    action TEXT NOT NULL,
    previous_publication_id TEXT REFERENCES platform_meta.publications (publication_id),
    published_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE platform_meta.current_publication (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    publication_id TEXT NOT NULL REFERENCES platform_meta.publications (publication_id),
    switched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reader-visible metadata: published releases only (candidates stay invisible).
CREATE VIEW platform_meta.published_releases AS
SELECT r.release_id, r.source_artifact_id, r.manifest, r.views, r.row_counts, r.content_digests, r.validated_at
FROM platform_meta.releases r
WHERE r.state = 'Validated'
  AND EXISTS (SELECT 1 FROM platform_meta.publications p WHERE p.release_id = r.release_id);

CREATE VIEW platform_meta.current_release AS
SELECT c.publication_id, p.release_id, p.published_at, c.switched_at
FROM platform_meta.current_publication c
JOIN platform_meta.publications p USING (publication_id);

-- Store scopes are configured separately from data releases and never copied from them.
CREATE TABLE platform_access.account_scope (
    principal_id TEXT COLLATE "C" NOT NULL CHECK (principal_id <> ''),
    store_id TEXT COLLATE "C" NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('sales', 'manager')),
    scope_version TEXT NOT NULL,
    PRIMARY KEY (principal_id, store_id)
);

CREATE TABLE platform_access.scope_policy (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    policy_version TEXT NOT NULL,
    scope_fingerprint TEXT NOT NULL,
    assignment_count BIGINT NOT NULL,
    configured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
