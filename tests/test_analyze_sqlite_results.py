import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts/analyze_sqlite_results.py"
spec = importlib.util.spec_from_file_location("analysis", MODULE)
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def test_predictions_do_not_infer_equivalence_from_failed_counterexample():
    assert analysis.predictions({"counterexample_valid": "N"})["validated_counterexample"] == "UNKNOWN"
    assert analysis.predictions({"counterexample_valid": "constraint failed"})["validated_counterexample"] == "UNKNOWN"
    result = analysis.predictions({"equiv": "Y", "counterexample_valid": "Y", "verieql_label": "EQU"})
    assert result["explicit_verdict"] == "EQ"
    assert result["validated_counterexample"] == "NEQ"
    assert result["verieql"] == "EQ"


def test_mapping_uses_source_ids_and_dbid_and_retains_missing(tmp_path):
    root = tmp_path / "sqlite"
    (root / "evebench").mkdir(parents=True)
    for dataset, (filename, folder) in analysis.SOURCES.items():
        directory = root / folder
        directory.mkdir(parents=True, exist_ok=True)
        current = [dict(index="1", source_index="24", dbid="a", ground_truth="NEQ"),
                   dict(index="2", source_index="25", dbid="b", ground_truth="EQ")]
        analysis.write_csv(root / "evebench" / filename, current)
        # A legacy result's index refers to source_index, never current index.
        source = [dict(index="24", dbid="a", counterexample_valid="Y"),
                  dict(index="99", dbid="a", counterexample_valid="Y")]
        analysis.write_csv(directory / f"{dataset}_baseline2_test.csv", source)
    summaries, audits = analysis.analyze(root, tmp_path / "reports")
    assert all(r["matched"] == 1 and r["missing"] == 1 and r["excluded_rows"] == 1 for r in audits)
    ce = [r for r in summaries if r["channel"] == "validated_counterexample"]
    assert all(r["accuracy"] == .5 and r["decided_accuracy"] == 1 for r in ce)
    assert all(r["approach"] == "CE" for r in ce)
    assert analysis.key({"id": "33"}, "beers") == ("beers", "33")
    assert analysis.key({"index": "24", "dbid": "a"}, "bird") != analysis.key({"index": "24", "dbid": "b"}, "bird")


def test_duplicates_abstain_and_unknown_counts_against_full_accuracy():
    rows = [dict(ground_truth="EQ", prediction="UNKNOWN", mapping="duplicate"),
            dict(ground_truth="NEQ", prediction="EQ", mapping="matched")]
    result = analysis.metrics(rows)
    assert result["accuracy"] == 0
    assert result["unknown"] == 1
    assert result["neq_as_eq"] == 1
    assert analysis.metrics([])["accuracy"] == ""
