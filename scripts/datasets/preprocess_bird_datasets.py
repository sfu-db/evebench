#!/usr/bin/env python3
"""Preprocess Bird SQLite pairs with complexity, portability, and optional sampling checks."""

from __future__ import annotations

from pathlib import Path
import argparse

import pandas as pd

from scripts.datasets.shared.calcite import calcite_syntax_errors, calcite_schema_errors
from scripts.datasets.shared.csv_io import atomic_csv
from scripts.datasets.shared.features import analyze_pair, pair_is_too_simple, query_features
from scripts.datasets.shared.portability import (
    CONSTRAINT_MODES, clean_schema_constraints, postgres_order_by_error,
    translate_query_for_calcite, translate_schema_for_calcite,
)
from scripts.datasets.shared.sampling import sample_pairs
from scripts.datasets.shared.sqlite import load_pairs
from scripts.paths import ROOT

DEFAULT_INPUT = ROOT / "datasets/raw/bird_benchmark.csv"


def reduce_file(
    source: Path,
    output: Path,
    *,
    require_calcite: bool = True,
    require_postgres_ordering: bool = True,
    constraint_mode: str = "all",
    translate_queries: bool = True,
    max_per_group: int | None = None,
) -> tuple[int, int]:
    """Apply complexity/portability checks, optionally sample, and write pairs.

    None preserves the original policy; zero deduplicates eligible pairs only.
    Positive caps sample by database, schema, label, and both query vectors.
    """
    frame = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return _write_reduced(
        frame, output, require_calcite=require_calcite,
        require_postgres_ordering=require_postgres_ordering,
        constraint_mode=constraint_mode, translate_queries=translate_queries,
        max_per_group=max_per_group,
    )


def preprocess_file(
    source: Path, output: Path, *, skip_missing_queries: bool = False,
    require_calcite: bool = True, require_postgres_ordering: bool = True,
    constraint_mode: str = "all", translate_queries: bool = True,
    max_per_group: int | None = None,
) -> tuple[int, int]:
    """Validate, reduce, and atomically write Bird pairs; return (written, skipped)."""
    frame, skipped = load_pairs(source, skip_missing_queries=skip_missing_queries)
    written, removed = _write_reduced(
        frame, output, require_calcite=require_calcite,
        require_postgres_ordering=require_postgres_ordering,
        constraint_mode=constraint_mode, translate_queries=translate_queries,
        max_per_group=max_per_group,
    )
    return written, skipped + removed


def _write_reduced(frame: pd.DataFrame, output: Path, **options) -> tuple[int, int]:
    reduced = reduce_pairs(frame, **options)
    atomic_csv(reduced, output)
    return len(reduced), len(frame) - len(reduced)


def reduce_pairs(
    frame: pd.DataFrame, *,
    require_calcite: bool = True, require_postgres_ordering: bool = True,
    constraint_mode: str = "all", translate_queries: bool = True,
    max_per_group: int | None = None,
) -> pd.DataFrame:
    """Apply complexity and portability checks before optional sampling."""
    positions: list[int] = []
    calcite_candidates: list[tuple[int, str, str, str]] = []
    schema_cache: dict[tuple[str, str], str] = {}
    for position, (_, row) in enumerate(frame.iterrows()):
        dialect = row.get("dialect", "sqlite").strip() or "sqlite"
        queries = row["q1"], row["q2"]
        if pair_is_too_simple(analyze_pair(*queries, dialect=dialect))[0]:
            continue
        if require_postgres_ordering and any(
            postgres_order_by_error(query, dialect=dialect) for query in queries
        ):
            continue

        if require_calcite:
            q1, q2 = (
                translate_query_for_calcite(query, dialect=dialect)
                if translate_queries else query
                for query in queries
            )
            schema_key = (row["schema_ddl"], dialect)
            if schema_key not in schema_cache:
                cleaned_schema = clean_schema_constraints(
                    schema_key[0], dialect=dialect, mode=constraint_mode,
                )[0]
                schema_cache[schema_key] = translate_schema_for_calcite(
                    cleaned_schema, dialect=dialect,
                )
            calcite_candidates.append((position, q1, q2, schema_cache[schema_key]))
        else:
            positions.append(position)

    if calcite_candidates:
        query_errors = calcite_syntax_errors([
            query for _, q1, q2, _ in calcite_candidates for query in (q1, q2)
        ])
        schemas = list(dict.fromkeys(candidate[3] for candidate in calcite_candidates))
        schema_errors = dict(zip(schemas, calcite_schema_errors(schemas)))
        positions = [
            position
            for candidate_number, (position, _, _, schema) in enumerate(calcite_candidates)
            if schema_errors[schema] is None
            and query_errors[2 * candidate_number] is None
            and query_errors[2 * candidate_number + 1] is None
        ]

    reduced = frame.iloc[positions].copy()
    if max_per_group is not None:
        reduced, _ = sample_pairs(
            reduced, cap=max_per_group,
            feature_extractor=query_features, default_dialect="sqlite",
        )
    reduced["index"] = range(1, len(reduced) + 1)
    return reduced


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, help="default: <input-file parent>/evebench")
    parser.add_argument("--output-file", type=Path, help="default: <output-dir>/sqlite.csv")
    parser.add_argument("--skip-missing-queries", action="store_true")
    parser.add_argument(
        "--max-per-group", type=int,
        help="sample eligible pairs per feature group; 0 deduplicates only; omitted disables sampling",
    )
    parser.add_argument(
        "--skip-calcite", action="store_true",
        help="do not require both queries to parse with Calcite",
    )
    parser.add_argument(
        "--skip-postgres-ordering", action="store_true",
        help="do not enforce PostgreSQL's DISTINCT/ORDER BY projection rule",
    )
    parser.add_argument(
        "--constraints", choices=CONSTRAINT_MODES, default="all",
        help="temporary Calcite schema cleanup: preserve, remove foreign keys, or all (default)",
    )
    parser.add_argument(
        "--preserve-query-text", action="store_true",
        help="check original query text instead of SQLGlot's ANSI-quoted SQLite rendering",
    )
    args = parser.parse_args()
    output = args.output_file or (args.output_dir or args.input_file.parent / "evebench") / "sqlite.csv"
    written, skipped = preprocess_file(
        args.input_file, output, skip_missing_queries=args.skip_missing_queries,
        require_calcite=not args.skip_calcite,
        require_postgres_ordering=not args.skip_postgres_ordering,
        constraint_mode=args.constraints,
        translate_queries=not args.preserve_query_text,
        max_per_group=args.max_per_group,
    )
    print(f"{output}: {written} rows written, {skipped} rows skipped")


if __name__ == "__main__":
    main()
