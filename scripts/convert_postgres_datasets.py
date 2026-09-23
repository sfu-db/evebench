#!/usr/bin/env python3
"""Combine PostgreSQL rewrite exports into an EveBench CSV using per-database DDL."""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import psycopg2
import sqlglot

COLUMNS = ("dbid", "index", "source_index", "schema_ddl", "q1", "q2", "dialect", "ground_truth", "source")
DEFAULT_INPUT = Path(__file__).resolve().parents[1] / "datasets" / "postgres"
DEFAULT_SCHEMA_DIR = DEFAULT_INPUT.parent / "schema"
DEFAULT_CONNECTION_STRING = "postgresql://postgres:postgres@14-postgres-1:5432/evebench_parseval"


def remove_line_comments(query: str) -> str:
    """Remove SQL -- comments while preserving quoted strings and identifiers."""
    result = []
    quote = None
    position = 0
    while position < len(query):
        char = query[position]
        if quote:
            result.append(char)
            if char == quote:
                if position + 1 < len(query) and query[position + 1] == quote:
                    result.append(query[position + 1])
                    position += 1
                else:
                    quote = None
        elif char in ("'", '"'):
            quote = char
            result.append(char)
        elif query.startswith("--", position):
            while position < len(query) and query[position] not in "\r\n":
                position += 1
            continue
        else:
            result.append(char)
        position += 1
    return "".join(result)


def validation_dsn(connection_string: str) -> str:
    """Use the server's maintenance database for temporary schema validation."""
    if connection_string.startswith(("postgresql://", "postgres://")):
        url = urlsplit(connection_string)
        return urlunsplit((url.scheme, url.netloc, "/postgres", url.query, url.fragment))
    return connection_string


def normalized_queries_match(original_sql: str, rewritten_sql: str, dialect: str) -> bool:
    """Compare all statements after SQLGlot normalizes their formatting."""
    read_dialect = {"postgresql": "postgres", "mariadb": "mysql"}.get(dialect, dialect)
    try:
        original = sqlglot.parse(original_sql, read=read_dialect)
        rewritten = sqlglot.parse(rewritten_sql, read=read_dialect)
    except (sqlglot.errors.ParseError, ValueError):
        return False
    return bool(original) and all(statement is not None for statement in original + rewritten) and (
        [statement.sql(dialect=read_dialect) for statement in original]
        == [statement.sql(dialect=read_dialect) for statement in rewritten]
    )


def convert_files(
    sources: list[Path], schemas: dict[str, str], output: Path, *, skip_missing_queries: bool = False,
    dialect: str = "postgres", connection_string: str = DEFAULT_CONNECTION_STRING,
) -> tuple[int, int]:
    """Execute query pairs and atomically write valid rows; return (written, skipped)."""
    if any(source.resolve() == output.resolve() for source in sources):
        raise ValueError("output must differ from every source CSV")
    converted = []
    skipped = 0
    rows_by_db = {}
    position = 0
    for source in sources:
        seen = set()
        frame = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        for ridx, row in frame.iterrows():
            dbid, source_index = row["db_id"].strip(), row["val_id"].strip()
            if dbid == "imdb_job":
                skipped += 1
                continue
            if dbid not in schemas:
                raise ValueError(f"{source.name}:{ridx}: missing schema for {dbid!r}")
            label = {"true": "EQ", "false": "NEQ"}.get(row["equiv"].strip().lower())              
            if label is None:
                raise ValueError(f"{source.name}:{ridx}: {dbid}: unsupported equiv value {row['equiv']!r}")
            if not row["original_sql"].strip() or not row["rewritten_sql"].strip():
                if skip_missing_queries:
                    skipped += 1
                    continue
                raise ValueError(
                    f"{source.name}:{ridx}: empty query for {dbid}:{source_index}; "
                    "use --skip-missing-queries to omit incomplete pairs"
                )

            original_sql = remove_line_comments(row["original_sql"])
            rewritten_sql = remove_line_comments(row["rewritten_sql"])
            if normalized_queries_match(original_sql, rewritten_sql, dialect):
                skipped += 1
                continue
            key = (dbid, original_sql, rewritten_sql)
            if key in seen:
                continue
            seen.add(key)
            rows_by_db.setdefault(dbid, []).append((position, source, original_sql, rewritten_sql, dbid, source_index, label))
            position += 1

    for dbid, rows in rows_by_db.items():
        connection = psycopg2.connect(validation_dsn(connection_string))
        try:
            with connection.cursor() as cursor:
                cursor.execute(schemas[dbid])
            for position, source, original_sql, rewritten_sql, dbid, source_index, label in rows:
                valid = True
                for query in (original_sql, rewritten_sql):
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute("SAVEPOINT evebench_query")
                            cursor.execute(query)
                    except psycopg2.Error:
                        valid = False
                    finally:
                        with connection.cursor() as cursor:
                            cursor.execute("ROLLBACK TO SAVEPOINT evebench_query")
                            cursor.execute("RELEASE SAVEPOINT evebench_query")
                    if not valid:
                        break
                if not valid:
                    skipped += 1
                    continue
                converted.append((position, dict(zip(COLUMNS, (
                    dbid, None, source_index, schemas[dbid], original_sql,
                    rewritten_sql, dialect, label, source.name
                )))))
        finally:
            connection.rollback()
            connection.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            ordered_rows = [row for _, row in sorted(converted)]
            for index, row in enumerate(ordered_rows, start=1):
                row["index"] = index
            pd.DataFrame(ordered_rows, columns=COLUMNS).to_csv(
                stream, index=False, lineterminator="\n"
            )
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(converted), skipped

def load_schema_directory(path: Path) -> dict[str, str]:
    """Load <dbid>.sql followed by optional <dbid>_ri.sql constraints.

    The supplied dss.sql defines TPC-H tables, so expose it as tpch.
    """
    schemas = {}
    for source in sorted(path.glob("*.sql")):
        if source.stem.endswith("_ri"):
            continue
        dbid = "tpch" if source.stem == "dss" else source.stem
        if dbid in schemas:
            raise ValueError(f"multiple SQL schema files map to {dbid!r}")
        parts = [source.read_text(encoding="utf-8-sig")]
        constraints = source.with_name(f"{source.stem}_ri.sql")
        if constraints.is_file():
            parts.append(constraints.read_text(encoding="utf-8-sig"))
        
        schemas[dbid] =  "\n\n".join(parts) + "\n"
    return schemas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--schema-dir", type=Path, default=DEFAULT_SCHEMA_DIR,
                        help="directory containing <dbid>.sql and optional <dbid>_ri.sql")
    parser.add_argument("--output-dir", type=Path,
                        help="default: <input-dir>/evebench")
    parser.add_argument("--output-file", type=Path,
                        help="combined CSV path; default: <output-dir>/postgres.csv")
    parser.add_argument("--dialect", type=str, default="postgres")
    parser.add_argument("--connection-string", default=DEFAULT_CONNECTION_STRING,
                        help="PostgreSQL DSN; use {dbid} to connect to a database per dataset")
    parser.add_argument("--skip-missing-queries", action="store_true",
                        help="omit rows with an empty original or rewritten query")
    args = parser.parse_args()
    output = args.output_file or (args.output_dir or args.input_dir / "evebench") / "postgres.csv"
    if output.parent.resolve() == args.input_dir.resolve():
        parser.error("output file must be outside the input directory")
    sources = sorted(args.input_dir.glob("*.csv"))
    if not sources:
        parser.error(f"no CSV files found in {args.input_dir}")
    
    schemas = load_schema_directory(args.schema_dir)
    
    try:
        written, skipped = convert_files(
            sources, schemas, output,
            dialect=args.dialect,
            connection_string=args.connection_string,
            skip_missing_queries=args.skip_missing_queries,
        )
    except (ValueError, OSError, psycopg2.Error) as exc:
        parser.error(str(exc))
    print(f"{output}: {written} rows written, {skipped} skipped")


if __name__ == "__main__":
    main()
