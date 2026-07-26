from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio

from evebench.adapters import CheckerRegistry
from evebench.api import create_app
from evebench.benchmark import ResultStore
from evebench.domain import (
    CheckOptions,
    CheckResult,
    CheckerDescriptor,
    CheckerRef,
    EquivalenceCase,
    ExecutionStatus,
    Verdict,
)


class APIStubChecker:
    descriptor = CheckerDescriptor(
        checker=CheckerRef("stub", "1.0", "1"),
        display_name="Stub",
        supported_dialects=("sqlite",),
        supported_semantics=("bag",),
        execution_mode="in_process",
    )

    async def check(
        self, case: EquivalenceCase, options: CheckOptions
    ) -> CheckResult:
        now = datetime.now(UTC)
        return CheckResult(
            checker=self.descriptor.checker,
            execution_status=ExecutionStatus.COMPLETED,
            verdict=Verdict.EQUIVALENT,
            duration_ms=1,
            started_at=now,
            finished_at=now,
        )


@pytest_asyncio.fixture
async def client(tmp_path):
    app = create_app(
        CheckerRegistry((APIStubChecker(),)),
        result_store=ResultStore(tmp_path / "results.sqlite"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as value:
        yield value


@pytest.mark.asyncio
async def test_lists_checker_capabilities(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/checkers")

    assert response.status_code == 200
    assert response.json()[0]["checker"]["checker_id"] == "stub"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", "SQL equivalence workbench"),
        ("/leaderboard", "Leaderboard"),
        ("/assets/styles.css", "--blue"),
    ],
)
async def test_serves_web_interface(
    client: httpx.AsyncClient,
    path: str,
    expected: str,
) -> None:
    response = await client.get(path)

    assert response.status_code == 200
    assert expected in response.text


@pytest.mark.asyncio
async def test_frontend_identifies_backends_independently_of_option_inputs(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/assets/app.js")

    assert response.status_code == 200
    assert 'querySelectorAll("input[data-checker-id]:checked")' in response.text
    assert ".backend-check input:checked" not in response.text


@pytest.mark.asyncio
async def test_runs_selected_checker(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/checks",
        json={
            "case": {
                "query_left": "SELECT a FROM t",
                "query_right": "SELECT a FROM t",
                "schema_ddl": "CREATE TABLE t (a INT);",
                "dialect": "SQLITE",
                "semantics": "bag",
            },
            "checker_ids": ["stub"],
        },
    )

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["execution_status"] == "completed"
    assert result["verdict"] == "equivalent"


@pytest.mark.asyncio
async def test_unknown_checker_is_request_error(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/checks",
        json={
            "case": {
                "query_left": "SELECT 1",
                "query_right": "SELECT 1",
                "schema_ddl": "CREATE TABLE t (a INT);",
                "dialect": "sqlite",
            },
            "checker_ids": ["missing"],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unknown_checker"


@pytest.mark.asyncio
async def test_memory_limit_is_rejected_until_enforced(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/v1/checks",
        json={
            "case": {
                "query_left": "SELECT 1",
                "query_right": "SELECT 1",
                "schema_ddl": "CREATE TABLE t (a INT);",
                "dialect": "sqlite",
            },
            "options": {"memory_limit_mb": 512},
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_benchmark_summary_is_empty_before_a_run(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/v1/benchmarks/summary")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_request_id_is_preserved_or_safely_replaced(
    client: httpx.AsyncClient,
) -> None:
    valid = await client.get("/healthz", headers={"x-request-id": "request-123"})
    invalid = await client.get("/healthz", headers={"x-request-id": "bad id"})

    assert valid.headers["x-request-id"] == "request-123"
    assert invalid.headers["x-request-id"] != "bad id"
    assert len(invalid.headers["x-request-id"]) == 32
