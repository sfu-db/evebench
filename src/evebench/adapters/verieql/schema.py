from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evebench.adapters.common.schema import SchemaCatalog, parse_schema
from sqlglot import exp


@dataclass(frozen=True, slots=True)
class VeriEQLSchema:
    tables: dict[str, dict[str, str]]
    constraints: list[dict[str, Any]]


def parse_verieql_ddl(schema_ddl: str, dialect: str) -> VeriEQLSchema:
    """Encode table DDL, including separate ADD CONSTRAINT primary/foreign keys.

    Unencodable but parseable DDL is passed to VeriEQL for an unsupported verdict.
    """
    return compile_verieql_schema(parse_schema(schema_ddl, dialect))


def compile_verieql_schema(catalog: SchemaCatalog) -> VeriEQLSchema:
    """Convert an ordered source schema catalog to VeriEQL's native input."""
    tables: dict[str, dict[str, str]] = {}
    constraints: list[dict[str, Any]] = []

    for statement in catalog.statements:
        if isinstance(statement, exp.AlterTable):
            table_name = _table_name(statement.this)
            actions = statement.args.get("actions") or []
            if table_name not in tables:
                raise ValueError(f"ALTER TABLE refers to unknown table: {table_name}")
            if not actions or not all(isinstance(action, exp.AddConstraint) for action in actions):
                constraints.append(_unsupported(statement))
                continue
            for action in actions:
                for item in action.expressions:
                    constraints.extend(_encode_table_constraint(table_name, item))
            continue
        if not (
            isinstance(statement, exp.Create)
            and statement.args.get("kind") == "TABLE"
            and isinstance(statement.this, exp.Schema)
        ):
            constraints.append(_unsupported(statement))
            continue
        schema = statement.this

        table_name = _table_name(schema.this)
        columns: dict[str, str] = {}
        for item in schema.expressions:
            if isinstance(item, exp.ColumnDef):
                column_name = item.name.upper()
                columns[column_name] = _column_type(item)
                constraints.extend(_column_constraints(table_name, column_name, item))
            elif isinstance(item, (exp.PrimaryKey, exp.ForeignKey, exp.Constraint)):
                constraints.extend(_encode_table_constraint(table_name, item))
            else:
                constraints.append(_unsupported(item))
        tables[table_name] = columns

    return VeriEQLSchema(tables=tables, constraints=constraints)


def _encode_table_constraint(table_name: str, item: exp.Expression) -> list[dict[str, Any]]:
    if isinstance(item, exp.Constraint) and any(
        isinstance(c, exp.PrimaryKey) for c in item.expressions
    ):
        result: list[dict[str, Any]] = []
        for pk in item.expressions:
            if not isinstance(pk, exp.PrimaryKey):
                return [_unsupported(item)]
            result.append({"primary": [_attribute(table_name, i.name) for i in pk.expressions]})
        return result
    if isinstance(item, exp.PrimaryKey):
        return [{"primary": [_attribute(table_name, i.name) for i in item.expressions]}]
    return _table_constraints(table_name, item)


def _column_constraints(
    table_name: str,
    column_name: str,
    column: exp.ColumnDef,
) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    for constraint in column.args.get("constraints") or []:
        kind = constraint.args.get("kind")
        if isinstance(kind, exp.NotNullColumnConstraint):
            constraints.append({"not_null": _attribute(table_name, column_name)})
        elif isinstance(kind, exp.PrimaryKeyColumnConstraint):
            constraints.append({"primary": [_attribute(table_name, column_name)]})
        elif isinstance(kind, exp.Reference):
            constraints.extend(_foreign_constraints(table_name, [column_name], kind))
        else:
            constraints.append(_unsupported(constraint))
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

    if not foreign_keys:
        return [_unsupported(expression)]
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
        else:
            constraints.append(_unsupported(foreign_key))
    return constraints


def _foreign_constraints(
    source_table: str,
    source_columns: list[str],
    reference: exp.Reference,
) -> list[dict[str, Any]]:
    target = reference.this
    if not isinstance(target, exp.Schema):
        raise ValueError("VeriEQL foreign key input requires target columns")
    target_table = _table_name(target.this)
    target_columns = [identifier.name for identifier in target.expressions]
    if len(source_columns) != len(target_columns):
        raise ValueError("foreign key has mismatched source and target columns")
    if len(source_columns) != 1:
        return [_unsupported(reference)]
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
        raise ValueError("Column has no declared type")
    if kind.this == exp.DataType.Type.TEXT:
        return "VARCHAR"
    return kind.sql(dialect="mysql")


def _table_name(table: exp.Expression) -> str:
    if isinstance(table, exp.Table) and table.name:
        return table.name.upper()
    raise ValueError("CREATE TABLE is missing a table name")


def _attribute(table: str, column: str) -> dict[str, str]:
    return {"value": f"{table.upper()}__{column.upper()}"}


def _unsupported(expression: exp.Expression) -> dict[str, str]:
    # VeriEQL rejects unknown constraint operators in add_constraints().
    # This keeps the decision in the backend without silently dropping DDL.
    return {"unsupported_schema": expression.sql()}
