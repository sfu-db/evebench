#!/usr/bin/env python3
"""Extract valid, non-string-matching beers pairs into an EveBench SQLite CSV.

Removes incomplete pairs, queries that fail on the empty SQLite schema or
Calcite syntax parsing (using the QED parser JAR),
normalized string matches, and duplicate pairs. Counterexample/model verdicts
are not validity checks; original ground_truth labels are preserved.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3

try:
    from .preprocess_sqlite_datasets import preprocess_file as preprocess_sqlite_file
except ImportError:  # Direct execution: python scripts/preprocess_beers_dataset.py
    from preprocess_sqlite_datasets import preprocess_file as preprocess_sqlite_file


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "datasets/sqlite/beers copy/beers_baseline_deepseek-chat.csv"
DEFAULT_OUTPUT = ROOT / "datasets/sqlite/evebench/beers.csv"


def preprocess_file(source: Path, output: Path) -> tuple[int, int]:
    """Write clean beers pairs atomically; return (written, skipped)."""
    return preprocess_sqlite_file(
        source, output, skip_missing_queries=True, schema_column="schema",
        dbid="beers", source_name="beers",
        require_calcite_syntax=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        written, skipped = preprocess_file(args.input_file, args.output_file)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(f"{args.output_file}: {written} rows written, {skipped} skipped")


if __name__ == "__main__":
    main()
