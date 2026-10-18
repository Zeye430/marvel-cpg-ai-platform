CREATE SCHEMA meta;
CREATE SCHEMA bronze;
CREATE SCHEMA silver;
CREATE SCHEMA gold;

CREATE TABLE meta.ingestion_runs (
    ingestion_run_id VARCHAR PRIMARY KEY, dataset_hash VARCHAR, data_version VARCHAR,
    source_location VARCHAR, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
    status VARCHAR, error VARCHAR
);
CREATE TABLE meta.source_files (
    ingestion_run_id VARCHAR, filename VARCHAR, source_file_hash VARCHAR,
    bytes BIGINT, parsed_rows BIGINT, header_json VARCHAR, contract_version VARCHAR,
    PRIMARY KEY (ingestion_run_id, filename)
);
CREATE TABLE meta.build_runs (
    build_id VARCHAR PRIMARY KEY, dataset_hash VARCHAR, config_hash VARCHAR,
    code_revision VARCHAR, code_tree_hash VARCHAR, configuration_json VARCHAR,
    started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, status VARCHAR, error VARCHAR
);
CREATE TABLE meta.quality_results (
    build_id VARCHAR, check_id VARCHAR, scope_id VARCHAR, severity VARCHAR,
    passed BOOLEAN, failed_count BIGINT, checked_count BIGINT, tolerance DOUBLE,
    evidence_json VARCHAR, note VARCHAR,
    PRIMARY KEY (build_id, check_id, scope_id)
);
CREATE TABLE meta.publications (
    publication_id VARCHAR PRIMARY KEY, build_id VARCHAR, layer VARCHAR,
    quality_status VARCHAR, previous_publication_id VARCHAR, created_at TIMESTAMPTZ
);
CREATE TABLE meta.policy_registry (
    policy_id VARCHAR, policy_version VARCHAR, approval_status VARCHAR,
    effective_from DATE, effective_to DATE, provenance VARCHAR, assumption VARCHAR,
    PRIMARY KEY (policy_id, policy_version)
);
