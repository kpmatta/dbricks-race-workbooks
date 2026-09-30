"""Compile a small expression/filter language into parameterized read-only SQL."""

import ast
import math
import re

from race.errors import AppError
from race.models import ChartConfig, SheetConfig


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 128 or "\x00" in value:
        raise AppError(422, "Invalid column name")
    return "`" + value.replace("`", "``") + "`"


def table_identifier(value):
    parts = value.split(".")
    if len(parts) not in (1, 3) or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in parts):
        raise AppError(422, "Use a catalog.schema.table identifier")
    return ".".join(identifier(p) for p in parts)


class Compiler:
    def __init__(self, columns):
        self.columns = set(columns)
        self.params = {}
        self.string_type = "STRING"

    def parameter(self, value):
        key = f"p{len(self.params)}"
        self.params[key] = value
        return f":{key}"

    def column(self, value):
        if value not in self.columns:
            raise AppError(422, f"Unknown column: {value}")
        return identifier(value)

    def expression(self, source):
        try:
            root = ast.parse(source, mode="eval")
        except (SyntaxError, ValueError, RecursionError):
            raise AppError(422, "Invalid calculated-column expression") from None
        if sum(1 for _ in ast.walk(root)) > 80:
            raise AppError(422, "Expression is too complex")

        def visit(node):
            if isinstance(node, ast.Expression):
                return visit(node.body)
            if isinstance(node, ast.Name):
                return self.column(node.id)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                if abs(node.value) > 1e15 or not math.isfinite(node.value):
                    raise AppError(422, "Numeric constant is out of range")
                return self.parameter(node.value)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return f"({'-' if isinstance(node.op, ast.USub) else '+'}{visit(node.operand)})"
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Div):
                    return f"(1.0 * {left} / NULLIF({right}, 0))"
                op = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}[type(node.op)]
                return f"({left} {op} {right})"
            # col("Gross Sales") addresses column names that aren't Python identifiers.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
                if node.func.id == "col" and len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
                    return self.column(node.args[0].value)
                if node.func.id == "abs" and len(node.args) == 1:
                    return f"ABS({visit(node.args[0])})"
                if node.func.id == "round" and len(node.args) in (1, 2):
                    digits = node.args[1] if len(node.args) == 2 else ast.Constant(value=0)
                    if (
                        not isinstance(digits, ast.Constant)
                        or type(digits.value) is not int
                        or not 0 <= digits.value <= 6
                    ):
                        raise AppError(422, "round precision must be an integer from 0 to 6")
                    return f"ROUND({visit(node.args[0])}, {digits.value})"
            raise AppError(422, 'Use columns, numbers, + - * /, abs(), round(), or col("name")')

        return visit(root)

    def filters(self, filters):
        fragments = []
        for f in filters:
            col = self.column(f.column)
            if f.op in ("is_null", "not_null") or (f.value is None and f.op in ("eq", "ne")):
                fragments.append(f"{col} IS {'NOT ' if f.op in ('not_null', 'ne') else ''}NULL")
            elif f.op == "contains":
                # instr is literal substring matching; %, _, and backslashes are not wildcards.
                fragments.append(
                    f"INSTR(LOWER(CAST({col} AS {self.string_type})), LOWER({self.parameter(str(f.value))})) > 0"
                )
            else:
                op = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[f.op]
                fragments.append(f"{col} {op} {self.parameter(f.value)}")
        return " AND ".join(fragments) or "1=1"

    def sheet(self, table, config):
        names = list(self.columns)
        additions = []
        for calc in config.calculations:
            if calc.name in names:
                raise AppError(422, "Calculated column names must be unique and distinct from source columns")
            additions.append(f"{self.expression(calc.expression)} AS {identifier(calc.name)}")
            names.append(calc.name)
        self.columns = set(names)
        for name in (
            config.columns + config.hidden_columns + list(config.formats) + list(config.column_widths)
        ):
            self.column(name)
        return f"(SELECT *{', ' if additions else ''}{', '.join(additions)} FROM {table_identifier(table)}) AS sheet_data"


class QueryService:
    def __init__(self, db):
        self.db = db

    def source_schema(self, source, user):
        table = table_identifier(source["config"]["table"])
        try:
            with self.db.session(data_user=user) as s:
                cursor = s.connection.cursor()
                try:
                    cursor.execute(f"SELECT * FROM {table} LIMIT 0")
                    return [{"name": col[0], "type": str(col[1] or "unknown")} for col in cursor.description]
                finally:
                    cursor.close()
        except Exception as exc:
            self.translate_error(exc)

    def validate(self, source, user, config):
        columns = self.source_schema(source, user)
        compiler = Compiler([c["name"] for c in columns])
        compiler.sheet(source["config"]["table"], config)
        compiler.filters(config.filters)
        for sort in config.sorts:
            compiler.column(sort.column)
        return columns

    @staticmethod
    def translate_error(exc):
        if isinstance(exc, AppError):
            raise exc
        text = str(exc).lower()
        if any(
            term in text
            for term in ("permission", "unauthorized", "not authorized", "insufficient_privileges")
        ):
            raise AppError(403, "Your Databricks identity cannot access this dataset") from None
        if any(term in text for term in ("timeout", "timed out", "interrupted", "cancelled")):
            raise AppError(504, "The query timed out. Narrow the filters and retry.") from None
        if "unresolved_column" in text or "datatype_mismatch" in text:
            raise AppError(
                422, "The query is invalid for this dataset. Check its columns and calculations."
            ) from None
        raise AppError(
            502, "The data query failed. Check warehouse availability and dataset access."
        ) from None

    def rows(self, source, user, config: SheetConfig, request):
        if request.page_size > self.db.settings.max_page_size:
            raise AppError(422, f"Page size exceeds {self.db.settings.max_page_size}")
        columns = self.source_schema(source, user)
        compiler = Compiler([c["name"] for c in columns])
        relation = compiler.sheet(source["config"]["table"], config)
        filters = config.filters if request.filters is None else request.filters
        sorts = config.sorts if request.sorts is None else request.sorts
        where = compiler.filters(filters)
        names = config.columns or [c["name"] for c in columns] + [c.name for c in config.calculations]
        select = ",".join(compiler.column(name) for name in names)
        ordering = ",".join(f"{compiler.column(s.column)} {s.direction.upper()}" for s in sorts)
        if not ordering:
            ordering = compiler.column(names[0])
        size, offset = request.page_size, (request.page - 1) * request.page_size
        try:
            with self.db.session(data_user=user) as s:
                total = s.execute(f"SELECT COUNT(*) AS n FROM {relation} WHERE {where}", compiler.params)[0][
                    "n"
                ]
                rows = s.execute(
                    f"SELECT {select} FROM {relation} WHERE {where} ORDER BY {ordering} "
                    f"LIMIT {size} OFFSET {offset}",
                    compiler.params,
                )
            return {
                "data": rows,
                "total": total,
                "page": request.page,
                "last_page": max(1, math.ceil(total / size)),
                "columns": names,
            }
        except Exception as exc:
            self.translate_error(exc)

    def chart(self, source, user, sheet: SheetConfig, chart: ChartConfig):
        columns = self.source_schema(source, user)
        compiler = Compiler([c["name"] for c in columns])
        relation = compiler.sheet(source["config"]["table"], sheet)
        dimension = compiler.column(chart.dimension)
        measure = compiler.column(chart.measure) if chart.measure else "*"
        where = compiler.filters(sheet.filters + chart.filters)
        limit = self.db.settings.max_chart_points
        try:
            with self.db.session(data_user=user) as s:
                rows = s.execute(
                    f"SELECT {dimension} AS dimension, {chart.aggregation}({measure}) AS value "
                    f"FROM {relation} WHERE {where} GROUP BY {dimension} ORDER BY {dimension} "
                    f"LIMIT {limit + 1}",
                    compiler.params,
                )
            if len(rows) > limit:
                raise AppError(
                    422, f"Chart exceeds {limit} groups. Add filters or use a lower-cardinality dimension."
                )
            return {"data": rows, "aggregation": chart.aggregation, "complete": True}
        except Exception as exc:
            self.translate_error(exc)
