from __future__ import annotations

from evebench.generator.models import GeneratedPair
from evebench.generator.service import deterministic_quality_gate


def test_quality_gate_accepts_executable_select_pairs() -> None:
    gate = deterministic_quality_gate(
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="sqlite",
        pair=GeneratedPair(
            query_left="SELECT a FROM t",
            query_right="SELECT a FROM t WHERE 1 = 1",
        ),
    )

    assert gate.accepted is True
    assert gate.reason_code is None


def test_quality_gate_rejects_duplicate_pairs() -> None:
    gate = deterministic_quality_gate(
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="sqlite",
        pair=GeneratedPair(
            query_left="SELECT a FROM t",
            query_right="SELECT a FROM t",
        ),
    )

    assert gate.accepted is False
    assert gate.reason_code == "duplicate_pair"


def test_quality_gate_rejects_invalid_sql() -> None:
    gate = deterministic_quality_gate(
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="sqlite",
        pair=GeneratedPair(
            query_left="SELECT FROM",
            query_right="SELECT a FROM t",
        ),
    )

    assert gate.accepted is False
    assert gate.reason_code == "invalid_sql"
