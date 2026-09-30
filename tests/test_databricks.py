from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from race.auth import User
from race.config import Settings
from race.db import Database
from race.errors import AppError
from race.main import create_app
from race.models import ChartConfig, Filter, RegisterSource
from race.query import Compiler, QueryService, table_identifier


def settings(**values):
    mode = values.pop("mode", "connected")
    return Settings(
        mode=mode,
        databricks_host="https://workspace.example.com",
        databricks_sql_http_path="/sql/1.0/warehouses/abc",
        **values,
    )


def test_database_only_uses_qualified_delta_metadata_tables():
    db = Database(settings())
    assert db.dialect == "databricks"
    assert db.table("folders") == "`sandbox`.`race`.`folders`"
    with pytest.raises(ValueError):
        db.table("untrusted")


def test_connector_uses_oauth_profile_for_metadata():
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.__exit__.return_value = False
    config = MagicMock(host="https://workspace.example.com")
    with (
        patch("databricks.sdk.core.Config", return_value=config) as config_type,
        patch("databricks.sql.connect", return_value=connection) as connect,
    ):
        with Database(settings(databricks_config_profile="race-local")).session():
            pass
    config_type.assert_called_once_with(profile="race-local", host="https://workspace.example.com")
    assert connect.call_args.kwargs["server_hostname"] == "workspace.example.com"
    credentials_provider = connect.call_args.kwargs["credentials_provider"]
    assert callable(credentials_provider)
    assert credentials_provider() == config.authenticate


def test_connector_uses_forwarded_user_token_for_source_data():
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.__exit__.return_value = False
    user = User("42", "Ada", token="forwarded-token")
    with patch("databricks.sql.connect", return_value=connection) as connect:
        with Database(settings(mode="databricks")).session(data_user=user):
            pass
    assert connect.call_args.kwargs["access_token"] == "forwarded-token"
    assert "credentials_provider" not in connect.call_args.kwargs


@pytest.mark.parametrize(
    "expression",
    ["__import__('os').system('id')", "revenue.__class__", "revenue ** 100", "open('file')"],
)
def test_expression_language_rejects_code(expression):
    with pytest.raises(AppError):
        Compiler(["revenue"]).expression(expression)


def test_compiler_uses_databricks_types_and_parameters():
    compiler = Compiler(["revenue", "cost"])
    expression = compiler.expression("round((revenue - cost) / 0, 2)")
    where = compiler.filters([Filter(column="revenue", op="contains", value="'unsafe%")])
    assert "NULLIF" in expression
    assert "AS STRING" in where
    assert "'unsafe%" not in where
    assert "'unsafe%" in compiler.params.values()


@pytest.mark.parametrize("name", ["cat.schema.table", "orders"])
def test_table_identifier_accepts_supported_names(name):
    assert table_identifier(name)


@pytest.mark.parametrize("name", ["cat.schema.table.extra", "cat.schema.table; DROP TABLE x", ""])
def test_table_identifier_rejects_unsafe_names(name):
    with pytest.raises(AppError):
        table_identifier(name)


def test_query_errors_translate_databricks_failures():
    with pytest.raises(AppError) as denied:
        QueryService.translate_error(RuntimeError("INSUFFICIENT_PRIVILEGES"))
    assert denied.value.status == 403
    with pytest.raises(AppError) as invalid:
        QueryService.translate_error(RuntimeError("UNRESOLVED_COLUMN"))
    assert invalid.value.status == 422


def test_models_validate_databricks_sources_and_charts():
    assert RegisterSource(name="Orders", table="main.sales.orders").table == "main.sales.orders"
    with pytest.raises(ValidationError):
        RegisterSource(name="Orders", table="orders")
    with pytest.raises(ValidationError):
        ChartConfig(sheet_id="sheet", dimension="region", aggregation="SUM")


def test_delta_migration_defines_every_metadata_table():
    migration = Path("migrations/001_metadata.sql").read_text().lower()
    expected = {
        "folders", "workbooks", "sheets", "charts", "data_sources",
        "resource_permissions", "audit_events", "schema_versions",
    }
    assert migration.count("using delta") == len(expected)
    for table in expected:
        assert "create table if not exists {{namespace}}." + table in migration


def test_live_page_and_browser_write_boundary_need_no_database():
    with TestClient(create_app(settings())) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert "RACE" in client.get("/").text
        response = client.post(
            "/api/resources/workbooks",
            json={"name": "Cross site"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert response.status_code == 403


def test_app_yaml_configures_delta_schema_and_warehouse_resource():
    config = Path("app.yaml").read_text()
    assert 'value: "sandbox"' in config
    assert 'value: "race"' in config
    warehouse_entry = config.split("name: DATABRICKS_WAREHOUSE_ID", 1)[1].splitlines()[1]
    assert warehouse_entry.strip().startswith(("value: ", "valueFrom: "))
