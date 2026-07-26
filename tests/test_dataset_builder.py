from __future__ import annotations

import csv
import json

import pytest

from evebench.benchmark import build_bird_dataset


def write_baseline(path, *, dbid: str = "database") -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "dbid",
                "index",
                "q1",
                "q2",
                "verieql_label",
                "clean_ddl",
                "parseval_label",
                "ground_truth",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "dbid": dbid,
                "index": "1",
                "q1": "SELECT id FROM t",
                "q2": "SELECT id FROM t",
                "verieql_label": "EQ",
                "clean_ddl": "CREATE TABLE t (ID INT);",
                "parseval_label": "EQ",
                "ground_truth": "EQ",
            }
        )


def test_builds_dataset_with_full_schema_and_provenance(tmp_path) -> None:
    baseline = tmp_path / "baseline.csv"
    schemas = tmp_path / "schemas.json"
    output = tmp_path / "benchmark.csv"
    write_baseline(baseline)
    schemas.write_text(
        json.dumps(
            {
                "database": [
                    "CREATE TABLE t (id INTEGER PRIMARY KEY)",
                    "CREATE TABLE u (value TEXT);",
                ]
            }
        ),
        encoding="utf-8",
    )

    count = build_bird_dataset(baseline, schemas, output)

    with output.open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    expected = (
        "CREATE TABLE t (id INTEGER PRIMARY KEY);\n\n"
        "CREATE TABLE u (value TEXT);"
    )
    assert count == 1
    assert row["schema_ddl"] == expected
    assert list(row) == [
        "dbid",
        "index",
        "schema_ddl",
        "q1",
        "q2",
        "verieql_label",
        "clean_ddl",
        "parseval_label",
        "ground_truth",
    ]
    assert row["clean_ddl"] == "CREATE TABLE t (ID INT);"
    assert row["ground_truth"] == "EQ"


def test_rejects_baseline_database_without_schema(tmp_path) -> None:
    baseline = tmp_path / "baseline.csv"
    schemas = tmp_path / "schemas.json"
    write_baseline(baseline, dbid="missing")
    schemas.write_text(
        json.dumps({"other": ["CREATE TABLE t (id INT)"]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing schema.*missing"):
        build_bird_dataset(baseline, schemas, tmp_path / "output.csv")
