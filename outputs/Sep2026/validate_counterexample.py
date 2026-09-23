from collections import Counter
from decimal import Decimal
import math

import psycopg2
from sqlglot import exp, parse


VALIDATION_POLICY = "bag_without_shared_outer_limit_v1"


def prepare_validation_queries(q1, q2):
    queries = []
    limits = []
    for query in (q1, q2):
        statements = [statement for statement in parse(query, read="postgres") if statement]
        if len(statements) != 1 or not isinstance(statements[0], exp.Query):
            raise ValueError("Each query must be one SELECT query")
        statement = statements[0]
        if any(node.key in {"insert", "update", "delete", "create", "drop", "alter",
                            "altertable", "command", "into"}
               for node in statement.walk()):
            raise ValueError("Queries must not modify the database")
        queries.append(statement)
        limit = statement.args.get("limit")
        value = limit.expression if isinstance(limit, exp.Limit) else None
        limits.append(int(value.this) if isinstance(value, exp.Literal)
                      and not value.is_string and value.this.isdigit() else None)
    if limits[0] is None or limits[0] != limits[1]:
        return q1, q2, ""
    for query in queries:
        query.set("limit", None)
    return queries[0].sql(dialect="postgres"), queries[1].sql(dialect="postgres"), str(limits[0])


def parse_inserts(counterexample):
    if not isinstance(counterexample, str) or not counterexample.strip():
        raise ValueError("No counterexample INSERT statements supplied")
    statements = [statement for statement in parse(counterexample, read="postgres") if statement]
    if not statements or any(not isinstance(statement, exp.Insert) for statement in statements):
        raise ValueError("Counterexamples must contain only INSERT statements")
    forbidden = {"delete", "update", "create", "drop", "alter", "altertable", "command"}
    for statement in statements:
        if any(node.key in forbidden for node in statement.walk()):
            raise ValueError("Counterexample contains a forbidden operation")
    return statements


def freeze_value(value):
    if isinstance(value, (float, Decimal)) and math.isnan(value):
        return ("numeric_nan",)
    if isinstance(value, dict):
        return tuple(sorted((key, freeze_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, memoryview):
        return bytes(value)
    return value


def results_differ(q1_rows, q2_rows):
    return Counter(freeze_value(row) for row in q1_rows) != Counter(
        freeze_value(row) for row in q2_rows
    )


def validate_counterexample(dbid, q1, q2, counterexample, connection_options=None,
                            timeout_ms=15000, max_rows=100000):
    result = {
        "counterexample_valid": "N",
        "validation_status": "error",
        "validation_error": "",
        "q1_output": [],
        "q2_output": [],
        "q1_row_count": "",
        "q2_row_count": "",
        "result_comparison": "bag",
        "validation_policy": VALIDATION_POLICY,
        "shared_limit_removed": "",
        "q1_validation_sql": q1,
        "q2_validation_sql": q2,
    }
    connection = None
    stage = "insert_validation"
    try:
        parse_inserts(counterexample)
        stage = "query_validation"
        q1, q2, shared_limit = prepare_validation_queries(q1, q2)
        result.update(q1_validation_sql=q1, q2_validation_sql=q2,
                      shared_limit_removed=shared_limit)
        options = dict(connection_options or {})
        options["dbname"] = f"{dbid}_ce_test"
        options.setdefault("connect_timeout", 10)
        stage = "connect"
        connection = psycopg2.connect(**options)
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != f"{dbid}_ce_test":
                raise ValueError("Refusing to validate outside the requested test database")
            cursor.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
            cursor.execute("SELECT set_config('lock_timeout', '5000', true)")
            stage = "insert"
            cursor.execute(counterexample)
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            outputs = []
            for name, query in (("q1", q1), ("q2", q2)):
                stage = name
                cursor.execute(query)
                rows = cursor.fetchmany(max_rows + 1)
                if len(rows) > max_rows:
                    raise ValueError(f"{name} exceeds the {max_rows}-row comparison limit")
                outputs.append(rows)
                result[f"{name}_output"] = rows[:100]
                result[f"{name}_row_count"] = len(rows)
            valid = results_differ(*outputs)
            result["counterexample_valid"] = "Y" if valid else "N"
            result["validation_status"] = "valid" if valid else "same_output"
    except Exception as error:
        result["validation_error"] = f"{stage}: {type(error).__name__}: {error}"
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()
    return result