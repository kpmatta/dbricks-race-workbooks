-- RACE production metadata. Every table below is a Unity Catalog managed Delta table.
-- Run once with schema/table creation privileges. Delta is the only metadata store.
-- Delta timestamps are UTC.
-- IDs and relationships are validated by the application.
CREATE SCHEMA IF NOT EXISTS {{namespace}};

CREATE TABLE IF NOT EXISTS {{namespace}}.folders (
    id STRING NOT NULL,
    name STRING NOT NULL,
    owner_id STRING NOT NULL,
    parent_id STRING,
    config_json STRING NOT NULL,
    version BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    deleted_at TIMESTAMP,
    cloned_from_id STRING,
    status STRING NOT NULL,
    operation_key STRING,
    write_token STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {{namespace}}.workbooks (
    id STRING NOT NULL,
    name STRING NOT NULL,
    owner_id STRING NOT NULL,
    parent_id STRING,
    config_json STRING NOT NULL,
    version BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    deleted_at TIMESTAMP,
    cloned_from_id STRING,
    status STRING NOT NULL,
    operation_key STRING,
    write_token STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {{namespace}}.sheets (
    id STRING NOT NULL,
    name STRING NOT NULL,
    owner_id STRING NOT NULL,
    parent_id STRING,
    config_json STRING NOT NULL,
    version BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    deleted_at TIMESTAMP,
    cloned_from_id STRING,
    status STRING NOT NULL,
    operation_key STRING,
    write_token STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {{namespace}}.charts (
    id STRING NOT NULL,
    name STRING NOT NULL,
    owner_id STRING NOT NULL,
    parent_id STRING,
    config_json STRING NOT NULL,
    version BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    deleted_at TIMESTAMP,
    cloned_from_id STRING,
    status STRING NOT NULL,
    operation_key STRING,
    write_token STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {{namespace}}.data_sources (
    id STRING NOT NULL,
    name STRING NOT NULL,
    owner_id STRING NOT NULL,
    parent_id STRING,
    config_json STRING NOT NULL,
    version BIGINT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    deleted_at TIMESTAMP,
    cloned_from_id STRING,
    status STRING NOT NULL,
    operation_key STRING,
    write_token STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {{namespace}}.resource_permissions (
    id STRING NOT NULL, resource_kind STRING NOT NULL, resource_id STRING NOT NULL,
    principal_id STRING NOT NULL, role STRING NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {{namespace}}.audit_events (
    id STRING NOT NULL, actor_id STRING NOT NULL, action STRING NOT NULL,
    resource_kind STRING NOT NULL, resource_id STRING NOT NULL, created_at TIMESTAMP NOT NULL,
    detail_json STRING NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS {{namespace}}.schema_versions (
    version BIGINT NOT NULL, applied_at TIMESTAMP NOT NULL
) USING DELTA;
