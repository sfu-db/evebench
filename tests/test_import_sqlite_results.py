import importlib.util
from pathlib import Path
import sys
import sqlite3

import pytest

from evebench.benchmark.store import ResultStore

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("sqlite_import", SCRIPTS / "import_sqlite_results.py")
importer = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(SCRIPTS))
try:
    spec.loader.exec_module(importer)
finally:
    sys.path.pop(0)


def make_sources(root):
    from analyze_sqlite_results import write_csv
    (root / "evebench").mkdir(parents=True)
    for dataset, (filename, folder) in importer.SOURCES.items():
        (root / folder).mkdir(parents=True, exist_ok=True)
        cases = [dict(index=str(i), source_index=str(i + 10), dbid="db", ground_truth=truth,
                      q1="SELECT 1", q2="SELECT 2", schema_ddl="", dialect="sqlite")
                 for i, truth in [(1, "NEQ"), (2, "EQ")]]
        write_csv(root / "evebench" / filename, cases)
        write_csv(root / folder / f"{dataset}_CE_test.csv",
                  [dict(index="11", dbid="db", counterexample_valid="Y"),
                   dict(index="12", dbid="db", counterexample_valid="N")])


def test_import_is_idempotent_and_leaderboard_matches_analysis(tmp_path):
    root, database = tmp_path / "sources", tmp_path / "results.sqlite"
    make_sources(root)
    runs = importer.import_results(root, database)
    assert importer.import_results(root, database) == runs
    store = ResultStore(database)
    summaries = store.summary_dicts()
    assert len(summaries) == 12
    ce = [r for r in summaries if r["checker_id"].endswith(":validated_counterexample")]
    assert len(ce) == 3
    assert all(r["accuracy"] == .5 and r["neq_count"] == 1 and r["unknown_count"] == 1 for r in ce)
    assert all(r["median_duration_ms"] is None and r["evaluated"] == 2 for r in summaries)
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM evaluation_results").fetchone()[0] == 24
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_import_rolls_back_all_datasets_on_invalid_case(tmp_path):
    from analyze_sqlite_results import read_csv, write_csv
    root, database = tmp_path / "sources", tmp_path / "results.sqlite"
    make_sources(root)
    path = root / "evebench/literature.csv"
    rows = read_csv(path)
    rows[1]["index"] = rows[0]["index"]
    write_csv(path, rows)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        importer.import_results(root, database)
    with ResultStore(database).connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0] == 0
