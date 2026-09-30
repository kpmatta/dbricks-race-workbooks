import json
from contextlib import contextmanager

from race.config import Settings


class Session:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, params=None):
        cursor = self.connection.cursor()
        try:
            cursor.execute(statement, params or {})
            if not cursor.description:
                return []
            names = [c[0] for c in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dialect = "databricks"

    def table(self, name):
        allowed = {
            "folders",
            "workbooks",
            "sheets",
            "charts",
            "data_sources",
            "resource_permissions",
            "audit_events",
            "schema_versions",
        }
        if name not in allowed:
            raise ValueError("Invalid metadata table")
        return f"`{self.settings.metadata_catalog}`.`{self.settings.metadata_schema}`.`{name}`"

    @contextmanager
    def session(self, *, data_user=None, transaction=False):
        from databricks import sql
        from databricks.sdk.core import Config

        host = self.settings.workspace_host or ""
        http_path = self.settings.warehouse_http_path
        if not http_path:
            raise RuntimeError(
                "Configure DATABRICKS_SQL_HTTP_PATH or the sql-warehouse resource in app.yaml"
            )
        args = {}
        if data_user and data_user.token:
            args["access_token"] = data_user.token
        else:
            if data_user and self.settings.mode == "databricks":
                raise RuntimeError("Dataset access requires the user's token")
            config_args = {}
            if self.settings.config_profile:
                config_args["profile"] = self.settings.config_profile
            if host:
                config_args["host"] = host
            cfg = Config(**config_args)
            host = host or cfg.host
            args["credentials_provider"] = lambda: cfg.authenticate
        with sql.connect(
            server_hostname=host.removeprefix("https://").rstrip("/"),
            http_path=http_path,
            use_cloud_fetch=False,
            session_configuration={"STATEMENT_TIMEOUT": str(self.settings.query_timeout_seconds)},
            **args,
        ) as conn:
            yield Session(conn)


def json_dump(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False)
