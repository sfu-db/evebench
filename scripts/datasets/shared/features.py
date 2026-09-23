"""Dialect-aware query features and pair complexity policies."""
from collections import Counter
from dataclasses import asdict, dataclass

from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
import sqlglot

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


def _skeleton(tree: exp.Expression, *, erase_identifiers: bool, dialect: str = "sqlite") -> str:
    """Return normalized SQL with literal or all leaf values erased."""

    def replace(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Literal):
            return exp.Literal.string("__literal__")
        if erase_identifiers and isinstance(node, exp.Identifier):
            return exp.Identifier(this="__identifier__", quoted=True)
        return node

    return tree.transform(replace, copy=True).sql(dialect=dialect)


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
        _skeleton(tree, erase_identifiers=False, dialect=dialect),
        _skeleton(tree, erase_identifiers=True, dialect=dialect),
    )


def is_complex(features: QueryFeatures) -> bool:
    """Whether a query meets the complex-query feature criteria."""
    return bool(
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


def analyze_pair(q1: str, q2: str, *, dialect: str = "sqlite") -> dict[str, object]:
    """Return pair-aware logical diagnostics for an audit row."""
    first, first_literals, first_structure = query_features(q1, dialect=dialect)
    second, second_literals, second_structure = query_features(q2, dialect=dialect)
    result: dict[str, object] = {
        "q1_is_complex": is_complex(first),
        "q2_is_complex": is_complex(second),
        "literal_only_change": first_literals == second_literals,
        "same_structure": first_structure == second_structure,
    }
    result.update({f"q1_{key}": value for key, value in asdict(first).items()})
    result.update({f"q2_{key}": value for key, value in asdict(second).items()})
    return result


def pair_is_too_simple(details: dict[str, object]) -> tuple[bool, str]:
    """Keep only pairs with complex logic on both sides."""
    remove = not (details["q1_is_complex"] and details["q2_is_complex"])
    reason = "pair_not_complex_on_both_sides" if remove else "both_queries_have_complex_logic"
    return remove, reason
