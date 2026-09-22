import pytest

from evebench.benchmark.query_transform import strip_shared_order_by_limit


def test_strips_matching_top_level_clauses_for_postgresql():
    left, right = strip_shared_order_by_limit(
        "SELECT a FROM t ORDER BY a DESC LIMIT 2",
        "SELECT b FROM t ORDER BY a DESC LIMIT 2",
        "postgresql",
    )
    assert left == "SELECT a FROM t"
    assert right == "SELECT b FROM t"


def test_strips_matching_limits_and_keeps_different_orders():
    left, right = strip_shared_order_by_limit(
        "SELECT a FROM t ORDER BY a LIMIT 2",
        "SELECT b FROM t ORDER BY b LIMIT 2",
        "sqlite",
    )
    assert left == "SELECT a FROM t ORDER BY a"
    assert right == "SELECT b FROM t ORDER BY b"


@pytest.mark.parametrize(
    "left_order,right_order,expected_left,expected_right",
    [
        ("a", "a", "SELECT a FROM t", "SELECT b FROM t"),
        ("a", "b", "SELECT a FROM t ORDER BY a", "SELECT b FROM t ORDER BY b"),
    ],
)
def test_strips_matching_offsets_with_limits(
    left_order, right_order, expected_left, expected_right
):
    left, right = strip_shared_order_by_limit(
        f"SELECT a FROM t ORDER BY {left_order} LIMIT 2 OFFSET 1",
        f"SELECT b FROM t ORDER BY {right_order} LIMIT 2 OFFSET 1",
        "sqlite",
    )
    assert (left, right) == (expected_left, expected_right)


@pytest.mark.parametrize(
    "left,right",
    [
        ("SELECT a FROM t ORDER BY a LIMIT 2", "SELECT b FROM t ORDER BY a LIMIT 3"),
        ("SELECT a FROM t ORDER BY a LIMIT 2", "SELECT b FROM t ORDER BY a"),
        ("SELECT a FROM t ORDER BY a LIMIT 2 OFFSET 1", "SELECT b FROM t ORDER BY a LIMIT 2 OFFSET 2"),
        ("SELECT a FROM t ORDER BY a LIMIT 2", "SELECT b FROM t ORDER BY a LIMIT 2 OFFSET 1"),
        ("SELECT a FROM t ORDER BY a LIMIT 2", "SELECT b FROM t ORDER BY a LIMIT"),
    ],
)
def test_keeps_queries_when_endings_do_not_match(left, right):
    assert strip_shared_order_by_limit(left, right, "sqlite") == (left, right)


def test_keeps_nested_order_and_limit():
    left = "SELECT * FROM (SELECT a FROM t ORDER BY a LIMIT 2) AS x"
    right = "SELECT * FROM (SELECT b FROM t ORDER BY a LIMIT 2) AS x"
    assert strip_shared_order_by_limit(left, right, "sqlite") == (left, right)
