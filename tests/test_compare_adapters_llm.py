import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "scripts/compare_adapters_llm.py"
SPEC = importlib.util.spec_from_file_location("adapter_llm_comparison", MODULE)
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


def test_correctness_relation_is_existential_within_each_ensemble():
    assert comparison.classify_correctness(["EQ", "UNKNOWN"], ["UNKNOWN"], "EQ") == "adapter_only"
    assert comparison.classify_correctness(["NEQ"], ["EQ", "UNKNOWN"], "EQ") == "llm_only"
    assert comparison.classify_correctness(["EQ"], ["EQ", "NEQ"], "EQ") == "both"
    assert comparison.classify_correctness(["UNKNOWN"], ["UNKNOWN"], "NEQ") == "neither"


def test_coverage_is_separate_from_correctness():
    assert comparison.classify_coverage(["NEQ"], ["UNKNOWN"]) == "adapter_only"
    assert comparison.classify_correctness(["NEQ"], ["UNKNOWN"], "EQ") == "neither"
    assert comparison.verdict_set(["UNKNOWN", "EQ", "EQ"]) == {"EQ"}


def test_ensemble_outcome_distinguishes_wrong_from_abstained_and_conflicted():
    assert comparison.ensemble_outcome(["UNKNOWN"], "EQ") == "abstained"
    assert comparison.ensemble_outcome(["NEQ", "UNKNOWN"], "EQ") == "wrong"
    assert comparison.ensemble_outcome(["EQ"], "EQ") == "correct"
    assert comparison.ensemble_outcome(["EQ", "NEQ"], "EQ") == "correct_with_conflict"
