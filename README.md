# RACE

RACE is a Python-first Databricks App for organizing governed data into Excel-style workbooks, sheets, and charts. It supports private and shared folders, viewer/editor roles, inherited permissions, independent workbook cloning, calculated columns, server-side filters and sorting, and Plotly charts over the complete filtered dataset.

## Metadata storage

Production metadata is stored exclusively in Unity Catalog managed Delta tables in `sandbox.race`:

| Delta table | Purpose |
| --- | --- |
| `folders` | Personal/shared hierarchy and ownership |
| `workbooks` | Workbook containers and clone provenance |
| `sheets` | Dataset references and saved sheet configuration |
| `charts` | Chart definitions and sheet references |
| `data_sources` | Registered Unity Catalog tables/views |
| `resource_permissions` | Direct viewer/editor grants |
| `audit_events` | Metadata activity trail |
| `schema_versions` | Applied migration versions |

The idempotent migration is [migrations/001_metadata.sql](migrations/001_metadata.sql). Every `CREATE TABLE` explicitly uses `USING DELTA`. All runtime modes use these Delta tables through a Databricks SQL warehouse. Source datasets remain in their original Unity Catalog tables and are never copied into metadata.

The browser libraries are pinned and checked into `race/static/vendor`, so runtime does not depend on a CDN. To refresh those exact assets and their license files, run `uv run python scripts/vendor_assets.py`.

## Run locally against Databricks

Authenticate with a Databricks CLI OAuth profile:

```bash
databricks auth login --host https://your-workspace-host --profile race-local
```

Install dependencies, then pass the OAuth profile and warehouse path to each local process. A deployed app receives the equivalent values from `app.yaml` and Databricks-managed resources:

```bash
uv sync --python 3.12

RACE_MODE=connected \
DATABRICKS_HOST=https://your-workspace-host \
DATABRICKS_CONFIG_PROFILE=race-local \
DATABRICKS_SQL_HTTP_PATH=/sql/1.0/warehouses/your-warehouse-id \
uv run python -m race.bootstrap

RACE_MODE=connected \
DATABRICKS_HOST=https://your-workspace-host \
DATABRICKS_CONFIG_PROFILE=race-local \
DATABRICKS_SQL_HTTP_PATH=/sql/1.0/warehouses/your-warehouse-id \
uv run python app.py
```

Open `http://127.0.0.1:8000`; API documentation is at `/docs`. Connected mode verifies the current identity through the OAuth profile. Dataset queries use that identity, so Unity Catalog grants, row filters, and column masks remain effective.

## Deploy as a Databricks App

The examples below use `sandbox.race`; substitute a catalog you can access if your workspace uses a different name. The catalog must already exist. `race.bootstrap` creates the schema and Delta tables, but it cannot create a missing catalog. Check first:

```bash
databricks catalogs list --profile race-local
```

If `sandbox` is absent, ask a catalog owner to create it or change `RACE_METADATA_CATALOG` in [app.yaml](app.yaml) to a catalog where you can create schemas. The app's warehouse ID is configured directly in `app.yaml`; replace that workspace-specific ID when deploying to another workspace.

### 1. Prepare and initialize metadata

Authenticate the CLI, install the app dependencies, and set the same metadata namespace used by `app.yaml`. Local bootstrap runs in `connected` mode and authenticates with your CLI profile; the deployed app runs in `databricks` mode and uses the Databricks Apps identity.

```bash
databricks auth login --host https://your-workspace-host --profile race-local
uv sync --python 3.12

export DATABRICKS_HOST=https://your-workspace-host
export DATABRICKS_CONFIG_PROFILE=race-local
export DATABRICKS_WAREHOUSE_ID=your-warehouse-id
export DATABRICKS_SQL_HTTP_PATH="/sql/1.0/warehouses/$DATABRICKS_WAREHOUSE_ID"
export RACE_MODE=connected
export RACE_METADATA_CATALOG=sandbox
export RACE_METADATA_SCHEMA=race

uv run python -m race.bootstrap
```

The identity used for bootstrap needs permission to create the `race` schema and metadata tables. For example, an administrator can grant the required Unity Catalog privileges on the chosen catalog. Bootstrap is idempotent, so it can be rerun after correcting a permission or configuration issue.

### 2. Create and configure the app

Create the app using a lowercase name with letters, numbers, or hyphens, then configure it in the Databricks Apps UI:

```bash
databricks apps create race-workbooks --profile race-local
```

In the app configuration:

- Enable user authorization and add the `sql` scope. Users must consent to the requested scope before querying source data.
- Grant the app service principal `CAN USE` on the warehouse whose ID is in `app.yaml`.
- Grant the app service principal `USE CATALOG` on the metadata catalog and `USE SCHEMA`, `SELECT`, and `MODIFY` on the metadata schema/tables. If the app will initialize metadata itself, also grant the required schema/table creation privileges; otherwise bootstrap once with an authorized user first.
- Grant `CAN USE` on the app to only the intended users or groups.

RACE uses the app service principal for shared metadata and the forwarded user token for source-table queries. Each user therefore needs their own Unity Catalog permissions on the source tables and warehouse; the app does not grant those permissions.

### 3. Upload and deploy

The CLI's `workspace import-dir` does not provide custom exclude patterns. Stage a runtime-only copy first: this keeps `app.py`, `app.yaml`, `requirements.txt`, and `race/`, while excluding local environments, tests, migration inputs already applied by bootstrap, and development files.

```bash
APP_SOURCE="/Workspace/Users/your-user/apps/race-workbooks"
APP_PACKAGE="$(mktemp -d)"
trap 'rm -rf "$APP_PACKAGE"' EXIT

rsync -a \
	--exclude='.git/' \
	--exclude='.databricks/' \
	--exclude='.venv/' \
	--exclude='venv/' \
	--exclude='__pycache__/' \
	--exclude='.pytest_cache/' \
	--exclude='.ruff_cache/' \
	--exclude='.mypy_cache/' \
	--exclude='.tox/' \
	--exclude='*.egg-info/' \
	--exclude='tests/' \
	--exclude='scripts/' \
	--exclude='migrations/' \
	--exclude='README.md' \
	--exclude='pyproject.toml' \
	--exclude='uv.lock' \
	--exclude='.python-version' \
	--exclude='.gitignore' \
	--exclude='.DS_Store' \
	./ "$APP_PACKAGE/"

# Creates the target and any missing parent folders; succeeds if it already exists.
databricks workspace mkdirs "$APP_SOURCE" --profile race-local
databricks workspace import-dir "$APP_PACKAGE" "$APP_SOURCE" --overwrite --profile race-local
databricks apps deploy race-workbooks \
	--source-code-path "$APP_SOURCE" \
	--profile race-local
```

`app.yaml` is read from the uploaded project root. Its command starts FastAPI through [app.py](app.py), which binds to `0.0.0.0` and the port assigned by Databricks Apps. Use `databricks apps deploy` for deployment; uploading files alone does not start the app.

### 4. Verify startup and readiness

Get the app URL and inspect its logs:

```bash
databricks apps get race-workbooks --profile race-local
databricks apps logs race-workbooks --profile race-local
```

Then check both health endpoints. `/health/live` verifies the web process; `/health/ready` verifies that metadata has been initialized and the app can connect to the warehouse.

```bash
APP_URL=https://your-app-url
curl -fsS "$APP_URL/health/live"
curl -i "$APP_URL/health/ready"
```

Expected responses are `{"status":"ok"}` for liveness and `{"status":"ready", ...}` for readiness. A `503` from readiness usually means bootstrap has not completed, the configured catalog/schema is wrong, or the app service principal lacks warehouse or metadata permissions.

Metadata operations use the app service principal. Source-data queries require `X-Forwarded-Access-Token` from the trusted Databricks Apps boundary and use the current user's identity. Sharing a workbook therefore never grants access to its referenced data.

## Databricks App configuration

Runtime configuration is declared in [app.yaml](app.yaml):

| `app.yaml` environment entry | Value | Meaning |
| --- | --- | --- |
| `RACE_MODE` | `databricks` | Enables the Databricks Apps identity boundary |
| `RACE_METADATA_CATALOG` | `sandbox` | Unity Catalog catalog for Delta metadata |
| `RACE_METADATA_SCHEMA` | `race` | Unity Catalog schema for Delta metadata |
| `RACE_QUERY_TIMEOUT_SECONDS` | `30` | SQL statement timeout |
| `RACE_MAX_PAGE_SIZE` | `500` | Maximum rows returned per grid page |
| `RACE_MAX_CHART_POINTS` | `1000` | Maximum chart groups |
| `DATABRICKS_WAREHOUSE_ID` | Workspace-specific warehouse ID | SQL warehouse used for metadata and source queries |

`DATABRICKS_HOST`, OAuth credentials, and the application identity are supplied by the Databricks
Apps runtime. They are not stored in source code or another configuration file.

## Behavior and operational limits

- Source tables/views are read-only. RACE persists filters, sorts, formats, calculations, widths, and chart definitions as JSON metadata.
- Folder grants inherit through descendants. A direct grant can add access, while moving an item changes inherited access. Only the owner can move a shared root, manage its sharing, or delete it.
- A clone gets new deterministic IDs, no sharing grants, and an immutable configuration snapshot. Interrupted multi-table clones remain hidden and a retry with the same idempotency key completes them.
- Updates require the current resource version. Stale writes return HTTP `409` rather than overwriting a newer change.
- Delta does not enforce primary/foreign-key uniqueness here. The application validates relationships and detects duplicate IDs. Run one application writer per metadata schema for this MVP; use a database-backed lock or a Lakebase metadata adapter before horizontally scaling metadata writes.
- Databricks SQL metadata writes become visible after warehouse/Delta commit latency. The UI debounces saves, exposes saving/error states, and reloads on conflicts.
- Calculated columns accept numeric constants, source columns, `+ - * /`, `abs()`, `round()`, and `col("Column name")`. SQL values are native parameters and identifiers are validated/quoted.

## Tests

```bash
uv run pytest -q
uv run ruff check race scripts tests
```

The suite covers Delta DDL, Databricks connection configuration, authorization rules, expression/filter safety, validation, and health/security boundaries without creating a local database.
