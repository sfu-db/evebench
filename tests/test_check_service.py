from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evebench.adapters import CheckerRegistry
from evebench.application import CheckService
from evebench.application.check_service import UnknownCheckerError
from evebench.domain import (
    CheckOptions,
    CheckResult,
    CheckerDescriptor,
    CheckerRef,
    EquivalenceCase,
    ExecutionStatus,
    Verdict,
)


class FakeChecker:
    def __init__(
        self,
        checker_id: str,
        *,
        supported: bool = True,
        raises: bool = False,
    ) -> None:
        self._supported = supported
        self._raises = raises
        self._descriptor = CheckerDescriptor(
            checker=CheckerRef(checker_id, "test", "1"),
            display_name=checker_id,
            supported_dialects=("sqlite",),
            supported_semantics=("bag",),
            execution_mode="in_process",
        )

    @property
    def descriptor(self) -> CheckerDescriptor:
        return self._descriptor

    async def check(
        self, case: EquivalenceCase, options: CheckOptions
    ) -> CheckResult:
        if self._raises:
            raise RuntimeError("fake failure")
        now = datetime.now(UTC)
        if not self._supported:
            return CheckResult(
                checker=self.descriptor.checker,
                execution_status=ExecutionStatus.UNSUPPORTED,
                verdict=None,
                duration_ms=0,
                started_at=now,
                finished_at=now,
                reason_code="unsupported_test",
                message="not supported by fake",
            )
        return CheckResult(
            checker=self.descriptor.checker,
            execution_status=ExecutionStatus.COMPLETED,
            verdict=Verdict.EQUIVALENT,
            duration_ms=1,
            started_at=now,
            finished_at=now,
        )


@pytest.fixture
def case() -> EquivalenceCase:
    return EquivalenceCase(
        query_left="SELECT a FROM t",
        query_right="SELECT a FROM t",
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="sqlite",
        semantics="bag",
    )


@pytest.mark.asyncio
async def test_service_isolates_failure_and_unsupported_results(
    case: EquivalenceCase,
) -> None:
    service = CheckService(
        CheckerRegistry(
            (
                FakeChecker("ok"),
                FakeChecker("unsupported", supported=False),
                FakeChecker("broken", raises=True),
            )
        )
    )

    results = await service.check(case, CheckOptions())

    assert [result.execution_status for result in results] == [
        ExecutionStatus.COMPLETED,
        ExecutionStatus.UNSUPPORTED,
        ExecutionStatus.FAILED,
    ]
    assert results[1].reason_code == "unsupported_test"
    assert results[2].reason_code == "adapter_error"


@pytest.mark.asyncio
async def test_service_rejects_unknown_checker(case: EquivalenceCase) -> None:
    service = CheckService(CheckerRegistry((FakeChecker("known"),)))

    with pytest.raises(UnknownCheckerError) as error:
        await service.check(case, CheckOptions(), ["missing"])

    assert error.value.checker_ids == ["missing"]
