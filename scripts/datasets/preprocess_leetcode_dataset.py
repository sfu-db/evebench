#!/usr/bin/env python3
"""Convert legacy LeetCode results to MySQL EveBench pairs.

Preserve source ground_truth, schemas, and SQL. Remove incomplete, duplicate,
formatting-identical, window-function, and Calcite-unparseable pairs. Calcite checks syntax only:
this does not execute MySQL queries or independently verify equivalence labels.
Requires the QED parser JAR (EVEBENCH_QED_PARSER_JAR / EVEBENCH_QED_JAVA).
"""
from __future__ import annotations

from functools import cache
from pathlib import Path
import argparse
import logging

import pandas as pd
import sqlglot

from scripts.datasets.shared.calcite import calcite_syntax_errors
from scripts.datasets.shared.csv_io import COLUMNS, atomic_csv
from scripts.datasets.shared.features import (
    analyze_pair,
    pair_is_too_simple,
    query_features,
)
from scripts.datasets.shared.sampling import sample_pairs
from scripts.paths import ROOT

DEFAULT_INPUT = ROOT / "datasets/raw/old_evebench_output/leetcode/leetcode_CE_deepseek-chat.csv"
DEFAULT_OUTPUT = ROOT / "datasets/leetcode/evebench/leetcode.csv"


@cache
def parse_mysql(query: str) -> tuple[sqlglot.exp.Expression, ...]:
    """Parse once for the preprocessing checks that can share an AST."""
    try:
        return tuple(sqlglot.parse(query, read="mysql"))
    except sqlglot.errors.SqlglotError:
        return ()


def contains_window_function(query: str) -> bool:
    """Find OVER clauses, including named windows, ignoring comments and literals.

    Token inspection also works when SQLGlot cannot parse other MySQL syntax.
    Tokenization failures are left to the mandatory Calcite syntax check.
    """
    try:
        tokens = sqlglot.Dialect.get_or_raise("mysql").tokenize(query)
    except sqlglot.errors.SqlglotError:
        return False
    return any(token.token_type == sqlglot.tokens.TokenType.OVER for token in tokens)


def normalized_queries_match(q1: str, q2: str) -> bool:
    first, second = parse_mysql(q1), parse_mysql(q2)
    return bool(first) and all(stmt is not None for stmt in first + second) and (
        [stmt.sql(dialect="mysql", comments=False) for stmt in first]
        == [stmt.sql(dialect="mysql", comments=False) for stmt in second]
    )


def simple_pair_reason(q1: str, q2: str) -> str | None:
    """Return a difficulty rejection reason, not an equivalence judgment."""
    if any(
        len(statements) != 1 or not isinstance(statements[0], sqlglot.exp.Query)
        for statements in (parse_mysql(q1), parse_mysql(q2))
    ):
        return "complexity_analysis_failed"
    remove, reason = pair_is_too_simple(analyze_pair(q1, q2, dialect="mysql"))
    return reason if remove else None


def query_for_calcite(query: str) -> str:
    """Quote AST identifiers in a temporary MySQL rendering for syntax checks.

    Interval units, function names, and literals are not identifiers. Preserve
    the original input on parse failure or multiple statements so this step
    cannot hide statement boundaries from Calcite.
    """
    statements = parse_mysql(query)
    if len(statements) != 1 or not isinstance(statements[0], sqlglot.exp.Query):
        return query
    return statements[0].sql(dialect="mysql", identify=True)


def preprocess_file(
    source: Path, output: Path, *, max_per_group: int | None = None,
) -> tuple[int, int]:
    """Filter, optionally sample, and atomically write LeetCode pairs."""
    frame = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    candidates, seen = [], set()
    for position, row in frame.iterrows():
        context = f"{source.name}:row {position + 2}:{row['dbid']}:{row['index']}"
        dbid, label = row["dbid"].strip(), row["ground_truth"].strip()
        if not dbid or not row["schema"].strip():
            raise ValueError(f"{context}: missing database ID or schema")
        if label not in {"EQ", "NEQ"}:
            raise ValueError(f"{context}: unsupported ground_truth {label!r}")
        q1, q2 = row["q1"], row["q2"]
        key = (dbid, row["schema"], q1, q2)
        if not q1.strip() or not q2.strip() or key in seen or normalized_queries_match(q1, q2):
            continue
        if contains_window_function(q1) or contains_window_function(q2):
            continue
        seen.add(key)
        candidates.append(dict(zip(COLUMNS, (
            dbid, None, row["index"], row["schema"], q1, q2, "mysql", label, "leetcode",
        ))))
    errors = calcite_syntax_errors(
        [query_for_calcite(row[side]) for row in candidates for side in ("q1", "q2")],
        mysql=True,
    )
    converted = []
    for i, row in enumerate(candidates):
        pair_errors = errors[2 * i:2 * i + 2]
        if any(error is not None for error in pair_errors):
            for side, error in zip(("q1", "q2"), pair_errors):
                if error is not None:
                    logging.getLogger(__name__).warning(
                        "%s:%s:%s: %s Calcite syntax rejection: %s",
                        source.name, row["dbid"], row["source_index"], side, error.splitlines()[0],
                    )
            continue
        reason = simple_pair_reason(row["q1"], row["q2"])
        if reason:
            logging.getLogger(__name__).warning(
                "%s:%s:%s: complexity rejection: %s",
                source.name, row["dbid"], row["source_index"], reason,
            )
            continue
        row["index"] = len(converted) + 1
        converted.append(row)
    result = pd.DataFrame(converted, columns=COLUMNS)
    if max_per_group is not None:
        result, _ = sample_pairs(
            result, cap=max_per_group,
            feature_extractor=query_features, default_dialect="mysql",
        )
    atomic_csv(result, output)
    return len(result), len(frame) - len(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--max-per-group", type=int,
        help=(
            "sample eligible pairs per feature group; 0 deduplicates only; "
            "omitted disables sampling"
        ),
    )
    args = parser.parse_args()
    written, skipped = preprocess_file(
        args.input_file, args.output_file, max_per_group=args.max_per_group,
    )
    print(f"{args.output_file}: {written} rows written, {skipped} skipped")


if __name__ == "__main__":
    main()
