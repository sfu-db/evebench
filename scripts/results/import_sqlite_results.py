#!/usr/bin/env python3
"""Import mapped historical SQLite results as atomic, repeatable leaderboard runs."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import csv
import hashlib
import json
import os
import tempfile

from evebench.benchmark.store import ResultStore
from scripts.paths import ROOT
from scripts.results.analyze_sqlite_results import SOURCES, analyze, read_csv

IMPORT_VERSION = 1


def import_results(sqlite_dir: Path, database: Path) -> list[int]:
    """Import all channels; an unchanged snapshot returns its existing run IDs.

    All three datasets commit together. Existing evaluations are never replaced.
    Import timestamps are not historical execution timestamps; durations are absent.
    """
    with tempfile.TemporaryDirectory(prefix="evebench-import-") as temporary:
        reports = Path(temporary)
        analyze(sqlite_dir, reports)
        cases = {name: read_csv(sqlite_dir / "evebench" / filename)
                 for name, (filename, _) in SOURCES.items()}
        hashes = {name: hashlib.sha256((sqlite_dir / "evebench" / filename).read_bytes()).hexdigest()
                  for name, (filename, _) in SOURCES.items()}
        fingerprint = hashlib.sha256(
            (str(IMPORT_VERSION) + json.dumps(hashes, sort_keys=True)).encode()
            + (reports / "mapped_results.csv").read_bytes()
        ).hexdigest()
        store = ResultStore(database)
        now = datetime.now(timezone.utc).isoformat()
        run_ids = {}
        with store.connect() as connection:
            # Serialize the idempotency check and inserts, including concurrent imports.
            connection.execute("BEGIN IMMEDIATE")
            for name, (filename, _) in SOURCES.items():
                dataset = f"sqlite_{name}_historical"
                existing = connection.execute(
                    "SELECT id FROM benchmark_runs WHERE dataset = ? AND status = 'completed' "
                    "AND json_extract(configuration_json, '$.import_fingerprint') = ?",
                    (dataset, fingerprint),
                ).fetchone()
                if existing:
                    run_ids[name] = existing["id"]
                    continue
                configuration = dict(import_fingerprint=fingerprint, importer="sqlite_historical",
                                     import_version=IMPORT_VERSION, imported_at=now,
                                     timing_available=False, analysis="all four verdict channels",
                                     source_directory=str(sqlite_dir.resolve()))
                cursor = connection.execute(
                    "INSERT INTO benchmark_runs (dataset, dataset_path, dataset_sha256, total_cases, "
                    "timeout_ms, status, configuration_json, started_at, finished_at) "
                    "VALUES (?, ?, ?, ?, 0, 'completed', ?, ?, ?)",
                    (dataset, str((sqlite_dir / "evebench" / filename).resolve()), hashes[name],
                     len(cases[name]), json.dumps(configuration), now, now),
                )
                run_ids[name] = cursor.lastrowid
                connection.executemany(
                    "INSERT INTO benchmark_cases (run_id, case_id, dataset_row, db_id, ground_truth, "
                    "query_left, query_right, schema_ddl, dialect) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(run_ids[name], row["index"], number, row["dbid"], row["ground_truth"],
                      row["q1"], row["q2"], row["schema_ddl"], row["dialect"])
                     for number, row in enumerate(cases[name], 1)],
                )
            with (reports / "mapped_results.csv").open(newline="", encoding="utf-8") as stream:
                def records():
                    for row in csv.DictReader(stream):
                        checker = "historical:" + ":".join(row[k] for k in
                            ("variant", "source_approach", "model", "channel"))
                        display = f"{row['model']} · {row['approach']} · {row['variant']} · {row['channel']}"
                        category = row["prediction"].lower()
                        verdict = {"eq": "equivalent", "neq": "not_equivalent", "unknown": "unknown"}[category]
                        yield (run_ids[row["dataset"]], row["index"], checker, display,
                               "historical", f"import-{IMPORT_VERSION}", category, "completed", verdict,
                               0, now, now, "historical_import",
                               "Imported stored verdict; execution time unavailable.",
                               json.dumps({"timing_available": False}), json.dumps(row))
                connection.executemany(
                    "INSERT INTO evaluation_results (run_id, case_id, checker_id, display_name, "
                    "backend_version, adapter_version, result_category, execution_status, verdict, "
                    "duration_ms, started_at, finished_at, reason_code, message, metrics_json, raw_output) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, case_id, checker_id) DO NOTHING", records(),
                )
        return list(run_ids.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-dir", type=Path, default=ROOT / "datasets/sqlite")
    parser.add_argument("--database", type=Path,
                        default=Path(os.environ.get("EVEBENCH_RESULTS_DB", "data/evebench.sqlite")))
    args = parser.parse_args()
    runs = import_results(args.sqlite_dir, args.database)
    print(f"Historical leaderboard runs: {', '.join(map(str, runs))}")
    print(f"Database: {args.database.resolve()}")


if __name__ == "__main__":
    main()
