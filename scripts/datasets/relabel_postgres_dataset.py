#!/usr/bin/env python3
"""Apply PostgreSQL 16 SPA labels to an existing processed PostgreSQL dataset."""

from __future__ import annotations

from pathlib import Path
import argparse

import pandas as pd

from scripts.datasets.shared.csv_io import atomic_csv
from scripts.paths import ROOT

DEFAULT_INPUT = ROOT / "datasets/postgres/evebench/postgres.csv"
DEFAULT_LABEL_DIR = ROOT / "datasets/raw/SPA_datasets"
FINAL_LABEL_COLUMN = "pg16_equiv_total"
LABELS = {"true": "EQ", "false": "NEQ"}


def load_labels(label_dir: Path) -> dict[tuple[str, str, str], tuple[str, bool]]:
    """Load labels keyed by processed source filename, database, and source index."""
    labels = {}
    for source in sorted(label_dir.glob("*_pg16.tsv")):
        source_name = source.name.removesuffix("_pg16.tsv") + ".csv"
        frame = pd.read_csv(
            source, sep="\t", dtype=str, keep_default_na=False, encoding="utf-8-sig"
        )
        for _, row in frame.iterrows():
            final_value = row[FINAL_LABEL_COLUMN].strip().lower()
            value = final_value or row["equiv"].strip().lower()
            labels[(source_name, row["db_id"].strip(), row["val_id"].strip())] = (
                LABELS[value],
                bool(final_value),
            )
    return labels


def relabel_dataset(
    source: Path, label_dir: Path, output: Path
) -> tuple[int, int, int, int]:
    """Write labels; return (matched, finalized, changed, unmatched)."""
    frame = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    labels = load_labels(label_dir)
    matched = finalized = changed = 0

    for index, row in frame.iterrows():
        key = (row["source"], row["dbid"], row["source_index"])
        replacement = labels.get(key)
        if replacement is None:
            continue
        label, has_final_label = replacement
        matched += 1
        finalized += has_final_label
        changed += label != row["ground_truth"]
        frame.at[index, "ground_truth"] = label

    atomic_csv(frame, output)
    return matched, finalized, changed, len(frame) - matched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--label-dir", type=Path, default=DEFAULT_LABEL_DIR)
    parser.add_argument(
        "--output-file",
        type=Path,
        help="default: replace --input-file atomically",
    )
    args = parser.parse_args()
    output = args.output_file or args.input_file
    matched, finalized, changed, unmatched = relabel_dataset(
        args.input_file, args.label_dir, output
    )
    print(
        f"{output}: {matched} SPA rows matched, {finalized} final labels used, "
        f"{changed} changed, {unmatched} rows without SPA labels"
    )


if __name__ == "__main__":
    main()
