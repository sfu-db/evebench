"""Render source queries for qed's SQL parser."""

import sqlglot
from sqlglot import exp


def compile_query(sql: str, dialect: str) -> str:
    read = {"postgresql": "postgres", "mariadb": "mysql"}.get(dialect, dialect)
    statements = [statement for statement in sqlglot.parse(sql, read=read) if statement]
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("QED requires exactly one SELECT query per side")
    return statements[0].sql(dialect="mysql")
