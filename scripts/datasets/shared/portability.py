"""Temporary SQL renderings and schema checks for portability audits."""
from sqlglot import exp
import sqlglot

CONSTRAINT_MODES = ("preserve", "foreign-keys", "all")


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


