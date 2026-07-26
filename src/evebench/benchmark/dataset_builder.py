from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

OUTPUT_COLUMNS = (
    "dbid",
    "index",
    "schema_ddl",
    "q1",
    "q2",
    "verieql_label",
    "clean_ddl",
    "parseval_label",
    "ground_truth",
)


def build_bird_dataset(
    baseline_path: str | Path,
    schema_path: str | Path,
    output_path: str | Path,
) -> int:
    """Join BIRD query pairs with authoritative per-database SQLite DDL."""
    baseline = Path(baseline_path).resolve()
    schema_file = Path(schema_path).resolve()
    output = Path(output_path).resolve()
    schemas = _load_schemas(schema_file)
    rows = _load_baseline(baseline)
    _validate_rows(rows, schemas)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=OUTPUT_COLUMNS,
                lineterminator="\n",
            )
            writer.writeheader()
            for row in rows:
                ddl = schemas[row["dbid"]]
                writer.writerow(
                    {
                        "dbid": row["dbid"],
                        "index": row["index"],
                        "schema_ddl": ddl,
                        "q1": row["q1"],
                        "q2": row["q2"],
                        "verieql_label": row["verieql_label"],
                        "clean_ddl": row["clean_ddl"],
                        "parseval_label": row["parseval_label"],
                        "ground_truth": row["ground_truth"],
                    }
                )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return len(rows)


def _load_schemas(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8") as stream:
        raw: Any = json.load(stream)
    if not isinstance(raw, dict) or not raw:
        raise ValueError("schema JSON must be a non-empty object keyed by dbid")

    schemas: dict[str, str] = {}
    for dbid, statements in raw.items():
        if not isinstance(dbid, str) or not dbid:
            raise ValueError("schema JSON contains an invalid dbid")
        if (
            not isinstance(statements, list)
            or not statements
            or not all(
                isinstance(statement, str) and statement.strip()
                for statement in statements
            )
        ):
            raise ValueError(
                f"schema entry {dbid!r} must be a non-empty DDL string array"
            )
        normalized = [
            statement.strip().rstrip(";").rstrip()
            for statement in statements
        ]
        schemas[dbid] = ";\n\n".join(normalized) + ";"
    return schemas


def _load_baseline(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fieldnames = list(reader.fieldnames or ())
        required = set(OUTPUT_COLUMNS) - {"schema_ddl"}
        missing = sorted(required - set(fieldnames))
        if missing:
            raise ValueError(
                "baseline CSV is missing column(s): " + ", ".join(missing)
            )
        rows = list(reader)
    if not rows:
        raise ValueError("baseline CSV contains no cases")
    return rows


def _validate_rows(
    rows: list[dict[str, str]],
    schemas: dict[str, str],
) -> None:
    missing = sorted({row["dbid"] for row in rows} - set(schemas))
    if missing:
        raise ValueError("missing schema for dbid(s): " + ", ".join(missing))
    seen: set[tuple[str, str]] = set()
    duplicates: set[str] = set()
    for row in rows:
        key = (row["dbid"], row["index"])
        if key in seen:
            duplicates.add(f"{key[0]}:{key[1]}")
        seen.add(key)
    if duplicates:
        raise ValueError(
            "duplicate benchmark case ID(s): " + ", ".join(sorted(duplicates))
        )
