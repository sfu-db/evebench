#!/usr/bin/env python3
"""Preprocess the Bird SQLite dataset into an EveBench CSV."""

from __future__ import annotations

import argparse
import json
import logging
import os
from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import subprocess
import tempfile

import pandas as pd
import sqlglot

COLUMNS = ("dbid", "index", "source_index", "schema_ddl", "q1", "q2", "dialect", "ground_truth", "source")
DEFAULT_INPUT = Path(__file__).resolve().parents[1] / "datasets" / "sqlite" / "bird_benchmark.csv"


def calcite_syntax_errors(queries: list[str]) -> list[str | None]:
    """Batch syntax checks using Calcite bundled with QED; fail on tool errors."""
    root = Path(__file__).resolve().parents[1]
    jar = Path(os.environ.get(
        "EVEBENCH_QED_PARSER_JAR",
        str(root / "baselines/qed/parser/target/qed-parser-1.0-SNAPSHOT-jar-with-dependencies.jar"),
    ))
    if not jar.is_file():
        raise ValueError(f"QED parser JAR not found: {jar}; build baselines/qed/parser first")
    java = os.environ.get("EVEBENCH_QED_JAVA", "java")
    try:
        result = subprocess.run(
            [java, "--class-path", str(jar), str(root / "scripts/CalciteSyntaxCheck.java")],
            input=json.dumps(queries), capture_output=True, text=True, timeout=120,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise ValueError(f"Calcite syntax checker failed: {detail}") from exc
    try:
        errors = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("Invalid output from Calcite syntax checker") from exc
    if not isinstance(errors, list) or len(errors) != len(queries) or any(
        error is not None and not isinstance(error, str) for error in errors
    ):
        raise ValueError("Invalid output from Calcite syntax checker")
    return errors


def remove_line_comments(query: str) -> str:
    """Remove -- comments without changing SQLite quotes or block comments."""
    result = []
    quote = None
    position = 0
    while position < len(query):
        char = query[position]
        if quote:
            result.append(char)
            if char == quote:
                if quote != "]" and position + 1 < len(query) and query[position + 1] == quote:
                    result.append(query[position + 1])
                    position += 1
                else:
                    quote = None
        elif query.startswith("/*", position):
            end = query.find("*/", position + 2)
            end = len(query) if end == -1 else end + 2
            result.append(query[position:end])
            position = end
            continue
        elif char in ("'", '"', "`", "["):
            quote = "]" if char == "[" else char
            result.append(char)
        elif query.startswith("--", position):
            while position < len(query) and query[position] not in "\r\n":
                position += 1
            continue
        else:
            result.append(char)
        position += 1
    return "".join(result)


def normalized_queries_match(q1: str, q2: str) -> bool:
    """Compare all statements after normalizing SQLite formatting."""
    try:
        first = sqlglot.parse(q1, read="sqlite")
        second = sqlglot.parse(q2, read="sqlite")
    except (sqlglot.errors.SqlglotError, ValueError):
        return False
    return bool(first) and all(stmt is not None for stmt in first + second) and (
        [stmt.sql(dialect="sqlite") for stmt in first]
        == [stmt.sql(dialect="sqlite") for stmt in second]
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
    frame = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    required = {"index", schema_column, "q1", "q2", label_column}
    if dbid is None:
        required.add("dbid")
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")
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
            q1, q2 = (remove_line_comments(row[name]) for name in ("q1", "q2"))
            if not q1.strip() or not q2.strip():
                if skip_missing_queries:
                    skipped += 1
                    continue
                raise ValueError(f"{context}: empty query; use --skip-missing-queries to omit incomplete pairs")
            key = (dbid, ddl, q1, q2)
            if normalized_queries_match(q1, q2) or key in seen:
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
        # Token offsets preserve all other syntax, scalar calls, and literals.
        parser_queries = []
        for row in converted:
            for side in ("q1", "q2"):
                query = row[side]
                tokens = sqlglot.Dialect.get_or_raise("sqlite").tokenize(query)
                for i in range(len(tokens) - 1, -1, -1):
                    token = tokens[i]
                    if (
                        token.token_type == sqlglot.tokens.TokenType.VAR
                        and token.text.lower() in {"rank", "min", "count"}
                        and (i + 1 == len(tokens) or tokens[i + 1].token_type
                             != sqlglot.tokens.TokenType.L_PAREN)
                    ):
                        query = query[:token.start] + '"' + token.text + '"' + query[token.end + 1:]
                parser_queries.append(query)
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

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            pd.DataFrame(converted, columns=COLUMNS).to_csv(stream, index=False, lineterminator="\n")
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(converted), skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, help="default: <input-file parent>/evebench")
    parser.add_argument("--output-file", type=Path, help="default: <output-dir>/sqlite.csv")
    parser.add_argument("--skip-missing-queries", action="store_true")
    args = parser.parse_args()
    output = args.output_file or (args.output_dir or args.input_file.parent / "evebench") / "sqlite.csv"
    try:
        written, skipped = preprocess_file(args.input_file, output, skip_missing_queries=args.skip_missing_queries)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(f"{output}: {written} rows written, {skipped} skipped")


if __name__ == "__main__":
    main()
