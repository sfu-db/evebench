from __future__ import annotations

import sqlglot
from sqlglot import exp

from evebench.adapters.schema import preprocess_calcite_ddl


def test_returns_ddl_unchanged_when_there_are_no_foreign_keys() -> None:
    ddl = "CREATE TABLE t (\n  id INT PRIMARY KEY,\n  name TEXT NOT NULL\n);\n"

    assert preprocess_calcite_ddl(ddl) == ddl


def test_removes_inline_and_table_foreign_keys() -> None:
    ddl = """
    CREATE TABLE parent (
      id INT PRIMARY KEY
    );
    CREATE TABLE child (
      id INT PRIMARY KEY,
      parent_id INT NOT NULL REFERENCES parent(id),
      CONSTRAINT child_parent_fk
        FOREIGN KEY (parent_id) REFERENCES parent(id),
      FOREIGN KEY (parent_id) REFERENCES parent(id)
    );
    """

    processed = preprocess_calcite_ddl(ddl)
    statements = sqlglot.parse(processed, read="mysql")

    assert not any(
        statement.find(exp.ForeignKey) or statement.find(exp.Reference)
        for statement in statements
    )
    child = statements[1]
    parent_id = next(
        column
        for column in child.find_all(exp.ColumnDef)
        if column.name == "parent_id"
    )
    assert "NOT NULL" in parent_id.sql()
    assert "REFERENCES" not in parent_id.sql()
    assert "PRIMARY KEY" in processed


def test_removes_foreign_key_alter_statements_but_keeps_other_alters() -> None:
    ddl = """
    CREATE TABLE child (id INT, parent_id INT);
    ALTER TABLE child
      ADD CONSTRAINT fk_parent
      FOREIGN KEY (parent_id) REFERENCES parent(id);
    ALTER TABLE child ADD COLUMN name TEXT;
    """

    processed = preprocess_calcite_ddl(ddl)

    assert "FOREIGN KEY" not in processed
    assert "ADD COLUMN `name` TEXT" in processed


def test_ignores_empty_statements_between_semicolons() -> None:
    ddl = "CREATE TABLE t (id INT);;;\n"

    assert preprocess_calcite_ddl(ddl) == ddl


def test_adds_length_required_by_calcite_to_bare_varchar() -> None:
    ddl = """
    CREATE TABLE FRPM (
      CDSCODE VARCHAR,
      `COUNTY NAME` VARCHAR,
      label VARCHAR(40),
      PRIMARY KEY (CDSCODE)
    );
    """

    processed = preprocess_calcite_ddl(ddl)

    assert "`CDSCODE` VARCHAR(255)" in processed
    assert "`COUNTY NAME` VARCHAR(255)" in processed
    assert "`label` VARCHAR(40)" in processed
    assert "VARCHAR(40)(255)" not in processed


def test_quotes_reserved_table_and_column_names_when_rewriting() -> None:
    ddl = "CREATE TABLE MATCH (GROUP INT, label VARCHAR);"

    processed = preprocess_calcite_ddl(ddl)

    assert "CREATE TABLE `MATCH`" in processed
    assert "`GROUP` INT" in processed


def test_removes_inline_constraint_name_but_preserves_primary_key() -> None:
    ddl = """
    CREATE TABLE event (
      event_id TEXT CONSTRAINT event_pk PRIMARY KEY,
      name TEXT
    );
    """

    processed = preprocess_calcite_ddl(ddl)

    assert "CONSTRAINT" not in processed
    assert "`event_id` TEXT PRIMARY KEY" in processed
