#!/usr/bin/env python3
"""Reduce logically simple SQL-equivalence pairs with auditable rules.

This is a postprocessing step: the source dataset remains the canonical,
lossless preprocessing output. It uses semantic feature gates rather than a
single weighted AST-size score, which would confuse long SQL with hard logic.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pandas as pd
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope


DEFAULT_INPUT = Path(__file__).resolve().parents[1] / "datasets/sqlite/evebench/sqlite.csv"
REQUIRED_COLUMNS = {"index", "schema_ddl", "q1", "q2", "ground_truth"}
PROFILES = ("conservative", "balanced", "aggressive", "complex-only")
CONSTRAINT_MODES = ("preserve", "foreign-keys", "all")


def _calcite_errors(inputs: list[str], *, statement_lists: bool) -> list[str | None]:
    """Parse SQL inputs with Calcite in one batch; fail on checker errors."""
    root = Path(__file__).resolve().parents[1]
    jar = Path(os.environ.get(
        "EVEBENCH_QED_PARSER_JAR",
        str(root / "baselines/qed/parser/target/qed-parser-1.0-SNAPSHOT-jar-with-dependencies.jar"),
    ))
    if not jar.is_file():
        raise ValueError(f"QED parser JAR not found: {jar}; build baselines/qed/parser first")
    java = os.environ.get("EVEBENCH_QED_JAVA", "java")
    try:
        command = [java, "--class-path", str(jar), str(root / "scripts/CalciteSyntaxCheck.java")]
        if statement_lists:
            command.append("--statements")
        result = subprocess.run(
            command, input=json.dumps(inputs), capture_output=True, text=True,
            timeout=120, check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise ValueError(f"Calcite syntax checker failed: {detail}") from exc
    try:
        errors = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("Invalid output from Calcite syntax checker") from exc
    if not isinstance(errors, list) or len(errors) != len(inputs) or any(
        error is not None and not isinstance(error, str) for error in errors
    ):
        raise ValueError("Invalid output from Calcite syntax checker")
    return errors


def calcite_syntax_errors(queries: list[str]) -> list[str | None]:
    """Require each input to be exactly one Calcite-parsable query."""
    return _calcite_errors(queries, statement_lists=False)


def calcite_schema_errors(schemas: list[str]) -> list[str | None]:
    """Require each input to be a nonempty Calcite-parsable statement list."""
    return _calcite_errors(schemas, statement_lists=True)


def translate_query_for_calcite(query: str, *, dialect: str = "sqlite") -> str:
    """Render one source query with ANSI quotes while preserving SQLite logic."""
    statements = sqlglot.parse(query, read=dialect)
    if len(statements) != 1 or statements[0] is None:
        raise ValueError(f"expected exactly one SQL statement, found {len(statements)}")
    # SQLite output retains SQLite functions and semantics. identify=True uses
    # ANSI double quotes for every identifier, including Calcite keywords.
    return statements[0].sql(dialect="sqlite", identify=True)


def _contains_foreign_key(expression: exp.Expression) -> bool:
    if isinstance(expression, (exp.ForeignKey, exp.Reference)):
        return True
    return any(_contains_foreign_key(child) for child in expression.iter_expressions())


def clean_schema_constraints(
    schema_ddl: str, *, dialect: str = "sqlite", mode: str = "foreign-keys",
) -> tuple[str, int]:
    """Remove selected table constraints and return (DDL, removed count).

    ``foreign-keys`` preserves keys, uniqueness, nullability, checks, and
    defaults because those constraints can be necessary for an EQ label.
    ``all`` is intentionally explicit because it changes schema semantics.
    """
    if mode not in CONSTRAINT_MODES:
        raise ValueError(
            f"unsupported constraint mode {mode!r}; expected one of {', '.join(CONSTRAINT_MODES)}"
        )
    if mode == "preserve":
        return schema_ddl, 0

    statements = sqlglot.parse(schema_ddl, read=dialect)
    cleaned: list[exp.Expression] = []
    removed = 0

    def drop(expression: exp.Expression) -> bool:
        return mode == "all" or _contains_foreign_key(expression)

    def clean_column(column: exp.ColumnDef) -> exp.ColumnDef:
        nonlocal removed
        result = column.copy()
        constraints = result.args.get("constraints") or []
        kept = [constraint for constraint in constraints if not drop(constraint)]
        removed += len(constraints) - len(kept)
        result.set("constraints", kept)
        return result

    for original in statements:
        if original is None:
            continue
        statement = original.copy()
        if (
            isinstance(statement, exp.Create)
            and statement.args.get("kind") == "TABLE"
            and isinstance(statement.this, exp.Schema)
        ):
            items: list[exp.Expression] = []
            for item in statement.this.expressions:
                if isinstance(item, exp.ColumnDef):
                    items.append(clean_column(item))
                elif drop(item):
                    removed += 1
                else:
                    items.append(item.copy())
            statement.this.set("expressions", items)
        elif isinstance(statement, exp.AlterTable):
            actions: list[exp.Expression] = []
            for action in statement.args.get("actions") or []:
                if isinstance(action, exp.ColumnDef):
                    actions.append(clean_column(action))
                elif isinstance(action, exp.AddConstraint):
                    constraints = [item for item in action.expressions if not drop(item)]
                    removed += len(action.expressions) - len(constraints)
                    if constraints:
                        kept_action = action.copy()
                        kept_action.set("expressions", [item.copy() for item in constraints])
                        actions.append(kept_action)
                else:
                    actions.append(action.copy())
            if not actions:
                continue
            statement.set("actions", actions)
        cleaned.append(statement)
    return ";\n".join(statement.sql(dialect=dialect) for statement in cleaned) + ";", removed


def translate_schema_for_calcite(schema_ddl: str, *, dialect: str = "sqlite") -> str:
    """Render preprocessed DDL with ANSI-quoted identifiers for Calcite."""
    statements = [statement for statement in sqlglot.parse(schema_ddl, read=dialect) if statement]
    if not statements:
        raise ValueError("expected at least one schema statement")
    # PostgreSQL rendering gives Calcite standard types (for example,
    # SQLite DATETIME becomes TIMESTAMP) without touching the stored DDL.
    return ";\n".join(
        statement.sql(dialect="postgres", identify=True) for statement in statements
    ) + ";"


@dataclass(frozen=True)
class QueryFeatures:
    """Features that affect equivalence reasoning, not textual SQL length."""

    nodes: int
    selects: int
    max_select_depth: int
    joins: int
    outer_joins: int
    subqueries: int
    correlated_subqueries: int
    set_operations: int
    aggregates: int
    predicates: int
    boolean_connectors: int
    max_boolean_depth: int
    groups: int
    having: int
    distinct: int
    windows: int
    null_sensitive: int
    arithmetic: int
    casts: int
    cases: int
    scalar_functions: int
    order_by: int
    limits: int


def _ancestor_depth(node: exp.Expression, classes: tuple[type[exp.Expression], ...]) -> int:
    depth = 0
    current: exp.Expression | None = node
    while current is not None:
        if isinstance(current, classes):
            depth += 1
        current = current.parent
    return depth


def _correlated_subquery_count(tree: exp.Expression) -> int:
    """Count nested scopes with qualified references to an outer scope."""
    scopes = traverse_scope(tree) or []
    return sum(
        any(column.table for column in scope.external_columns)
        for scope in scopes[:-1]  # traverse_scope returns the root scope last
    )


def _skeleton(tree: exp.Expression, *, erase_identifiers: bool) -> str:
    """Return normalized SQL with literal or all leaf values erased."""

    def replace(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Literal):
            return exp.Literal.string("__literal__")
        if erase_identifiers and isinstance(node, exp.Identifier):
            return exp.Identifier(this="__identifier__", quoted=True)
        return node

    return tree.transform(replace, copy=True).sql(dialect="sqlite")


def postgres_order_by_error(query: str, *, dialect: str = "sqlite") -> str | None:
    """Check PostgreSQL's DISTINCT/ORDER BY projection requirement.

    Calcite's parser only checks grammar and accepts this construct. PostgreSQL
    permits non-projected ORDER BY expressions normally, but not under SELECT
    DISTINCT. This check is deliberately separate from Calcite syntax status.
    """
    tree = sqlglot.parse_one(query, read=dialect)
    failures: list[str] = []
    for select_number, select in enumerate(tree.find_all(exp.Select), start=1):
        order = select.args.get("order")
        if not select.args.get("distinct") or order is None:
            continue
        projected: set[str] = set()
        aliases: set[str] = set()
        for projection in select.expressions:
            expression = projection.this if isinstance(projection, exp.Alias) else projection
            projected.add(expression.sql(dialect="sqlite"))
            if isinstance(projection, exp.Alias):
                aliases.add(projection.alias.casefold())
        for ordered in order.expressions:
            expression = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            if (
                isinstance(expression, exp.Literal)
                and not expression.is_string
                and expression.this.isdigit()
            ):
                position = int(expression.this)
                if 1 <= position <= len(select.expressions):
                    continue
            if isinstance(expression, exp.Column) and not expression.table:
                if expression.name.casefold() in aliases:
                    continue
            if expression.sql(dialect="sqlite") not in projected:
                failures.append(
                    f"SELECT {select_number}: DISTINCT ORDER BY expression must appear "
                    f"in select list: {expression.sql(dialect='sqlite')}"
                )
    return "; ".join(failures) or None


def query_features(query: str, *, dialect: str = "sqlite") -> tuple[QueryFeatures, str, str]:
    """Parse one query and return logical features plus diagnostic skeletons."""
    statements = sqlglot.parse(query, read=dialect)
    if len(statements) != 1 or statements[0] is None:
        raise ValueError(f"expected exactly one SQL statement, found {len(statements)}")
    tree = statements[0]
    nodes = list(tree.walk())
    counts = Counter(type(node).__name__ for node in nodes)
    boolean_types = (exp.And, exp.Or, exp.Not)
    select_nodes = [node for node in nodes if isinstance(node, exp.Select)]
    boolean_nodes = [node for node in nodes if isinstance(node, boolean_types)]
    aggregates = sum(isinstance(node, exp.AggFunc) for node in nodes)
    scalar_functions = sum(
        isinstance(node, exp.Func) and not isinstance(node, (exp.AggFunc, exp.Cast))
        for node in nodes
    )
    null_sensitive = sum(
        isinstance(node, exp.Null)
        or type(node).__name__ in {"Coalesce", "IfNull", "NullSafeEQ", "NullSafeNEQ"}
        for node in nodes
    )
    outer_joins = sum(
        isinstance(node, exp.Join)
        and str(node.args.get("side") or "").upper() in {"LEFT", "RIGHT", "FULL"}
        for node in nodes
    )
    arithmetic = sum(
        counts[name] for name in ("Add", "Sub", "Mul", "Div", "Mod", "Pow")
    )
    features = QueryFeatures(
        nodes=len(nodes),
        selects=len(select_nodes),
        max_select_depth=max((_ancestor_depth(node, (exp.Select,)) for node in select_nodes), default=0),
        joins=counts["Join"],
        outer_joins=outer_joins,
        # EXISTS/IN query bodies are Select nodes without an explicit Subquery
        # wrapper in SQLGlot, so nested SELECT depth is the safer lower bound.
        subqueries=max(counts["Subquery"], len(select_nodes) - 1),
        correlated_subqueries=_correlated_subquery_count(tree),
        set_operations=counts["Union"] + counts["Intersect"] + counts["Except"],
        aggregates=aggregates,
        predicates=sum(isinstance(node, exp.Predicate) for node in nodes),
        boolean_connectors=len(boolean_nodes),
        max_boolean_depth=max(
            (_ancestor_depth(node, boolean_types) for node in boolean_nodes), default=0,
        ),
        groups=counts["Group"],
        having=counts["Having"],
        distinct=counts["Distinct"],
        windows=counts["Window"],
        null_sensitive=null_sensitive,
        arithmetic=arithmetic,
        casts=counts["Cast"] + counts["TryCast"],
        cases=counts["Case"],
        scalar_functions=scalar_functions,
        order_by=counts["Order"],
        limits=counts["Limit"],
    )
    return (
        features,
        _skeleton(tree, erase_identifiers=False),
        _skeleton(tree, erase_identifiers=True),
    )


def logic_tier(features: QueryFeatures) -> str:
    """Classify the reasoning needed as simple, moderate, or complex."""
    complex_logic = (
        features.outer_joins
        or features.joins >= 2
        or features.subqueries
        or features.correlated_subqueries
        or features.max_select_depth > 1
        or features.set_operations
        or features.groups
        or features.having
        or features.distinct
        or features.windows
        or features.cases
        or features.predicates >= 3
        or features.max_boolean_depth >= 2
        or (features.order_by and features.limits)
    )
    if complex_logic:
        return "complex"
    moderate_logic = (
        features.joins
        or features.aggregates
        or features.predicates >= 2
        or features.boolean_connectors
        or features.null_sensitive
        or features.arithmetic
        or features.casts
        or features.scalar_functions
        or features.order_by
        or features.limits
    )
    return "moderate" if moderate_logic else "simple"


def analyze_pair(q1: str, q2: str, *, dialect: str = "sqlite") -> dict[str, object]:
    """Return pair-aware logical diagnostics for an audit row."""
    first, first_literals, first_structure = query_features(q1, dialect=dialect)
    second, second_literals, second_structure = query_features(q2, dialect=dialect)
    result: dict[str, object] = {
        "q1_logic_tier": logic_tier(first),
        "q2_logic_tier": logic_tier(second),
        "literal_only_change": first_literals == second_literals,
        "same_structure": first_structure == second_structure,
    }
    result.update({f"q1_{key}": value for key, value in asdict(first).items()})
    result.update({f"q2_{key}": value for key, value in asdict(second).items()})
    return result


def pair_is_too_simple(details: dict[str, object], profile: str) -> tuple[bool, str]:
    """Apply a named, inspectable reduction policy."""
    if profile not in PROFILES:
        raise ValueError(f"unsupported profile {profile!r}; expected one of {', '.join(PROFILES)}")
    q1_tier, q2_tier = details["q1_logic_tier"], details["q2_logic_tier"]
    both_simple = q1_tier == q2_tier == "simple"
    if profile == "conservative":
        remove = both_simple and bool(details["same_structure"])
        reason = "both_simple_same_structure" if remove else "retained_by_conservative_profile"
    elif profile == "balanced":
        remove = both_simple
        reason = "both_queries_logically_simple" if remove else "contains_nontrivial_logic"
    elif profile == "aggressive":
        remove = q1_tier != "complex" and q2_tier != "complex"
        reason = "neither_query_has_complex_logic" if remove else "contains_complex_logic"
    else:
        remove = q1_tier != "complex" or q2_tier != "complex"
        reason = "pair_not_complex_on_both_sides" if remove else "both_queries_have_complex_logic"
    return remove, reason


def _atomic_csv(frame: pd.DataFrame, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            frame.to_csv(stream, index=False, lineterminator="\n")
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def reduce_file(
    source: Path,
    output: Path,
    *,
    audit: Path | None = None,
    profile: str = "balanced",
    require_calcite: bool = True,
    require_postgres_ordering: bool = True,
    constraint_mode: str = "all",
    translate_queries: bool = True,
) -> tuple[int, int]:
    """Apply complexity and portability checks, then atomically write pairs."""
    if profile not in PROFILES:
        raise ValueError(f"unsupported profile {profile!r}; expected one of {', '.join(PROFILES)}")
    if constraint_mode not in CONSTRAINT_MODES:
        raise ValueError(
            f"unsupported constraint mode {constraint_mode!r}; "
            f"expected one of {', '.join(CONSTRAINT_MODES)}"
        )
    source_resolved, output_resolved = source.resolve(), output.resolve()
    if source_resolved == output_resolved:
        raise ValueError("output must differ from the source CSV")
    if audit is not None and audit.resolve() in {source_resolved, output_resolved}:
        raise ValueError("audit must differ from source and output CSVs")

    frame = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")

    audits: list[dict[str, object]] = []
    schema_cache: dict[tuple[str, str], tuple[str, int]] = {}
    for row_number, row in frame.iterrows():
        dialect = row.get("dialect", "sqlite").strip() or "sqlite"
        try:
            details = analyze_pair(row["q1"], row["q2"], dialect=dialect)
            translated = {
                side: translate_query_for_calcite(row[side], dialect=dialect)
                if translate_queries else row[side]
                for side in ("q1", "q2")
            }
            schema_key = (row["schema_ddl"], dialect)
            if schema_key not in schema_cache:
                schema_cache[schema_key] = clean_schema_constraints(
                    row["schema_ddl"], dialect=dialect, mode=constraint_mode,
                )
            cleaned_schema, constraints_removed = schema_cache[schema_key]
            calcite_schema = translate_schema_for_calcite(cleaned_schema, dialect=dialect)
        except (sqlglot.errors.SqlglotError, ValueError) as exc:
            raise ValueError(
                f"{source.name}:{row_number}: index {row['index']}: cannot analyze row: {exc}"
            ) from exc
        remove, reason = pair_is_too_simple(details, profile)
        audits.append({
            "input_index": row["index"],
            "source_index": row.get("source_index", ""),
            "dbid": row.get("dbid", ""),
            "ground_truth": row["ground_truth"],
            "constraint_mode": constraint_mode,
            "schema_changed": cleaned_schema != row["schema_ddl"],
            "constraints_removed": constraints_removed,
            "schema_calcite_ddl": calcite_schema,
            "q1_translation_changed": translated["q1"] != row["q1"],
            "q2_translation_changed": translated["q2"] != row["q2"],
            "q1_calcite_sql": translated["q1"],
            "q2_calcite_sql": translated["q2"],
            "logic_keep": not remove,
            "keep": not remove,
            "reason": reason,
            **details,
        })

    calcite_errors: list[str | None] = [None] * (2 * len(frame))
    schema_error_by_ddl: dict[str, str | None] = {}
    if require_calcite:
        queries = [
            record[f"{side}_calcite_sql"] for record in audits for side in ("q1", "q2")
        ]
        calcite_errors = calcite_syntax_errors(queries)
        schemas = list(dict.fromkeys(str(record["schema_calcite_ddl"]) for record in audits))
        schema_error_by_ddl = dict(zip(schemas, calcite_schema_errors(schemas)))

    for position, (_, row) in enumerate(frame.iterrows()):
        record = audits[position]
        pair_calcite_errors = calcite_errors[2 * position:2 * position + 2]
        dialect = row.get("dialect", "sqlite").strip() or "sqlite"
        schema_error = schema_error_by_ddl.get(str(record["schema_calcite_ddl"]))
        postgres_errors = [None, None]
        if require_postgres_ordering:
            postgres_errors = [
                postgres_order_by_error(str(record[f"{side}_calcite_sql"]), dialect=dialect)
                for side in ("q1", "q2")
            ]
        record.update({
            "calcite_q1_ok": pair_calcite_errors[0] is None if require_calcite else "",
            "calcite_q2_ok": pair_calcite_errors[1] is None if require_calcite else "",
            "calcite_q1_error": pair_calcite_errors[0] or "",
            "calcite_q2_error": pair_calcite_errors[1] or "",
            "calcite_schema_ok": schema_error is None if require_calcite else "",
            "calcite_schema_error": schema_error or "",
            "postgres_order_q1_ok": postgres_errors[0] is None if require_postgres_ordering else "",
            "postgres_order_q2_ok": postgres_errors[1] is None if require_postgres_ordering else "",
            "postgres_order_q1_error": postgres_errors[0] or "",
            "postgres_order_q2_error": postgres_errors[1] or "",
        })
        failed_sides = [
            side for side, calcite_error, postgres_error in zip(
                ("q1", "q2"), pair_calcite_errors, postgres_errors,
            ) if calcite_error is not None or postgres_error is not None
        ]
        failed_inputs = failed_sides + (["schema"] if schema_error is not None else [])
        if record["logic_keep"] and failed_inputs:
            record["keep"] = False
            record["reason"] = "portability_rejected_" + "_and_".join(failed_inputs)

    audit_frame = pd.DataFrame(audits)
    keep_mask = audit_frame["keep"].astype(bool).to_numpy()
    reduced = frame.loc[keep_mask].copy()
    reduced["index"] = range(1, len(reduced) + 1)
    _atomic_csv(reduced, output)
    if audit is not None:
        _atomic_csv(audit_frame, audit)
    return len(reduced), len(frame) - len(reduced)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--output-file", type=Path,
        help="default: <input stem>.reduced.csv next to the input",
    )
    parser.add_argument(
        "--audit-file", type=Path,
        help="optional CSV with logical features, reasons, and decisions",
    )
    parser.add_argument(
        "--profile", choices=PROFILES, default="balanced",
        help="reduction policy (default: balanced)",
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
    output = args.output_file or args.input_file.with_name(f"{args.input_file.stem}.reduced.csv")
    try:
        written, removed = reduce_file(
            args.input_file, output, audit=args.audit_file, profile=args.profile,
            require_calcite=not args.skip_calcite,
            require_postgres_ordering=not args.skip_postgres_ordering,
            constraint_mode=args.constraints,
            translate_queries=not args.preserve_query_text,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"{output}: {written} rows written, {removed} rows removed")
    if args.audit_file:
        print(f"{args.audit_file}: audit for {written + removed} rows written")


if __name__ == "__main__":
    main()
