from __future__ import annotations

import pytest

from evebench.api.app import default_registry
from evebench.application import CheckService
from evebench.domain import CheckOptions, EquivalenceCase
from evebench.domain.models import ExecutionStatus, Verdict
from evebench.execution import BackendProcessPool

SQL_LEFT = "SELECT name FROM users WHERE age >= 25"
SQL_RIGHT = "SELECT name FROM users WHERE age > 26"
SCHEMA = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"


@pytest.mark.asyncio
async def test_proposed_case_runs_through_real_backends(tmp_path) -> None:
    pool = BackendProcessPool(max_workers=2, max_tasks=5)
    service = CheckService(default_registry(pool))
    parseval_database = tmp_path / "parseval.db"
    options = CheckOptions(
        timeout_ms=20_000,
        seed=7,
        backend_options={
            "parseval": {
                "connection_string": f"sqlite:///{parseval_database}",
            }
        },
    )
    try:
        [sqlsolver] = await service.check(
            EquivalenceCase(
                query_left=SQL_LEFT,
                query_right=SQL_RIGHT,
                schema_ddl=SCHEMA,
                dialect="mysql",
                semantics="bag",
            ),
            options,
            ["sqlsolver"],
        )
        [parseval] = await service.check(
            EquivalenceCase(
                query_left=SQL_LEFT,
                query_right=SQL_RIGHT,
                schema_ddl=SCHEMA,
                dialect="sqlite",
                semantics="bag",
            ),
            options,
            ["parseval"],
        )
        [spes] = await service.check(
            EquivalenceCase(
                query_left=SQL_LEFT,
                query_right=SQL_RIGHT,
                schema_ddl=SCHEMA,
                dialect="mysql",
                semantics="bag",
            ),
            options,
            ["spes"],
        )
        [verieql] = await service.check(
            EquivalenceCase(
                query_left=SQL_LEFT,
                query_right=SQL_RIGHT,
                schema_ddl=SCHEMA,
                dialect="mysql",
                semantics="bag",
            ),
            options,
            ["verieql"],
        )
        print(f"SQLSolver: {sqlsolver.execution_status}, {sqlsolver.verdict}")
        print(f"ParSEval: {parseval.execution_status}, {parseval.verdict}")
        print(f"SPES: {spes.execution_status}, {spes.verdict}")
        print(f"VeriEQL: {verieql.execution_status}, {verieql.verdict}")
    finally:
        pool.stop()

    assert sqlsolver.execution_status == ExecutionStatus.COMPLETED
    assert sqlsolver.verdict == Verdict.NOT_EQUIVALENT

    assert spes.execution_status == ExecutionStatus.COMPLETED
    assert spes.verdict == Verdict.UNKNOWN

    assert verieql.execution_status == ExecutionStatus.COMPLETED
    assert verieql.verdict == Verdict.NOT_EQUIVALENT
    assert verieql.witness is not None

    assert parseval.execution_status == ExecutionStatus.COMPLETED
    assert parseval.verdict == Verdict.NOT_EQUIVALENT
    assert parseval.witness is not None
    assert parseval.witness["query_left_rows"] != parseval.witness["query_right_rows"]
    assert not parseval_database.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("checker_id", ["sqlsolver", "spes", "verieql"])
async def test_postgres_integer_case_with_separate_primary_key(checker_id):
    pool = BackendProcessPool(max_workers=1, max_tasks=2)
    registry = default_registry(pool)
    descriptor = registry.get(checker_id).descriptor
    if not descriptor.available:
        pool.stop()
        pytest.skip(descriptor.unavailable_reason)
    try:
        [result] = await CheckService(registry).check(
            EquivalenceCase(
                query_left="SELECT id FROM t",
                query_right="SELECT id FROM t",
                schema_ddl="CREATE TABLE t (id INTEGER); ALTER TABLE t ADD PRIMARY KEY (id);",
                dialect="postgresql",
                semantics="bag",
            ),
            CheckOptions(timeout_ms=30_000),
            [checker_id],
        )
        assert result.execution_status == ExecutionStatus.COMPLETED, result
        assert result.verdict == Verdict.EQUIVALENT, result
    finally:
        pool.stop()


@pytest.mark.asyncio
async def test_sqlsolver_accepts_postgres_alter_table_only():
    pool = BackendProcessPool(max_workers=1, max_tasks=2)
    registry = default_registry(pool)
    if not registry.get("sqlsolver").descriptor.available:
        pool.stop()
        pytest.skip("SQLSolver JAR unavailable")
    try:
        [result] = await CheckService(registry).check(
            EquivalenceCase(
                query_left="SELECT id FROM t",
                query_right="SELECT id FROM t",
                schema_ddl="CREATE TABLE t (id INT); ALTER TABLE ONLY t ADD PRIMARY KEY (id);",
                dialect="postgresql",
                semantics="bag",
            ),
            CheckOptions(timeout_ms=30_000),
            ["sqlsolver"],
        )
        assert result.execution_status == ExecutionStatus.COMPLETED, result
        assert result.verdict == Verdict.EQUIVALENT, result
    finally:
        pool.stop()
