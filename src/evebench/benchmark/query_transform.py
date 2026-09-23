"""Optional SQL transformations applied to benchmark query pairs."""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def strip_shared_order_by_limit(
    query_left: str, query_right: str, dialect: str
) -> tuple[str, str]:
    """Remove shared top-level limits and offsets, plus matching orders."""
    read_dialect = {"postgresql": "postgres", "mariadb": "mysql"}.get(
        dialect, dialect
    )
    
    left = sqlglot.parse_one(query_left, read=read_dialect)
    right = sqlglot.parse_one(query_right, read=read_dialect)
        
    left_order, right_order = left.args.get("order"), right.args.get("order")
    left_limit, right_limit = left.args.get("limit"), right.args.get("limit")
    left_offset, right_offset = left.args.get("offset"), right.args.get("offset")
    if not (left_order and right_order and left_limit and right_limit):
        return query_left, query_right
    if left_limit != right_limit or left_offset != right_offset:
        return query_left, query_right

    left.set("limit", None)
    right.set("limit", None)
    left.set("offset", None)
    right.set("offset", None)
    if left_order == right_order:
        left.set("order", None)
        right.set("order", None)
    return left.sql(dialect=read_dialect), right.sql(dialect=read_dialect)
