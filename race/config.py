import os
import re
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Databricks Apps materializes app.yaml env entries before starting app.py.
    model_config = SettingsConfigDict(env_prefix="RACE_", extra="ignore")
    mode: Literal["connected", "databricks"] = "connected"
    metadata_catalog: str = "sandbox"
    metadata_schema: str = "race"
    query_timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_page_size: int = Field(default=500, ge=1, le=1000)
    max_chart_points: int = Field(default=1000, ge=1, le=5000)
    databricks_host: str | None = None
    databricks_config_profile: str | None = None
    databricks_sql_http_path: str | None = None
    databricks_warehouse_id: str | None = None

    @model_validator(mode="after")
    def validate_environment(self):
        for name in (self.metadata_catalog, self.metadata_schema):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("Metadata catalog/schema must be simple SQL identifiers")
        if os.getenv("DATABRICKS_APP_NAME") and self.mode != "databricks":
            raise ValueError("Databricks Apps must run with RACE_MODE=databricks")
        if self.mode == "databricks" and not os.getenv("DATABRICKS_APP_NAME"):
            raise ValueError("Databricks identity headers require the Databricks Apps runtime")
        return self

    @property
    def workspace_host(self):
        return os.getenv("DATABRICKS_HOST") or self.databricks_host

    @property
    def warehouse_http_path(self):
        value = os.getenv("DATABRICKS_SQL_HTTP_PATH") or self.databricks_sql_http_path
        if value:
            return value
        warehouse = os.getenv("DATABRICKS_WAREHOUSE_ID") or self.databricks_warehouse_id
        return f"/sql/1.0/warehouses/{warehouse}" if warehouse else None

    @property
    def config_profile(self):
        return os.getenv("DATABRICKS_CONFIG_PROFILE") or self.databricks_config_profile
