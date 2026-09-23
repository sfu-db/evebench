#!/usr/bin/env python3
"""Analyze Postgres LLM solver results against the EveBench labels.

DF runs can establish EQ through an explicit Y verdict or NEQ through a
validated counterexample accompanying an N verdict. CE runs do not support an
EQ verdict: a validated counterexample is NEQ and every other outcome is
UNKNOWN. An invalid counterexample is not evidence of inequivalence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import argparse
import csv
import json

from scripts.paths import ROOT

DEFAULT_DATASET = ROOT / "datasets/postgres/evebench/postgres.csv"
DEFAULT_RESULTS = ROOT / "outputs/Sep2026/full709"
DEFAULT_OUTPUT = DEFAULT_RESULTS / "analysis"


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(100_000_000)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def ratio(numerator: int, denominator: int) -> float | str:
    return round(numerator / denominator, 6) if denominator else ""


def case_key(row: dict[str, str]) -> tuple[str, str]:
    dbid, index = row.get("dbid", "").strip(), row.get("index", "").strip()
    if not dbid or not index:
        raise ValueError("Every row must have nonempty dbid and index values")
    return dbid, index


def prediction(row: dict[str, str], prompt: str) -> str:
    """Return the conclusion actually supported by one archived result row."""
    if prompt == "DF":
        verdict = row.get("equiv", "").strip().upper()
        if verdict == "Y":
            return "EQ"
        if verdict == "N" and row.get("counterexample_valid", "").strip().upper() == "Y":
            return "NEQ"
        return "UNKNOWN"
    if prompt == "CE":
        return "NEQ" if row.get("counterexample_valid", "").strip().upper() == "Y" else "UNKNOWN"
    raise ValueError(f"Unsupported prompt {prompt!r}; expected DF or CE")


def metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute metrics with NEQ as the positive class."""
    counts = Counter((row["ground_truth"], row["prediction"]) for row in records)
    reported = Counter(row["prediction"] for row in records)
    total = len(records)
    actual_eq = sum(row["ground_truth"] == "EQ" for row in records)
    actual_neq = total - actual_eq

    # Positive means an inequivalence detection (NEQ).
    true_positive = counts["NEQ", "NEQ"]
    false_positive = counts["EQ", "NEQ"]
    true_negative = counts["EQ", "EQ"]
    false_negative = counts["NEQ", "EQ"]
    unknown_eq = counts["EQ", "UNKNOWN"]
    unknown_neq = counts["NEQ", "UNKNOWN"]
    decided = total - reported["UNKNOWN"]

    return {
        "total": total,
        "reported_EQ": reported["EQ"],
        "reported_NEQ": reported["NEQ"],
        "reported_UNKNOWN": reported["UNKNOWN"],
        "decided": decided,
        "coverage": ratio(decided, total),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "unknown_actual_EQ": unknown_eq,
        "unknown_actual_NEQ": unknown_neq,
        # Standard rates omit abstentions from their denominators.
        "false_positive_rate": ratio(false_positive, false_positive + true_negative),
        "false_negative_rate": ratio(false_negative, false_negative + true_positive),
        # These make the effect of UNKNOWN conclusions visible.
        "false_positive_ratio_all_actual_EQ": ratio(false_positive, actual_eq),
        "false_negative_ratio_all_actual_NEQ": ratio(false_negative, actual_neq),
    }


def discover_runs(results_dir: Path) -> list[Path]:
    paths = sorted(results_dir.glob("postgres_*.csv"))
    if not paths:
        raise ValueError(f"No postgres_*.csv result files found in {results_dir}")
    return paths


def load_dataset(path: Path) -> tuple[list[dict[str, str]], dict[tuple[str, str], dict[str, str]]]:
    rows = read_csv(path)
    if not rows:
        raise ValueError(f"Dataset is empty: {path}")
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = case_key(row)
        if key in lookup:
            raise ValueError(f"Duplicate dataset case {key[0]}:{key[1]}")
        if row.get("ground_truth") not in {"EQ", "NEQ"}:
            raise ValueError(f"Invalid ground truth for {key[0]}:{key[1]}")
        lookup[key] = row
    return rows, lookup


def load_run(
    path: Path,
    dataset: dict[tuple[str, str], dict[str, str]],
) -> tuple[tuple[str, str], dict[tuple[str, str], dict[str, str]]]:
    rows = read_csv(path)
    if not rows:
        raise ValueError(f"Result file is empty: {path}")
    models = {row.get("model", "").strip() for row in rows}
    prompts = {row.get("prompt", "").strip().upper() for row in rows}
    if len(models) != 1 or "" in models or len(prompts) != 1:
        raise ValueError(f"Expected exactly one nonempty model and prompt in {path.name}")
    model, prompt = next(iter(models)), next(iter(prompts))
    if prompt not in {"DF", "CE"}:
        raise ValueError(f"Unsupported prompt {prompt!r} in {path.name}")

    lookup: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = case_key(row)
        if key in lookup:
            raise ValueError(f"Duplicate result case {key[0]}:{key[1]} in {path.name}")
        if key not in dataset:
            raise ValueError(f"Unknown result case {key[0]}:{key[1]} in {path.name}")
        expected = dataset[key]["ground_truth"]
        if row.get("ground_truth") != expected:
            raise ValueError(f"Ground-truth mismatch for {key[0]}:{key[1]} in {path.name}")
        lookup[key] = row
    missing = dataset.keys() - lookup.keys()
    if missing:
        first = sorted(missing)[0]
        raise ValueError(f"{path.name} is missing {len(missing)} cases, including {first[0]}:{first[1]}")
    return (prompt, model), lookup


def markdown_report(
    summaries: list[dict[str, Any]],
    disagreements: list[dict[str, Any]],
    conflict_cases: list[dict[str, Any]],
    dataset_path: Path,
    results_dir: Path,
) -> str:
    lines = [
        "# PostgreSQL LLM Solver Analysis",
        "",
        f"Dataset: `{dataset_path}` ({summaries[0]['total']} cases).",
        f"Results: `{results_dir}`.",
        "",
        "DF reports EQ for an explicit Y verdict and NEQ only for an N verdict with a",
        "validated counterexample. CE likewise reports NEQ only for a validated",
        "counterexample. Invalid, failed, and missing counterexamples are UNKNOWN.",
        "NEQ is the positive class: FP means",
        "ground-truth EQ reported as NEQ, and FN means ground-truth NEQ reported as EQ.",
        "Standard FPR/FNR exclude UNKNOWN outcomes; the two `all actual` columns retain them",
        "in the relevant ground-truth-class denominator.",
        "",
        "## Performance",
        "",
        "| Prompt | Model | EQ | NEQ | UNKNOWN | Coverage | FP | FN | FPR | FNR | FP / all EQ | FN / all NEQ |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    def percent(value: float | str) -> str:
        return "n/a" if value == "" else f"{100 * value:.2f}%"

    for row in summaries:
        lines.append(
            f"| {row['prompt']} | {row['model']} | {row['reported_EQ']} | "
            f"{row['reported_NEQ']} | {row['reported_UNKNOWN']} | {percent(row['coverage'])} | "
            f"{row['false_positive']} | {row['false_negative']} | "
            f"{percent(row['false_positive_rate'])} | {percent(row['false_negative_rate'])} | "
            f"{percent(row['false_positive_ratio_all_actual_EQ'])} | "
            f"{percent(row['false_negative_ratio_all_actual_NEQ'])} |"
        )

    by_prompt = Counter(row["prompt"] for row in disagreements)
    original_query_conflicts = sum(row["original_query_evidence_count"] > 0 for row in conflict_cases)
    limit_only_conflicts = len(conflict_cases) - original_query_conflicts
    lines.extend([
        "",
        "## Model disagreements",
        "",
        "A disagreement means at least two models under the same prompt have different",
        "EQ/NEQ/UNKNOWN conclusions. Full case details are in `disagreements.csv`.",
        "",
        f"- DF disagreements: {by_prompt['DF']}",
        f"- CE disagreements: {by_prompt['CE']}",
        "",
        "## Ground-truth EQ conflicts",
        "",
        f"At least one model generated a validated counterexample for {len(conflict_cases)} cases",
        "currently labeled EQ. `label_conflict_cases.csv` contains one row per case, and",
        "`label_conflict_evidence.csv` retains every model counterexample supporting the audit.",
        "",
        f"- {original_query_conflicts} cases have a valid counterexample against the original queries; these are candidates to relabel as NEQ.",
        f"- {limit_only_conflicts} cases conflict only after a shared outer LIMIT was removed; do not relabel these without reviewing that policy.",
        "",
        "The source dataset is not modified automatically. The evidence should be reviewed before",
        "changing labels, especially where the intended benchmark semantics include assumptions",
        "that are not expressed as schema constraints.",
        "",
    ])
    return "\n".join(lines)


def analyze(
    dataset_path: Path, results_dir: Path, output_dir: Path
) -> tuple[list[dict], list[dict], list[dict]]:
    dataset_rows, dataset = load_dataset(dataset_path)
    runs: dict[tuple[str, str], dict[tuple[str, str], dict[str, str]]] = {}
    source_files: dict[tuple[str, str], str] = {}
    for path in discover_runs(results_dir):
        identity, rows = load_run(path, dataset)
        if identity in runs:
            raise ValueError(f"Duplicate run for prompt={identity[0]}, model={identity[1]}")
        runs[identity], source_files[identity] = rows, path.name

    models_by_prompt: dict[str, list[str]] = defaultdict(list)
    for prompt, model in runs:
        models_by_prompt[prompt].append(model)
    for models in models_by_prompt.values():
        models.sort()

    detailed: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for prompt, model in sorted(runs):
        records = []
        for case in dataset_rows:
            key = case_key(case)
            result = runs[prompt, model][key]
            predicted = prediction(result, prompt)
            record = {
                "prompt": prompt,
                "model": model,
                "dbid": key[0],
                "index": key[1],
                "source_index": case.get("source_index", ""),
                "source": case.get("source", ""),
                "ground_truth": case["ground_truth"],
                "prediction": predicted,
                "equiv": result.get("equiv", ""),
                "counterexample_valid": result.get("counterexample_valid", ""),
                "validation_status": result.get("validation_status", ""),
                "result_file": source_files[prompt, model],
            }
            detailed.append(record)
            records.append(record)
        summaries.append({"prompt": prompt, "model": model, **metrics(records)})

    conflict_evidence: list[dict[str, Any]] = []
    conflicts_by_case: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (prompt, model), run in sorted(runs.items()):
        for case in dataset_rows:
            key = case_key(case)
            result = run[key]
            if case["ground_truth"] != "EQ" or result.get("counterexample_valid", "").strip().upper() != "Y":
                continue
            shared_limit = result.get("shared_limit_removed", "").strip()
            evidence = {
                "dbid": key[0],
                "index": key[1],
                "source_index": case.get("source_index", ""),
                "source": case.get("source", ""),
                "ground_truth": case["ground_truth"],
                "prompt": prompt,
                "model": model,
                "validation_scope": "shared_limit_removed" if shared_limit else "original_queries",
                "shared_limit_removed": shared_limit,
                "counterexample": result.get("counterexample", ""),
                "explanation": result.get("explanation", ""),
                "q1_output": result.get("q1_output", ""),
                "q2_output": result.get("q2_output", ""),
                "q1_validation_sql": result.get("q1_validation_sql", ""),
                "q2_validation_sql": result.get("q2_validation_sql", ""),
                "result_file": source_files[prompt, model],
            }
            conflict_evidence.append(evidence)
            conflicts_by_case[key].append(evidence)

    conflict_cases: list[dict[str, Any]] = []
    for case in dataset_rows:
        key = case_key(case)
        evidence = conflicts_by_case.get(key, [])
        if not evidence:
            continue
        original_count = sum(row["validation_scope"] == "original_queries" for row in evidence)
        limit_count = len(evidence) - original_count
        conflict_cases.append({
            "dbid": key[0],
            "index": key[1],
            "source_index": case.get("source_index", ""),
            "source": case.get("source", ""),
            "ground_truth": case["ground_truth"],
            "suggested_action": "review_and_relabel_NEQ" if original_count else "review_limit_policy_only",
            "evidence_count": len(evidence),
            "original_query_evidence_count": original_count,
            "shared_limit_removed_evidence_count": limit_count,
            "models": ";".join(sorted({row["model"] for row in evidence})),
            "prompt_models": ";".join(sorted(f"{row['prompt']}:{row['model']}" for row in evidence)),
            "q1": case.get("q1", ""),
            "q2": case.get("q2", ""),
        })

    predictions = {(row["prompt"], row["model"], row["dbid"], row["index"]): row["prediction"]
                   for row in detailed}
    disagreements: list[dict[str, Any]] = []
    matrix: list[dict[str, Any]] = []
    prediction_columns = [f"{prompt}_{model}" for prompt in sorted(models_by_prompt)
                          for model in models_by_prompt[prompt]]
    for case in dataset_rows:
        dbid, index = case_key(case)
        matrix_row: dict[str, Any] = {
            "dbid": dbid,
            "index": index,
            "source_index": case.get("source_index", ""),
            "source": case.get("source", ""),
            "ground_truth": case["ground_truth"],
        }
        for prompt in sorted(models_by_prompt):
            prompt_predictions = {}
            for model in models_by_prompt[prompt]:
                value = predictions[prompt, model, dbid, index]
                matrix_row[f"{prompt}_{model}"] = value
                prompt_predictions[model] = value
            if len(set(prompt_predictions.values())) > 1:
                disagreements.append({
                    "prompt": prompt,
                    "dbid": dbid,
                    "index": index,
                    "source_index": case.get("source_index", ""),
                    "source": case.get("source", ""),
                    "ground_truth": case["ground_truth"],
                    **prompt_predictions,
                })
        matrix.append(matrix_row)

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_fields = list(summaries[0])
    detail_fields = list(detailed[0])
    disagreement_models = sorted({model for models in models_by_prompt.values() for model in models})
    disagreement_fields = ["prompt", "dbid", "index", "source_index", "source", "ground_truth",
                           *disagreement_models]
    matrix_fields = ["dbid", "index", "source_index", "source", "ground_truth", *prediction_columns]
    conflict_case_fields = list(conflict_cases[0]) if conflict_cases else [
        "dbid", "index", "source_index", "source", "ground_truth", "suggested_action",
        "evidence_count", "original_query_evidence_count", "shared_limit_removed_evidence_count",
        "models", "prompt_models", "q1", "q2",
    ]
    conflict_evidence_fields = list(conflict_evidence[0]) if conflict_evidence else [
        "dbid", "index", "source_index", "source", "ground_truth", "prompt", "model",
        "validation_scope", "shared_limit_removed", "counterexample", "explanation", "q1_output",
        "q2_output", "q1_validation_sql", "q2_validation_sql", "result_file",
    ]
    write_csv(output_dir / "summary.csv", summaries, summary_fields)
    write_csv(output_dir / "predictions.csv", detailed, detail_fields)
    write_csv(output_dir / "disagreements.csv", disagreements, disagreement_fields)
    write_csv(output_dir / "prediction_matrix.csv", matrix, matrix_fields)
    write_csv(output_dir / "label_conflict_cases.csv", conflict_cases, conflict_case_fields)
    write_csv(output_dir / "label_conflict_evidence.csv", conflict_evidence, conflict_evidence_fields)
    (output_dir / "summary.json").write_text(
        json.dumps(summaries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        markdown_report(summaries, disagreements, conflict_cases, dataset_path, results_dir),
        encoding="utf-8",
    )
    return summaries, disagreements, conflict_cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summaries, disagreements, conflict_cases = analyze(args.dataset, args.results_dir, args.output_dir)
    print(f"Analyzed {len(summaries)} model/prompt runs")
    print(f"Found {len(disagreements)} within-prompt model disagreements")
    print(f"Found {len(conflict_cases)} ground-truth EQ cases with validated counterexamples")
    print(f"Reports: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
