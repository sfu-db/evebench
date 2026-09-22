#!/usr/bin/env python3
"""Execute literature schemas and queries unchanged on PostgreSQL; report errors.

Each pair uses an isolated schema in a transaction that is always rolled back.
This checks execution on empty tables, not equivalence or label correctness.
"""

import argparse
import csv
import os
from pathlib import Path
import uuid

import psycopg2
from psycopg2 import sql

try:
    from .convert_postgres_datasets import DEFAULT_CONNECTION_STRING, validation_dsn
except ImportError:
    from convert_postgres_datasets import DEFAULT_CONNECTION_STRING, validation_dsn

ROOT = Path(__file__).resolve().parents[1]


def check_file(source: Path, output: Path, connection_string: str) -> tuple[int, int]:
    if source.resolve() == output.resolve():
        raise ValueError("output must differ from input")
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    results = []
    connection = psycopg2.connect(connection_string, connect_timeout=10)
    try:
        for row in rows:
            result = dict(source_index=row["source_index"], dbid=row["dbid"],
                          schema_error="", q1_error="", q2_error="")
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '10s'")
                    cursor.execute("SET LOCAL lock_timeout = '3s'")
                    namespace = sql.Identifier("literature_check_" + uuid.uuid4().hex)
                    cursor.execute(sql.SQL("CREATE SCHEMA {}").format(namespace))
                    cursor.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(namespace))
                    try:
                        cursor.execute(row["schema_ddl"])
                    except psycopg2.Error as exc:
                        result["schema_error"] = str(exc).strip()
                    if not result["schema_error"]:
                        for side in ("q1", "q2"):
                            cursor.execute("SAVEPOINT query_check")
                            try:
                                cursor.execute(row[side])
                                cursor.fetchall()
                            except psycopg2.Error as exc:
                                result[side + "_error"] = str(exc).strip()
                            finally:
                                cursor.execute("ROLLBACK TO SAVEPOINT query_check")
                                cursor.execute("RELEASE SAVEPOINT query_check")
            finally:
                connection.rollback()
            result["status"] = "FAIL" if any(result[k] for k in
                ("schema_error", "q1_error", "q2_error")) else "PASS"
            results.append(result)
    finally:
        connection.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source_index", "dbid", "status",
                                                  "schema_error", "q1_error", "q2_error"])
        writer.writeheader()
        writer.writerows(results)
    passed = sum(row["status"] == "PASS" for row in results)
    return passed, len(results) - passed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", type=Path,
                        default=ROOT / "datasets/sqlite/evebench/literature.csv")
    parser.add_argument("--output-file", type=Path,
                        default=ROOT / "tmp/literature_audit/postgres_execution.csv")
    parser.add_argument("--connection-string", default=os.environ.get(
        "EVEBENCH_POSTGRES_DSN", validation_dsn(DEFAULT_CONNECTION_STRING)))
    args = parser.parse_args()
    try:
        passed, failed = check_file(args.input_file, args.output_file, args.connection_string)
    except (ValueError, OSError, psycopg2.Error) as exc:
        parser.error(str(exc))
    print(f"{args.output_file}: {passed} pairs passed, {failed} failed")


if __name__ == "__main__":
    main()
