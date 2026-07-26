from __future__ import annotations

from dataclasses import replace
from functools import lru_cache

import sqlglot
from sqlglot import exp

from evebench.adapters.core import (
    AdapterInputError,
    normalize_adapter_input,
    reject_unknown_options,
)
from evebench.domain.models import (
    CheckOptions,
    CheckerDescriptor,
    EquivalenceCase,
    ExecutionStatus,
)

DEFAULT_VARCHAR_LENGTH = 255


def preprocess_calcite_case(
    case: EquivalenceCase,
    options: CheckOptions,
    descriptor: CheckerDescriptor,
) -> EquivalenceCase:
    """Prepare common case fields and schema for a Calcite-based adapter."""
    normalized = normalize_adapter_input(case, options, descriptor)
    reject_unknown_options(
        options.for_backend(descriptor.checker.checker_id),
        allowed=set(),
        display_name=descriptor.display_name,
    )
    try:
        schema_ddl = preprocess_calcite_ddl(normalized.schema_ddl)
    except Exception as exc:
        raise AdapterInputError(
            ExecutionStatus.INVALID_INPUT,
            "invalid_schema",
            f"Unable to preprocess schema DDL: {exc}",
        ) from exc
    if not schema_ddl:
        raise AdapterInputError(
            ExecutionStatus.INVALID_INPUT,
            "empty_schema",
            "Schema preprocessing produced no CREATE TABLE statements",
        )
    return replace(normalized, schema_ddl=schema_ddl)


@lru_cache(maxsize=128)
def preprocess_calcite_ddl(schema_ddl: str) -> str:
    """Normalize DDL for the MySQL grammar used by Calcite-based backends.

    SQLGlot parses the schema as MySQL-compatible DDL because the vendored
    Calcite backends consume MySQL-style schema files. Foreign keys are removed,
    and bare VARCHAR declarations receive the length required by that grammar.
    If no normalization is needed, return the original text exactly.
    """
    statements = sqlglot.parse(schema_ddl, read="mysql")
    changed = False
    retained: list[exp.Expression] = []

    for statement in statements:
        # SQLGlot represents empty statements between repeated semicolons as
        # None. They carry no schema information.
        if statement is None:
            continue
        statement_changed = _add_required_varchar_lengths(statement)
        statement_changed |= _strip_inline_constraint_names(statement)
        statement_changed |= _strip_column_references(statement)
        statement_changed |= _strip_create_table_foreign_keys(statement)
        keep, alter_changed = _strip_alter_table_foreign_keys(statement)
        statement_changed |= alter_changed
        changed |= statement_changed
        if keep:
            retained.append(statement)

    if not changed:
        return schema_ddl
    if not retained:
        return ""
    return (
        ";\n".join(
            statement.sql(dialect="mysql", identify=True)
            for statement in retained
        )
        + ";"
    )


def _add_required_varchar_lengths(statement: exp.Expression) -> bool:
    changed = False
    for column in statement.find_all(exp.ColumnDef):
        kind = column.args.get("kind")
        if (
            isinstance(kind, exp.DataType)
            and kind.this == exp.DataType.Type.VARCHAR
            and not kind.expressions
        ):
            kind.set(
                "expressions",
                [exp.Literal.number(DEFAULT_VARCHAR_LENGTH)],
            )
            changed = True
    return changed


def _strip_inline_constraint_names(statement: exp.Expression) -> bool:
    """Keep inline constraints but remove SQLite names MySQL cannot parse."""
    changed = False
    for column in statement.find_all(exp.ColumnDef):
        for constraint in column.args.get("constraints") or []:
            if isinstance(constraint, exp.ColumnConstraint) and constraint.this:
                constraint.set("this", None)
                changed = True
    return changed


def _strip_column_references(statement: exp.Expression) -> bool:
    changed = False
    for column in statement.find_all(exp.ColumnDef):
        constraints = column.args.get("constraints") or []
        retained = [
            constraint
            for constraint in constraints
            if not _contains_foreign_key(constraint)
        ]
        if len(retained) != len(constraints):
            column.set("constraints", retained)
            changed = True
    return changed


def _strip_create_table_foreign_keys(statement: exp.Expression) -> bool:
    if not isinstance(statement, exp.Create) or not isinstance(
        statement.this, exp.Schema
    ):
        return False
    expressions = statement.this.expressions
    retained = [
        expression
        for expression in expressions
        if not _contains_foreign_key(expression)
    ]
    if len(retained) == len(expressions):
        return False
    statement.this.set("expressions", retained)
    return True


def _strip_alter_table_foreign_keys(
    statement: exp.Expression,
) -> tuple[bool, bool]:
    if not isinstance(statement, exp.AlterTable):
        return True, False
    actions = statement.args.get("actions") or []
    retained = [
        action for action in actions if not _contains_foreign_key(action)
    ]
    if len(retained) == len(actions):
        return True, False
    statement.set("actions", retained)
    return bool(retained), True


def _contains_foreign_key(expression: exp.Expression) -> bool:
    return (
        isinstance(expression, (exp.ForeignKey, exp.Reference))
        or expression.find(exp.ForeignKey) is not None
        or expression.find(exp.Reference) is not None
    )
