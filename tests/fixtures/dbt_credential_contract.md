# dbt credential contract snapshot

<!-- Generated from the installed dbt credential dataclasses; do not edit manually. -->

This snapshot is the review boundary for dbt-adapter upgrades. dbt charts' strict Pydantic source models remain the authoring contract.

## bigquery

dbt-bigquery 1.11.1

### `dbt.adapters.bigquery.credentials.BigQueryCredentials`

- `database`
- `schema`
- `method`
- `execution_project`
- `quota_project`
- `location`
- `api_endpoint`
- `priority`
- `maximum_bytes_billed`
- `impersonate_service_account`
- `job_retry_deadline_seconds`
- `job_retries`
- `job_creation_timeout_seconds`
- `job_execution_timeout_seconds`
- `keyfile`
- `keyfile_json`
- `token`
- `refresh_token`
- `client_id`
- `client_secret`
- `token_uri`
- `workload_pool_provider_path`
- `service_account_impersonation_url`
- `token_endpoint`
- `compute_region`
- `dataproc_cluster_name`
- `gcs_bucket`
- `submission_method`
- `dataproc_batch`
- `scopes`

Aliases:
- `project` → `database`
- `dataset` → `schema`
- `target_project` → `target_database`
- `target_dataset` → `target_schema`
- `retries` → `job_retries`
- `timeout_seconds` → `job_execution_timeout_seconds`
- `dataproc_region` → `compute_region`

## duckdb

dbt-duckdb 1.10.0

### `dbt.adapters.duckdb.credentials.DuckDBCredentials`

- `database`
- `schema`
- `path`
- `config_options`
- `extensions`
- `settings`
- `secrets`
- `external_root`
- `use_credential_provider`
- `attach`
- `filesystems`
- `remote`
- `plugins`
- `disable_transactions`
- `keep_open`
- `module_paths`
- `retries`
- `is_ducklake`

## postgres

dbt-postgres 1.10.0

### `dbt.adapters.postgres.connections.PostgresCredentials`

- `database`
- `schema`
- `host`
- `user`
- `port`
- `password`
- `connect_timeout`
- `role`
- `search_path`
- `keepalives_idle`
- `sslmode`
- `sslcert`
- `sslkey`
- `sslrootcert`
- `application_name`
- `retries`

Aliases:
- `dbname` → `database`
- `pass` → `password`

## redshift

dbt-redshift 1.11.1

### `dbt.adapters.redshift.connections.RedshiftCredentials`

- `database`
- `schema`
- `host`
- `port`
- `method`
- `user`
- `password`
- `cluster_id`
- `iam_profile`
- `autocreate`
- `db_groups`
- `ra3_node`
- `datasharing`
- `connect_timeout`
- `role`
- `sslmode`
- `retries`
- `retry_all`
- `region`
- `autocommit`
- `access_key_id`
- `secret_access_key`
- `idc_region`
- `issuer_url`
- `idp_listen_port`
- `idc_client_display_name`
- `idp_response_timeout`
- `token_endpoint`
- `is_serverless`
- `serverless_work_group`
- `serverless_acct_id`
- `tcp_keepalive`
- `tcp_keepalive_idle`
- `tcp_keepalive_interval`
- `tcp_keepalive_count`
- `query_group`
- `allow_concurrent_drops`
- `drop_without_cascade`

Aliases:
- `dbname` → `database`
- `pass` → `password`

## snowflake

dbt-snowflake 1.12.4

### `dbt.adapters.snowflake.connections.SnowflakeCredentials`

- `database`
- `schema`
- `account`
- `user`
- `warehouse`
- `role`
- `password`
- `authenticator`
- `private_key`
- `private_key_path`
- `private_key_passphrase`
- `token`
- `oauth_client_id`
- `oauth_client_secret`
- `query_tag`
- `client_session_keep_alive`
- `host`
- `port`
- `proxy_host`
- `proxy_port`
- `protocol`
- `connect_retries`
- `connect_timeout`
- `retry_on_database_errors`
- `retry_all`
- `insecure_mode`
- `reuse_connections`
- `s3_stage_vpce_dns_name`
- `workload_identity_provider`
- `workload_identity_entra_resource`
- `platform_detection_timeout_seconds`
