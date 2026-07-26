from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evebench.adapters.core import (
    AdapterInputError,
    duration_ms,
    error_result,
    parse_last_json_object,
)
from evebench.adapters.schema import preprocess_calcite_case
from evebench.domain.models import (
    CheckOptions,
    CheckResult,
    CheckerDescriptor,
    CheckerRef,
    EquivalenceCase,
    ExecutionStatus,
    Verdict,
)


@dataclass(frozen=True, slots=True)
class SPESConfig:
    jar_path: str
    library_path: str
    working_directory: str
    java_command: str = "java"
    backend_version: str = "1.0"
    adapter_version: str = "1"


@dataclass(frozen=True, slots=True)
class SPESInput:
    query_left: str
    query_right: str
    schema_ddl: str
    timeout_seconds: float


def spes_descriptor(config: SPESConfig) -> CheckerDescriptor:
    jar_path = Path(config.jar_path)
    available = jar_path.is_file()
    return CheckerDescriptor(
        checker=CheckerRef("spes", config.backend_version, config.adapter_version),
        display_name="SPES",
        supported_dialects=("calcite",),
        supported_semantics=("bag",),
        execution_mode="process_pool",
        available=available,
        unavailable_reason=None if available else f"JAR not found: {jar_path}",
    )


class SPESAdapter:
    """SPES adapter backed by its executable fat JAR."""

    def __init__(self, config: SPESConfig) -> None:
        self._config = config
        self._descriptor = spes_descriptor(config)

    def preprocess(
        self,
        case: EquivalenceCase,
        options: CheckOptions,
    ) -> SPESInput:
        normalized = preprocess_calcite_case(
            case,
            options,
            self._descriptor,
        )
        return SPESInput(
            query_left=normalized.query_left,
            query_right=normalized.query_right,
            schema_ddl=normalized.schema_ddl,
            timeout_seconds=max(0.001, options.timeout_ms * 0.95 / 1000),
        )

    def check(
        self,
        case: EquivalenceCase,
        options: CheckOptions,
    ) -> CheckResult:
        started = datetime.now(UTC)
        try:
            prepared = self.preprocess(case, options)
        except AdapterInputError as exc:
            return error_result(
                self._descriptor,
                started,
                exc.status,
                reason_code=exc.reason_code,
                message=str(exc),
            )

        try:
            completed = self._invoke(prepared)
        except subprocess.TimeoutExpired:
            return error_result(
                self._descriptor,
                started,
                ExecutionStatus.TIMEOUT,
                reason_code="backend_timeout",
                message="SPES exceeded its subprocess deadline",
            )
        except Exception as exc:
            return error_result(
                self._descriptor,
                started,
                ExecutionStatus.FAILED,
                reason_code="backend_error",
                message=f"{type(exc).__name__}: {exc}",
            )

        raw = completed.stdout.strip()
        if completed.returncode != 0:
            message = (
                completed.stderr.strip()
                or f"SPES exited with code {completed.returncode}"
            )
            return error_result(
                self._descriptor,
                started,
                ExecutionStatus.FAILED,
                reason_code="backend_error",
                message=message,
                raw_output=raw or None,
            )

        try:
            native = parse_last_json_object(raw)
        except ValueError as exc:
            return error_result(
                self._descriptor,
                started,
                ExecutionStatus.FAILED,
                reason_code="unparseable_output",
                message=f"Invalid SPES output: {exc}",
                raw_output=raw or None,
            )

        decision = native.get("decision")
        verdict = {
            "true": Verdict.EQUIVALENT,
            "false": Verdict.NOT_EQUIVALENT,
            "unknown": Verdict.UNKNOWN,
        }.get(decision)
        if verdict is None:
            return error_result(
                self._descriptor,
                started,
                ExecutionStatus.FAILED,
                reason_code="unparseable_output",
                message=f"Unknown SPES decision: {decision!r}",
                raw_output=raw,
            )

        finished = datetime.now(UTC)
        return CheckResult(
            checker=self._descriptor.checker,
            execution_status=ExecutionStatus.COMPLETED,
            verdict=verdict,
            duration_ms=duration_ms(started, finished),
            started_at=started,
            finished_at=finished,
            reason_code=(
                "backend_unknown" if verdict == Verdict.UNKNOWN else None
            ),
            message=(
                str(native.get("reason"))
                if verdict == Verdict.UNKNOWN and native.get("reason")
                else None
            ),
            raw_output=raw,
        )

    def _invoke(
        self,
        prepared: SPESInput,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        current_library_path = environment.get("LD_LIBRARY_PATH")
        environment["LD_LIBRARY_PATH"] = (
            self._config.library_path
            if not current_library_path
            else self._config.library_path + os.pathsep + current_library_path
        )
        command = [
            self._config.java_command,
            f"-Djava.library.path={self._config.library_path}",
            "-jar",
            self._config.jar_path,
            "-sql1",
            prepared.query_left,
            "-sql2",
            prepared.query_right,
            "-schema",
            prepared.schema_ddl,
        ]
        return subprocess.run(
            command,
            cwd=self._config.working_directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=prepared.timeout_seconds,
            check=False,
        )
