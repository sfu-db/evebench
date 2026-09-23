import scripts.results.analyze_postgres_llm_results as analysis


def test_prediction_requires_validated_counterexample_for_neq():
    assert analysis.prediction({"equiv": "Y"}, "DF") == "EQ"
    assert analysis.prediction({"equiv": "N", "counterexample_valid": "Y"}, "DF") == "NEQ"
    assert analysis.prediction({"equiv": "N", "counterexample_valid": "N"}, "DF") == "UNKNOWN"
    assert analysis.prediction({"equiv": "", "counterexample_valid": "Y"}, "DF") == "UNKNOWN"
    assert analysis.prediction({"counterexample_valid": "Y"}, "CE") == "NEQ"
    assert analysis.prediction({"counterexample_valid": "N"}, "CE") == "UNKNOWN"


def test_metrics_treat_neq_as_positive_and_keep_unknown_visible():
    rows = [
        {"ground_truth": "NEQ", "prediction": "NEQ"},
        {"ground_truth": "NEQ", "prediction": "EQ"},
        {"ground_truth": "NEQ", "prediction": "UNKNOWN"},
        {"ground_truth": "EQ", "prediction": "EQ"},
        {"ground_truth": "EQ", "prediction": "NEQ"},
        {"ground_truth": "EQ", "prediction": "UNKNOWN"},
    ]
    result = analysis.metrics(rows)
    assert (result["reported_EQ"], result["reported_NEQ"], result["reported_UNKNOWN"]) == (2, 2, 2)
    assert (result["true_positive"], result["false_positive"]) == (1, 1)
    assert (result["true_negative"], result["false_negative"]) == (1, 1)
    assert result["false_positive_rate"] == 0.5
    assert result["false_negative_rate"] == 0.5
    assert result["false_positive_ratio_all_actual_EQ"] == 0.333333
    assert result["false_negative_ratio_all_actual_NEQ"] == 0.333333
    assert not any("accuracy" in key for key in result)
