"""SQLite dataset filtering shared by Bird, beers, and literature."""
from contextlib import ExitStack, closing
from pathlib import Path
import logging
import sqlite3

import pandas as pd
import sqlglot

from scripts.datasets.shared.calcite import calcite_syntax_errors
from scripts.datasets.shared.csv_io import COLUMNS, atomic_csv

def normalize_query(
    query: str, *, dialect: str = "sqlite", target_dialect: str | None = None,
) -> str:
    """Remove all comments and render SQL in the requested dialect.

    The output dialect defaults to the source dialect. Formatting and syntax
    may change; parsing and unsupported translation errors propagate.
    All statements are retained so downstream validation can reject batches.
    """
    return ";\n".join(sqlglot.transpile(
        query, read=dialect, write=target_dialect or dialect, comments=False,
        unsupported_level=sqlglot.ErrorLevel.RAISE,
    ))


def normalized_queries_match(q1: str, q2: str, dialect: str = "sqlite") -> bool:
    """Compare all statements after normalizing formatting in the given dialect."""
    try:
        first = sqlglot.parse(q1, read=dialect)
        second = sqlglot.parse(q2, read=dialect)
    except (sqlglot.errors.SqlglotError, ValueError):
        return False
    return bool(first) and all(stmt is not None for stmt in first + second) and (
        [stmt.sql(dialect=dialect) for stmt in first]
        == [stmt.sql(dialect=dialect) for stmt in second]
    )


def valid_query(connection: sqlite3.Connection, query: str) -> bool:
    """Execute a query against its empty schema and roll back any changes."""
    connection.execute("SAVEPOINT evebench_query")
    try:
        connection.execute(query).fetchall()
        return True
    except sqlite3.Error:
        return False
    finally:
        connection.execute("ROLLBACK TO SAVEPOINT evebench_query")
        connection.execute("RELEASE SAVEPOINT evebench_query")


def preprocess_file(
    source: Path, output: Path, *, skip_missing_queries: bool = False,
    schema_column: str = "schema_ddl", dbid: str | None = None,
    source_name: str = "bird",
    require_calcite_syntax: bool = False,
    label_column: str = "ground_truth",
    label_mapping: dict[str, str] | None = None,
    excluded_source_indices: frozenset[str] = frozenset(),
) -> tuple[int, int]:
    """Atomically write valid, nontrivial pairs; return (written, skipped).

    Empty-database execution checks validity, not equivalence. Source ground
    truth labels are retained unchanged. A fixed dbid supports single-database
    inputs without a dbid column.
    """
    if source.resolve() == output.resolve():
        raise ValueError("output must differ from the source CSV")
    frame, skipped = load_pairs(
        source, skip_missing_queries=skip_missing_queries, schema_column=schema_column,
        dbid=dbid, source_name=source_name, require_calcite_syntax=require_calcite_syntax,
        label_column=label_column, label_mapping=label_mapping,
        excluded_source_indices=excluded_source_indices,
    )
    atomic_csv(frame, output)
    return len(frame), skipped


def load_pairs(
    source: Path, *, skip_missing_queries: bool = False,
    schema_column: str = "schema_ddl", dbid: str | None = None,
    source_name: str = "bird", require_calcite_syntax: bool = False,
    label_column: str = "ground_truth",
    label_mapping: dict[str, str] | None = None,
    excluded_source_indices: frozenset[str] = frozenset(),
) -> tuple[pd.DataFrame, int]:
    """Load and validate SQLite pairs without writing an intermediate CSV."""
    frame = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    converted, seen, connections = [], set(), {}
    skipped = 0
    fixed_dbid = dbid
    with ExitStack() as stack:
        for ridx, row in frame.iterrows():
            if row["index"].strip() in excluded_source_indices:
                skipped += 1
                continue
            dbid = (fixed_dbid if fixed_dbid is not None else row["dbid"]).strip()
            ddl = row[schema_column]
            label = row[label_column].strip()
            if label_mapping is not None:
                label = label_mapping.get(label, label)
            context = f"{source.name}:{ridx}: {dbid}:{row['index']}"
            if not dbid or not ddl.strip():
                raise ValueError(f"{context}: missing database ID or schema")
            if label not in {"EQ", "NEQ"}:
                raise ValueError(f"{context}: unsupported {label_column} {label!r}")
            try:
                q1, q2 = (normalize_query(row[name], dialect="sqlite") for name in ("q1", "q2"))
            except sqlglot.errors.SqlglotError:
                skipped += 1
                continue          
            if not q1.strip() or not q2.strip():
                if not skip_missing_queries:
                    raise ValueError(f"{context}: missing query")
                skipped += 1
                continue
            key = (dbid, ddl, q1, q2)
            if normalized_queries_match(q1, q2, dialect="sqlite") or key in seen:
                skipped += 1
                continue
            seen.add(key)
            schema_key = (dbid, ddl)
            if schema_key not in connections:
                connection = stack.enter_context(closing(sqlite3.connect(":memory:")))
                try:
                    connection.executescript(ddl)
                except sqlite3.Error as exc:
                    raise ValueError(f"{context}: invalid schema: {exc}") from exc
                connection.execute("PRAGMA query_only = ON")
                connections[schema_key] = connection
            connection = connections[schema_key]
            if not all(valid_query(connection, query) for query in (q1, q2)):
                skipped += 1
                continue
            converted.append(dict(zip(COLUMNS, (
                dbid, len(converted) + 1, row["index"], ddl, q1, q2, "sqlite", label, source_name,
            ))))

    if require_calcite_syntax:
        # Quote SQLite identifiers that collide with Calcite function keywords.
        parser_queries = []
        for row in converted:
            for side in ("q1", "q2"):
                query = sqlglot.parse_one(row[side], read="sqlite").transform(
                    lambda node: sqlglot.exp.to_identifier(node.name, quoted=True)
                    if isinstance(node, sqlglot.exp.Identifier)
                    and node.name.lower() in {"rank", "min", "count"}
                    else node
                )
                parser_queries.append(query.sql(dialect="sqlite"))
        errors = calcite_syntax_errors(parser_queries)
        retained = []
        for i, row in enumerate(converted):
            pair_errors = errors[2 * i:2 * i + 2]
            if any(error is not None for error in pair_errors):
                skipped += 1
                for side, error in zip(("q1", "q2"), pair_errors):
                    if error is not None:
                        logging.getLogger(__name__).warning(
                            "%s:%s: %s Calcite syntax rejection: %s",
                            source.name, row["source_index"], side, error,
                        )
                continue
            row["index"] = len(retained) + 1
            retained.append(row)
        converted = retained

    return pd.DataFrame(converted, columns=COLUMNS), skipped
