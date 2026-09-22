#!/usr/bin/env python3
"""Compare conventional adapter results with archived LLM conclusions.

The two directional exports use the current dataset label as the reference:
``adapter_only`` means at least one selected adapter concluded the reference
label while no selected LLM run did, and ``llm_only`` means the reverse.
Separate columns retain raw decisiveness and contradictory conclusions so that
coverage is not confused with correctness (or with a formal proof).
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "data/evebench.sqlite"
DEFAULT_DATASET = ROOT / "datasets/postgres/evebench/postgres.csv"
DEFAULT_LLM_ANALYSIS = ROOT / "outputs/Sep2026/full709/analysis"
METADATA_COLUMNS = {"dbid", "index", "source_index", "source", "ground_truth"}
DECISIVE = {"EQ", "NEQ"}


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(100_000_000)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def normalize_result_category(value: str) -> str:
    return {"eq": "EQ", "neq": "NEQ"}.get(value.strip().lower(), "UNKNOWN")


def correctly_solved(verdicts: Iterable[str], ground_truth: str) -> bool:
    return ground_truth in verdicts


def ensemble_outcome(verdicts: Iterable[str], ground_truth: str) -> str:
    values = verdict_set(verdicts)
    if ground_truth in values:
        return "correct_with_conflict" if len(values) > 1 else "correct"
    if values:
        return "wrong"
    return "abstained"


def classify_correctness(
    adapter_verdicts: Iterable[str], llm_verdicts: Iterable[str], ground_truth: str
) -> str:
    adapter_correct = correctly_solved(adapter_verdicts, ground_truth)
    llm_correct = correctly_solved(llm_verdicts, ground_truth)
    if adapter_correct and llm_correct:
        return "both"
    if adapter_correct:
        return "adapter_only"
    if llm_correct:
        return "llm_only"
    return "neither"


def classify_coverage(adapter_verdicts: Iterable[str], llm_verdicts: Iterable[str]) -> str:
    adapter_decisive = any(value in DECISIVE for value in adapter_verdicts)
    llm_decisive = any(value in DECISIVE for value in llm_verdicts)
    if adapter_decisive and llm_decisive:
        return "both"
    if adapter_decisive:
        return "adapter_only"
    if llm_decisive:
        return "llm_only"
    return "neither"


def verdict_set(values: Iterable[str]) -> set[str]:
    return {value for value in values if value in DECISIVE}


def choose_run(connection: sqlite3.Connection, requested: int | None, case_count: int) -> int:
    if requested is not None:
        row = connection.execute(
            "SELECT id, status, total_cases FROM benchmark_runs WHERE id = ?", (requested,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Adapter benchmark run does not exist: {requested}")
        if row["status"] != "completed":
            raise ValueError(f"Adapter benchmark run {requested} is {row['status']}, not completed")
        if row["total_cases"] != case_count:
            raise ValueError(
                f"Adapter run {requested} has {row['total_cases']} cases; LLM matrix has {case_count}"
            )
        return requested

    row = connection.execute(
        """
        SELECT id FROM benchmark_runs
        WHERE status = 'completed' AND total_cases = ?
          AND EXISTS (
              SELECT 1 FROM evaluation_results e
              WHERE e.run_id = benchmark_runs.id
                AND e.checker_id NOT LIKE 'historical:%'
                AND e.checker_id != 'llm'
          )
        ORDER BY id DESC LIMIT 1
        """,
        (case_count,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No completed adapter run with {case_count} cases was found")
    return int(row["id"])


def load_adapter_results(
    database: Path, run_id: int | None, case_count: int, selected: set[str] | None
) -> tuple[int, list[str], dict[str, dict[str, dict[str, str]]]]:
    uri = f"file:{database.resolve()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ValueError(f"Cannot open adapter database {database}: {exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        chosen = choose_run(connection, run_id, case_count)
        rows = connection.execute(
            """
            SELECT c.case_id, c.db_id, c.ground_truth, e.checker_id,
                   e.result_category, e.execution_status, e.reason_code
            FROM benchmark_cases c
            JOIN evaluation_results e
              ON e.run_id = c.run_id AND e.case_id = c.case_id
            WHERE c.run_id = ?
            ORDER BY c.dataset_row, e.checker_id
            """,
            (chosen,),
        ).fetchall()
    finally:
        connection.close()

    available = sorted({row["checker_id"] for row in rows
                        if row["checker_id"] != "llm"
                        and not row["checker_id"].startswith("historical:")})
    checkers = sorted(selected) if selected else available
    missing = set(checkers) - set(available)
    if missing:
        raise ValueError(f"Adapter(s) absent from run {chosen}: {', '.join(sorted(missing))}")

    results: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        checker = row["checker_id"]
        if checker not in checkers:
            continue
        results[row["case_id"]][checker] = {
            "prediction": normalize_result_category(row["result_category"]),
            "status": row["execution_status"],
            "reason": row["reason_code"] or "",
            "dbid": row["db_id"] or "",
            "ground_truth": row["ground_truth"] or "",
        }
    return chosen, checkers, results


def markdown_report(
    run_id: int,
    checkers: list[str],
    llm_columns: list[str],
    cases: list[dict[str, Any]],
    pairwise: list[dict[str, Any]],
) -> str:
    correctness = Counter(row["correctness_relation"] for row in cases)
    coverage = Counter(row["coverage_relation"] for row in cases)
    conflicts = sum(row["cross_method_conflict"] == "Y" for row in cases)
    adapter_only_modes = Counter(
        row["llm_outcome"] for row in cases if row["correctness_relation"] == "adapter_only"
    )
    llm_only_modes = Counter(
        row["adapter_outcome"] for row in cases if row["correctness_relation"] == "llm_only"
    )
    lines = [
        "# Adapter–LLM Complementarity",
        "",
        f"Adapter benchmark run: `{run_id}`. Cases: {len(cases)}.",
        f"Adapters: {', '.join(checkers)}.",
        f"LLM runs: {', '.join(llm_columns)}.",
        "",
        "The directional sets are label-aware: a side solves a case when at least one of its",
        "selected systems returns the current ground-truth verdict. A conclusive but opposing",
        "verdict does not count as solving the case. Raw EQ/NEQ coverage is reported separately.",
        "Because some current labels are under audit, these are empirical label agreements, not",
        "independent certifications of semantic correctness.",
        "",
        "## Ensemble comparison",
        "",
        "| Relation | Correctly solved | Raw decisive coverage |",
        "| --- | ---: | ---: |",
    ]
    for relation in ("adapter_only", "llm_only", "both", "neither"):
        lines.append(f"| {relation} | {correctness[relation]} | {coverage[relation]} |")
    lines.extend([
        "",
        f"Cases with at least one cross-method EQ/NEQ conflict: {conflicts}.",
        "",
        "Directional failure modes:",
        "",
        f"- Of the {correctness['adapter_only']} adapter-only cases, the LLM ensemble was "
        f"wrong on {adapter_only_modes['wrong']} and abstained on {adapter_only_modes['abstained']}.",
        f"- Of the {correctness['llm_only']} LLM-only cases, the adapter ensemble was "
        f"wrong on {llm_only_modes['wrong']} and abstained on {llm_only_modes['abstained']}.",
        "",
        "- `adapter_only.csv`: adapters solve the current label and no LLM run does.",
        "- `llm_only.csv`: an LLM run solves the current label and no adapter does.",
        "- `adapter_only_decisive.csv` and `llm_only_decisive.csv`: the literal raw-verdict",
        "  comparison, regardless of the current label.",
        "- `cases.csv`: all cases, individual outcomes, failure statuses, and conflict flags.",
        "- `pairwise_summary.csv`: every adapter versus every LLM run.",
        "",
        "## Pairwise comparison",
        "",
        "| Adapter | LLM run | Adapter only | LLM only | Both | Neither |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ])
    for row in pairwise:
        lines.append(
            f"| {row['adapter']} | {row['llm_run']} | {row['adapter_only']} | "
            f"{row['llm_only']} | {row['both']} | {row['neither']} |"
        )
    return "\n".join(lines) + "\n"


def analyze(
    database: Path,
    dataset_path: Path,
    matrix_path: Path,
    output_dir: Path,
    *,
    run_id: int | None = None,
    selected_checkers: set[str] | None = None,
    selected_llm_columns: set[str] | None = None,
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    dataset_rows = read_csv(dataset_path)
    matrix_rows = read_csv(matrix_path)
    if not matrix_rows:
        raise ValueError(f"LLM prediction matrix is empty: {matrix_path}")
    dataset = {(row["dbid"], row["index"]): row for row in dataset_rows}
    if len(dataset) != len(dataset_rows):
        raise ValueError("Dataset contains duplicate dbid/index keys")
    matrix_keys = {(row["dbid"], row["index"]) for row in matrix_rows}
    if len(matrix_keys) != len(matrix_rows):
        raise ValueError("LLM prediction matrix contains duplicate dbid/index keys")
    if matrix_keys != set(dataset):
        raise ValueError("Dataset and LLM prediction matrix do not contain the same cases")

    possible_llm = [name for name in matrix_rows[0] if name not in METADATA_COLUMNS]
    llm_columns = sorted(selected_llm_columns) if selected_llm_columns else possible_llm
    missing_llm = set(llm_columns) - set(possible_llm)
    if missing_llm:
        raise ValueError(f"LLM column(s) absent from matrix: {', '.join(sorted(missing_llm))}")
    if not llm_columns:
        raise ValueError("No LLM prediction columns selected")

    chosen_run, checkers, adapter_results = load_adapter_results(
        database, run_id, len(matrix_rows), selected_checkers
    )
    cases: list[dict[str, Any]] = []
    pair_counts: dict[tuple[str, str], Counter[str]] = {
        (adapter, llm): Counter() for adapter in checkers for llm in llm_columns
    }
    for matrix in matrix_rows:
        key = (matrix["dbid"], matrix["index"])
        source = dataset[key]
        case_id = f"{key[0]}:{key[1]}"
        adapter_case = adapter_results.get(case_id, {})
        if set(adapter_case) != set(checkers):
            missing = set(checkers) - set(adapter_case)
            raise ValueError(f"Adapter run {chosen_run} is missing {case_id}: {', '.join(sorted(missing))}")
        if any(item["dbid"] != key[0] or item["ground_truth"] != source["ground_truth"]
               for item in adapter_case.values()):
            raise ValueError(f"Adapter/dataset metadata mismatch for {case_id}")

        adapter_predictions = {name: adapter_case[name]["prediction"] for name in checkers}
        llm_predictions = {name: matrix[name].strip().upper() for name in llm_columns}
        invalid = {value for value in llm_predictions.values() if value not in DECISIVE | {"UNKNOWN"}}
        if invalid:
            raise ValueError(f"Invalid LLM prediction(s) for {case_id}: {', '.join(sorted(invalid))}")
        ground_truth = source["ground_truth"]
        adapter_set, llm_set = verdict_set(adapter_predictions.values()), verdict_set(llm_predictions.values())
        relation = classify_correctness(adapter_predictions.values(), llm_predictions.values(), ground_truth)
        coverage_relation = classify_coverage(adapter_predictions.values(), llm_predictions.values())
        row: dict[str, Any] = {
            "dbid": key[0],
            "index": key[1],
            "source_index": source.get("source_index", ""),
            "source": source.get("source", ""),
            "ground_truth": ground_truth,
            "correctness_relation": relation,
            "coverage_relation": coverage_relation,
            "adapter_outcome": ensemble_outcome(adapter_predictions.values(), ground_truth),
            "llm_outcome": ensemble_outcome(llm_predictions.values(), ground_truth),
            "adapter_correct": "Y" if correctly_solved(adapter_predictions.values(), ground_truth) else "N",
            "llm_correct": "Y" if correctly_solved(llm_predictions.values(), ground_truth) else "N",
            "adapter_verdicts": ";".join(sorted(adapter_set)) or "UNKNOWN",
            "llm_verdicts": ";".join(sorted(llm_set)) or "UNKNOWN",
            "adapter_internal_conflict": "Y" if len(adapter_set) > 1 else "N",
            "llm_internal_conflict": "Y" if len(llm_set) > 1 else "N",
            "cross_method_conflict": "Y" if adapter_set and llm_set and len(adapter_set | llm_set) > 1 else "N",
        }
        for checker in checkers:
            item = adapter_case[checker]
            row[f"adapter_{checker}"] = item["prediction"]
            row[f"adapter_{checker}_status"] = item["status"]
            row[f"adapter_{checker}_reason"] = item["reason"]
        for llm in llm_columns:
            row[f"llm_{llm}"] = llm_predictions[llm]
        row["q1"] = source.get("q1", "")
        row["q2"] = source.get("q2", "")
        row["dialect"] = source.get("dialect", "")
        cases.append(row)

        for adapter in checkers:
            for llm in llm_columns:
                pair_relation = classify_correctness(
                    [adapter_predictions[adapter]], [llm_predictions[llm]], ground_truth
                )
                pair_counts[adapter, llm][pair_relation] += 1

    pairwise = [
        {"adapter": adapter, "llm_run": llm,
         **{name: pair_counts[adapter, llm][name]
            for name in ("adapter_only", "llm_only", "both", "neither")}}
        for adapter in checkers for llm in llm_columns
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    case_fields = list(cases[0])
    write_csv(output_dir / "cases.csv", cases, case_fields)
    write_csv(output_dir / "adapter_only.csv",
              [row for row in cases if row["correctness_relation"] == "adapter_only"], case_fields)
    write_csv(output_dir / "llm_only.csv",
              [row for row in cases if row["correctness_relation"] == "llm_only"], case_fields)
    write_csv(output_dir / "adapter_only_decisive.csv",
              [row for row in cases if row["coverage_relation"] == "adapter_only"], case_fields)
    write_csv(output_dir / "llm_only_decisive.csv",
              [row for row in cases if row["coverage_relation"] == "llm_only"], case_fields)
    write_csv(output_dir / "pairwise_summary.csv", pairwise, list(pairwise[0]))
    breakdown = []
    for relation in ("adapter_only", "llm_only", "both", "neither"):
        selected_cases = [row for row in cases if row["correctness_relation"] == relation]
        for dimension in ("ground_truth", "dbid", "source"):
            for value, count in sorted(Counter(row[dimension] for row in selected_cases).items()):
                breakdown.append({
                    "correctness_relation": relation,
                    "dimension": dimension,
                    "value": value,
                    "count": count,
                })
    write_csv(output_dir / "breakdown.csv", breakdown,
              ["correctness_relation", "dimension", "value", "count"])
    (output_dir / "report.md").write_text(
        markdown_report(chosen_run, checkers, llm_columns, cases, pairwise), encoding="utf-8"
    )
    return chosen_run, cases, pairwise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--llm-analysis-dir", type=Path, default=DEFAULT_LLM_ANALYSIS)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-id", type=int, help="completed adapter run (default: latest matching run)")
    parser.add_argument("--checker", action="append", dest="checkers",
                        help="adapter checker_id to include; repeat to select several")
    parser.add_argument("--llm-column", action="append", dest="llm_columns",
                        help="prediction_matrix column to include; repeat to select several")
    args = parser.parse_args()
    output_dir = args.output_dir or args.llm_analysis_dir / "adapter_llm_comparison"
    try:
        chosen, cases, _ = analyze(
            args.database,
            args.dataset,
            args.llm_analysis_dir / "prediction_matrix.csv",
            output_dir,
            run_id=args.run_id,
            selected_checkers=set(args.checkers) if args.checkers else None,
            selected_llm_columns=set(args.llm_columns) if args.llm_columns else None,
        )
    except (OSError, ValueError, KeyError, csv.Error, sqlite3.Error) as exc:
        parser.error(str(exc))
    counts = Counter(row["correctness_relation"] for row in cases)
    print(f"Compared {len(cases)} cases using adapter run {chosen}")
    print(f"Adapter-only: {counts['adapter_only']}; LLM-only: {counts['llm_only']}")
    print(f"Reports: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
