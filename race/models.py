from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Kind = Literal["folders", "workbooks", "sheets", "charts"]
Name = Annotated[str, Field(min_length=1, max_length=120)]
Scalar = str | int | float | bool | None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Filter(StrictModel):
    column: str = Field(min_length=1, max_length=128)
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "is_null", "not_null"] = "eq"
    value: Scalar = None


class Sort(StrictModel):
    column: str = Field(min_length=1, max_length=128)
    direction: Literal["asc", "desc"] = "asc"


class Calculation(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    expression: str = Field(min_length=1, max_length=500)


class ColumnFormat(StrictModel):
    type: Literal["text", "number", "currency", "percent", "date"] = "text"
    decimals: int = Field(default=2, ge=0, le=6)


class SheetConfig(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    columns: list[str] = Field(default_factory=list, max_length=200)
    hidden_columns: list[str] = Field(default_factory=list, max_length=200)
    column_widths: dict[str, Annotated[int, Field(ge=50, le=1000)]] = Field(default_factory=dict)
    formats: dict[str, ColumnFormat] = Field(default_factory=dict)
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    sorts: list[Sort] = Field(default_factory=list, max_length=10)
    calculations: list[Calculation] = Field(default_factory=list, max_length=20)

    @field_validator("columns", "hidden_columns")
    @classmethod
    def unique_columns(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Columns must be unique")
        return value


class ChartConfig(StrictModel):
    sheet_id: str = Field(min_length=1, max_length=100)
    type: Literal["bar", "line", "scatter", "pie"] = "bar"
    dimension: str = Field(min_length=1, max_length=128)
    measure: str | None = Field(default=None, max_length=128)
    aggregation: Literal["SUM", "AVG", "COUNT", "MIN", "MAX"] = "SUM"
    filters: list[Filter] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def measure_required(self):
        if self.aggregation != "COUNT" and not self.measure:
            raise ValueError("Choose a measure for this aggregation")
        return self


class CreateResource(StrictModel):
    name: Name
    parent_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value


class UpdateResource(StrictModel):
    version: int = Field(ge=1)
    name: Name | None = None
    parent_id: str | None = None
    config: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        return CreateResource.clean_name(value) if value is not None else None


class ShareRequest(StrictModel):
    principal_id: str = Field(min_length=1, max_length=200)
    role: Literal["viewer", "editor"]
    version: int = Field(ge=1)


class CloneRequest(StrictModel):
    name: Name
    folder_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=100)


class QueryRequest(StrictModel):
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=100, ge=1, le=1000)
    filters: list[Filter] | None = Field(default=None, max_length=30)
    sorts: list[Sort] | None = Field(default=None, max_length=10)


class RegisterSource(StrictModel):
    name: Name
    table: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
