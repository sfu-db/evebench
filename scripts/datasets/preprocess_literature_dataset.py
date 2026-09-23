#!/usr/bin/env python3
"""Preprocess literature pairs into an EveBench SQLite CSV.

Use verieql_label as the source label (EQU becomes EQ). Remove incomplete,
duplicate, normalized-identical, and SQLite-invalid pairs using the shared
preprocessor. Empty-schema execution checks validity, not equivalence.
Exclude upstream symbolic-predicate cases, even when the input replaces their
predicates with executable comparisons. Source labels are not independently
verified by this script.
"""

from __future__ import annotations

from pathlib import Path
import argparse

from scripts.datasets.shared.sqlite import preprocess_file as preprocess_sqlite_file
from scripts.paths import ROOT

DEFAULT_INPUT = ROOT / "datasets/sqlite/output/literature/literature_CE_gpt-4o.csv"
DEFAULT_OUTPUT = ROOT / "datasets/sqlite/evebench/literature.csv"

# Upstream has-symbolic-predicates cases; 11 is already absent in the default CSV.
# https://github.com/VeriEQL/VeriEQL/blob/main/benchmarks/literature/literature-rewrite.jsonlines
# These are original source indices, not the output's sequential indices.
SYMBOLIC_SOURCE_INDICES = frozenset({"10", "11", "22", "23", "25", "27", "33", "35"})


def preprocess_file(source: Path, output: Path) -> tuple[int, int]:
    """Write literature pairs atomically; return (written, skipped)."""
    return preprocess_sqlite_file(
        source, output, skip_missing_queries=True, schema_column="schema",
        source_name="literature", label_column="verieql_label",
        label_mapping={"EQU": "EQ"},
        excluded_source_indices=SYMBOLIC_SOURCE_INDICES,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    written, skipped = preprocess_file(args.input_file, args.output_file)
    print(f"{args.output_file}: {written} rows written, {skipped} skipped")


if __name__ == "__main__":
    main()
