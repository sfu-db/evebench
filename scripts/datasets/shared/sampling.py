"""Shared deterministic query-pair sampling for dialect-specific reducers.

The feature extractor is passed by the caller to keep sampling independent of
complexity filtering and portability checks.
"""
from collections import Counter, defaultdict
import json

import pandas as pd
import sqlglot

FEATURES = (
    "joins", "outer_joins", "subqueries", "correlated_subqueries", "set_operations",
    "groups", "having", "distinct", "null_sensitive", "cases", "order_by", "limits",
    "aggregates", "max_boolean_depth",
)


def pair_keys(row, *, feature_extractor, default_dialect="sqlite"):
    """Keep identifier case and literal values for duplicate detection."""
    dialect = row.get("dialect", default_dialect).strip() or default_dialect
    queries, signatures = [], []
    for side in ("q1", "q2"):
        statements = sqlglot.parse(row[side], read=dialect)
        if len(statements) != 1 or not isinstance(statements[0], sqlglot.exp.Query):
            raise ValueError("expected exactly one query")
        queries.append(statements[0].sql(dialect=dialect, comments=False))
        features = feature_extractor(row[side], dialect=dialect)[0]
        signatures.append(tuple(min(getattr(features, key), 2) for key in FEATURES))
    base = (dialect, row["dbid"], row["schema_ddl"])
    return base + tuple(sorted(queries)), base + (row["ground_truth"],) + tuple(sorted(signatures))


AUDIT_COLUMNS = (
    "input_index", "source_index", "dbid", "ground_truth", "keep", "reason",
    "duplicate_of", "family", "signature", "analysis_error",
)


def sample_pairs(frame, *, cap=2, feature_extractor, default_dialect="sqlite"):
    """Return a sampled frame and one audit row per input, preserving input order.

    Conflicts and analysis failures bypass the cap. cap=0 means deduplication
    only. Database ID and exact original schema are required grouping context.
    """
    if cap < 0:
        raise ValueError("cap must be nonnegative")
    records, labels = [], defaultdict(set)
    for row in frame.to_dict("records"):
        try:
            duplicate, family = pair_keys(
                row, feature_extractor=feature_extractor, default_dialect=default_dialect,
            )
            error = ""
            labels[duplicate].add(row["ground_truth"])
        except (sqlglot.errors.SqlglotError, ValueError) as exc:
            duplicate, family, error = None, None, str(exc)
        records.append((row, duplicate, family, error))
    seen, families, counts, kept, decisions = {}, {}, Counter(), [], []
    for row, duplicate, family, error in records:
        representative = ""
        if error:
            keep, reason = True, "analysis_failed_preserved"
        elif len(labels[duplicate]) > 1:
            keep, reason = True, "conflicting_labels_preserved"
        elif duplicate in seen:
            keep, reason = False, "duplicate_pair"
            representative = seen[duplicate]
        else:
            keep = not cap or counts[family] < cap
            reason = "representative" if keep else "feature_group_cap"
            if keep:
                seen[duplicate] = row["index"]
                counts[family] += 1
        if family is not None:
            families.setdefault(family, len(families) + 1)
        decisions.append(dict(
            input_index=row["index"], source_index=row.get("source_index", ""),
            dbid=row["dbid"], ground_truth=row["ground_truth"], keep=keep,
            reason=reason, duplicate_of=representative, family=families.get(family, ""),
            signature=json.dumps(family[-2:]) if family else "", analysis_error=error,
        ))
        if keep:
            kept.append({**row, "index": len(kept) + 1})
    return (
        pd.DataFrame(kept, columns=frame.columns),
        pd.DataFrame(decisions, columns=AUDIT_COLUMNS),
    )
