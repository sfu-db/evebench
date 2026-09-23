#!/usr/bin/env python3
"""Map historical approach outputs to current SQLite datasets and score them."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import argparse
import csv

from scripts.paths import ROOT

SOURCES = {"beers": ("beers.csv", "beers copy"),
           "bird": ("sqlite.csv", "output/bird"),
           "literature": ("literature.csv", "output/literature")}
ALIASES = {"baseline": "DF", "baseline2": "CE",
           "condition-CoT2": "CE-condition-CoT", "error-type-CoT2": "CE-NonEqType-CoT"}


def read_csv(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(100_000_000)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def label(value: str) -> str:
    return {"Y": "EQ", "EQ": "EQ", "EQU": "EQ", "N": "NEQ",
            "NEQ": "NEQ", "NIE": "NEQ"}.get(value.strip().upper(), "UNKNOWN")


def key(row: dict[str, str], dataset: str, *, current: bool = False) -> tuple[str, str]:
    index = row.get("source_index", "") if current else row.get("index", row.get("id", ""))
    if not index.strip():
        raise ValueError(f"Missing source identifier in {dataset} row")
    return (row.get("dbid", "").strip() if dataset != "beers" else "beers", index.strip())


def predictions(row: dict[str, str]) -> dict[str, str]:
    ce = row.get("counterexample_valid", "").strip().upper()
    return {
        "explicit_verdict": label(row.get("equiv", "")),
        # A failed counterexample does not establish equivalence.
        "validated_counterexample": "NEQ" if ce == "Y" else "UNKNOWN",
        "parseval": label(row.get("parseval_label", "")),
        "verieql": label(row.get("verieql_label", "")),
    }


def metrics(rows: list[dict]) -> dict:
    counts = Counter((r["ground_truth"], r["prediction"]) for r in rows)
    total = len(rows)
    decided = sum(r["prediction"] != "UNKNOWN" for r in rows)
    correct = sum(r["prediction"] == r["ground_truth"] for r in rows)
    ratio = lambda a, b: round(a / b, 6) if b else ""
    return dict(total=total, matched=sum(r["mapping"] == "matched" for r in rows),
                decided=decided, unknown=total-decided, correct=correct,
                coverage=ratio(decided, total), accuracy=ratio(correct, total),
                decided_accuracy=ratio(correct, decided),
                eq_as_eq=counts["EQ", "EQ"], eq_as_neq=counts["EQ", "NEQ"],
                neq_as_eq=counts["NEQ", "EQ"], neq_as_neq=counts["NEQ", "NEQ"],
                eq_recall=ratio(counts["EQ", "EQ"], sum(r["ground_truth"] == "EQ" for r in rows)),
                neq_recall=ratio(counts["NEQ", "NEQ"], sum(r["ground_truth"] == "NEQ" for r in rows)))


def analyze(sqlite_dir: Path, output_dir: Path) -> tuple[list[dict], list[dict]]:
    details, summaries, audits = [], [], []
    for dataset, (filename, folder) in SOURCES.items():
        current = read_csv(sqlite_dir / "evebench" / filename)
        keys = [key(r, dataset, current=True) for r in current]
        key_set = set(keys)
        if len(keys) != len(key_set):
            raise ValueError(f"Duplicate source identifiers in {filename}")
        if any(r["ground_truth"] not in {"EQ", "NEQ"} for r in current):
            raise ValueError(f"Invalid ground truth in {filename}")
        paths = sorted((sqlite_dir / folder).glob("*.csv"))
        if not paths:
            raise ValueError(f"No result CSVs in {sqlite_dir / folder}")
        for path in paths:
            parts = path.stem.split("_", 2)
            if len(parts) != 3 or parts[0] not in {dataset, dataset + "-dummy"}:
                audits.append(dict(dataset=dataset, file=path.name, status="skipped_auxiliary",
                                   rows="", matched="", missing="", duplicate_keys="", excluded_rows=""))
                continue
            prefix, approach, model = parts
            metadata = dict(dataset=dataset, variant="dummy" if prefix.endswith("-dummy") else "original",
                            approach=ALIASES.get(approach, approach), source_approach=approach,
                            model=model, file=path.name)
            source = read_csv(path)
            lookup = defaultdict(list)
            for row in source:
                lookup[key(row, dataset)].append(row)
            duplicates = sum(len(v) > 1 for k, v in lookup.items() if k in key_set)
            matched = sum(len(lookup[k]) == 1 for k in keys)
            audits.append(dict(dataset=dataset, file=path.name, status="loaded", rows=len(source),
                               matched=matched, missing=sum(not lookup[k] for k in keys),
                               duplicate_keys=duplicates,
                               excluded_rows=sum(len(v) for k, v in lookup.items() if k not in key_set)))
            by_channel = defaultdict(list)
            for case, identity in zip(current, keys):
                candidates = lookup[identity]
                mapping = "matched" if len(candidates) == 1 else "duplicate" if candidates else "missing"
                result = candidates[0] if mapping == "matched" else {}
                for channel, prediction in predictions(result).items():
                    record = dict(**metadata, channel=channel, index=case["index"],
                                  source_index=case["source_index"], dbid=case["dbid"],
                                  ground_truth=case["ground_truth"], mapping=mapping,
                                  prediction=prediction, correct=prediction == case["ground_truth"],
                                  source_ground_truth=result.get("ground_truth", ""),
                                  equiv=result.get("equiv", ""),
                                  counterexample_valid=result.get("counterexample_valid", ""))
                    details.append(record)
                    by_channel[channel].append(record)
            for channel, records in by_channel.items():
                summaries.append(dict(**metadata, channel=channel, **metrics(records)))
    if not summaries:
        raise ValueError("No recognized result files")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "summary.csv", summaries)
    write_csv(output_dir / "mapped_results.csv", details)
    write_csv(output_dir / "mapping_audit.csv", audits)
    return summaries, audits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-dir", type=Path, default=ROOT / "datasets/sqlite")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tmp/sqlite_results_analysis")
    args = parser.parse_args()
    summaries, audits = analyze(args.sqlite_dir, args.output_dir)
    print(f"Analyzed {sum(r['status'] == 'loaded' for r in audits)} runs; {len(summaries)} metric rows")
    print(f"Reports: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
