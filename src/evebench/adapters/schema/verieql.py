from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import sqlglot
from sqlglot import exp


@dataclass(frozen=True, slots=True)
class VeriEQLSchema:
    tables: dict[str, dict[str, str]]
    constraints: list[dict[str, Any]]


def parse_verieql_ddl(schema_ddl: str, dialect: str) -> VeriEQLSchema:
    """Translate CREATE TABLE DDL into VeriEQL's native schema representation."""
    return deepcopy(_parse_verieql_ddl(schema_ddl, dialect))


@lru_cache(maxsize=128)
def _parse_verieql_ddl(
    schema_ddl: str,
    dialect: str,
) -> VeriEQLSchema:
    read_dialect = _sqlglot_dialect(dialect)
    statements = sqlglot.parse(schema_ddl, read=read_dialect)
    tables: dict[str, dict[str, str]] = {}
    constraints: list[dict[str, Any]] = []

    for statement in statements:
        if statement is None or not isinstance(statement, exp.Create):
            continue
        schema = statement.this
        if not isinstance(schema, exp.Schema):
            continue

        table_name = _table_name(schema.this)
        columns: dict[str, str] = {}
        for item in schema.expressions:
            if isinstance(item, exp.ColumnDef):
                column_name = item.name.upper()
                columns[column_name] = _column_type(item)
                constraints.extend(
                    _column_constraints(table_name, column_name, item)
                )
            elif isinstance(item, exp.PrimaryKey):
                constraints.append(
                    {
                        "primary": [
                            _attribute(table_name, identifier.name)
                            for identifier in item.expressions
                        ]
                    }
                )
            else:
                constraints.extend(_table_constraints(table_name, item))
        if not columns:
            raise ValueError(f"table {table_name!r} has no columns")
        tables[table_name] = columns

    if not tables:
        raise ValueError("schema contains no CREATE TABLE statements")
    return VeriEQLSchema(tables=tables, constraints=constraints)


def _column_constraints(
    table_name: str,
    column_name: str,
    column: exp.ColumnDef,
) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    for constraint in column.args.get("constraints") or []:
        kind = constraint.args.get("kind")
        if isinstance(kind, exp.NotNullColumnConstraint):
            constraints.append(
                {"not_null": _attribute(table_name, column_name)}
            )
        elif isinstance(kind, exp.PrimaryKeyColumnConstraint):
            constraints.append(
                {"primary": [_attribute(table_name, column_name)]}
            )
        elif isinstance(kind, exp.Reference):
            constraints.extend(
                _foreign_constraints(table_name, [column_name], kind)
            )
    return constraints


def _table_constraints(
    table_name: str,
    expression: exp.Expression,
) -> list[dict[str, Any]]:
    foreign_keys: list[exp.ForeignKey] = []
    if isinstance(expression, exp.ForeignKey):
        foreign_keys.append(expression)
    elif isinstance(expression, exp.Constraint):
        foreign_keys.extend(expression.find_all(exp.ForeignKey))

    constraints: list[dict[str, Any]] = []
    for foreign_key in foreign_keys:
        reference = foreign_key.args.get("reference")
        if isinstance(reference, exp.Reference):
            constraints.extend(
                _foreign_constraints(
                    table_name,
                    [identifier.name for identifier in foreign_key.expressions],
                    reference,
                )
            )
    return constraints


def _foreign_constraints(
    source_table: str,
    source_columns: list[str],
    reference: exp.Reference,
) -> list[dict[str, Any]]:
    target = reference.this
    if not isinstance(target, exp.Schema):
        return []
    target_table = _table_name(target.this)
    target_columns = [identifier.name for identifier in target.expressions]
    if len(source_columns) != len(target_columns):
        raise ValueError("foreign key has mismatched source and target columns")
    return [
        {
            "foreign": [
                _attribute(source_table, source_column),
                _attribute(target_table, target_column),
            ]
        }
        for source_column, target_column in zip(source_columns, target_columns)
    ]


def _column_type(column: exp.ColumnDef) -> str:
    kind = column.args.get("kind")
    if not isinstance(kind, exp.DataType):
        return "INT"
    value = str(kind.this).upper().split(".")[-1]
    if value in {"BOOLEAN", "BOOL"}:
        return "BOOLEAN"
    if value == "DATE":
        return "DATE"
    if any(token in value for token in ("CHAR", "TEXT", "STRING", "ENUM")):
        return "VARCHAR"
    return "INT"


def _table_name(table: exp.Expression) -> str:
    if isinstance(table, exp.Table) and table.name:
        return table.name.upper()
    raise ValueError("CREATE TABLE is missing a table name")


def _attribute(table: str, column: str) -> dict[str, str]:
    return {"value": f"{table.upper()}__{column.upper()}"}


def _sqlglot_dialect(dialect: str) -> str | None:
    return {
        "calcite": "mysql",
        "mariadb": "mysql",
        "postgres": "postgres",
        "postgresql": "postgres",
    }.get(dialect.lower(), dialect.lower() or None)
