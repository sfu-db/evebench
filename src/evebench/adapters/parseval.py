from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields
from datetime import UTC, datetime

from parseval import disprove
from parseval.generator.config import GenerationConfig

from evebench.adapters.core import (
    AdapterInputError,
    duration_ms,
    error_result,
    normalize_adapter_input,
    reject_unknown_options,
)
from evebench.adapters.parseval_database import (
    cleanup_database,
    redact_connection,
    validate_connection_string,
)
from evebench.domain.models import (
    CheckOptions,
    CheckResult,
    CheckerDescriptor,
    CheckerRef,
    EquivalenceCase,
    ExecutionStatus,
    Verdict,
    json_safe,
)


@dataclass(frozen=True, slots=True)
class ParsevalConfig:
    backend_version: str
    adapter_version: str = "1"


@dataclass(frozen=True, slots=True)
class ParsevalInput:
    query_left: str
    query_right: str
    schema_ddl: str
    connection_string: str
    dialect: str
    semantics: str
    generation_config: GenerationConfig
    timeout_seconds: int


def parseval_descriptor(config: ParsevalConfig) -> CheckerDescriptor:
    return CheckerDescriptor(
        checker=CheckerRef("parseval", config.backend_version, config.adapter_version),
        display_name="ParSEval",
        supported_dialects=("sqlite", "mysql", "postgresql"),
        supported_semantics=("bag", "set"),
        execution_mode="process_pool",
    )


class ParsevalAdapter:
    """Complete ParSEval adapter. Instances live only in pool workers."""

    def __init__(self, config: ParsevalConfig) -> None:
        self._descriptor = parseval_descriptor(config)

    def preprocess(
        self,
        case: EquivalenceCase,
        options: CheckOptions,
    ) -> ParsevalInput:
        normalized = normalize_adapter_input(case, options, self._descriptor)
        backend_options = dict(options.for_backend("parseval"))
        connection_string = backend_options.pop("connection_string", None)
        reject_unknown_options(
            backend_options,
            allowed={item.name for item in fields(GenerationConfig)},
            display_name="ParSEval",
        )
        if (
            not isinstance(connection_string, str)
            or not connection_string.strip()
        ):
            raise AdapterInputError(
                ExecutionStatus.INVALID_INPUT,
                "missing_connection_string",
                "ParSEval requires backend_options.parseval.connection_string",
            )
        connection_string = connection_string.strip()
        connection_error = validate_connection_string(
            connection_string,
            normalized.dialect,
        )
        if connection_error is not None:
            raise AdapterInputError(
                ExecutionStatus.INVALID_INPUT,
                "invalid_connection_string",
                connection_error,
            )
        if options.seed is not None:
            backend_options["seed"] = options.seed
        _validate_generation_options(backend_options)
        try:
            generation_config = GenerationConfig(**backend_options)
        except (TypeError, ValueError) as exc:
            raise AdapterInputError(
                ExecutionStatus.INVALID_INPUT,
                "invalid_backend_options",
                str(exc),
            ) from exc
        return ParsevalInput(
            query_left=normalized.query_left,
            query_right=normalized.query_right,
            schema_ddl=normalized.schema_ddl,
            connection_string=connection_string,
            dialect=normalized.dialect,
            semantics=normalized.semantics,
            generation_config=generation_config,
            timeout_seconds=max(1, math.ceil(options.timeout_ms / 1000)),
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

        native_result = None
        backend_error: Exception | None = None
        cleanup_error: Exception | None = None
        try:
            native_result = disprove(
                prepared.query_left,
                prepared.query_right,
                prepared.schema_ddl,
                prepared.connection_string,
                prepared.dialect,
                semantics=prepared.semantics,
                generation_config=prepared.generation_config,
                timeout=prepared.timeout_seconds,
            )
        except Exception as exc:
            backend_error = exc
        finally:
            try:
                cleanup_database(prepared.connection_string)
            except Exception as exc:
                cleanup_error = exc

        if cleanup_error is not None:
            return error_result(
                self._descriptor,
                started,
                ExecutionStatus.FAILED,
                reason_code="cleanup_failed",
                message=redact_connection(
                    f"{type(cleanup_error).__name__}: {cleanup_error}",
                    prepared.connection_string,
                ),
            )
        if backend_error is not None:
            return error_result(
                self._descriptor,
                started,
                ExecutionStatus.FAILED,
                reason_code="backend_error",
                message=redact_connection(
                    f"{type(backend_error).__name__}: {backend_error}",
                    prepared.connection_string,
                ),
            )

        assert native_result is not None
        native = native_result.to_dict()
        native.pop("connection_string", None)
        finished = datetime.now(UTC)
        duration = duration_ms(started, finished)
        raw = redact_connection(
            json.dumps(native, default=str, separators=(",", ":")),
            prepared.connection_string,
        )
        native_verdict = native.get("verdict")
        if native_verdict in {"syntax_error", "runtime_error"}:
            status = (
                ExecutionStatus.INVALID_INPUT
                if native_verdict == "syntax_error"
                else ExecutionStatus.FAILED
            )
            return error_result(
                self._descriptor,
                started,
                status,
                reason_code=native_verdict,
                message=redact_connection(
                    native.get("error_msg") or f"ParSEval returned {native_verdict}",
                    prepared.connection_string,
                ),
                raw_output=raw,
                finished_at=finished,
            )
        if native_verdict == "timeout":
            return error_result(
                self._descriptor,
                started,
                ExecutionStatus.TIMEOUT,
                reason_code="backend_timeout",
                message=redact_connection(
                    native.get("error_msg") or "ParSEval timed out",
                    prepared.connection_string,
                ),
                raw_output=raw,
                finished_at=finished,
            )

        verdict = {
            "eq": Verdict.EQUIVALENT,
            "neq": Verdict.NOT_EQUIVALENT,
            "unknown": Verdict.UNKNOWN,
        }.get(native_verdict)
        if verdict is None:
            return error_result(
                self._descriptor,
                started,
                ExecutionStatus.FAILED,
                reason_code="unparseable_output",
                message=f"Unknown ParSEval verdict: {native_verdict!r}",
                raw_output=raw,
                finished_at=finished,
            )

        generation = native.get("generation") or {}
        return CheckResult(
            checker=self._descriptor.checker,
            execution_status=ExecutionStatus.COMPLETED,
            verdict=verdict,
            duration_ms=duration,
            started_at=started,
            finished_at=finished,
            message=(
                redact_connection(
                    native["error_msg"],
                    prepared.connection_string,
                )
                if native.get("error_msg")
                else None
            ),
            witness=(
                _counterexample(native)
                if verdict == Verdict.NOT_EQUIVALENT
                else None
            ),
            metrics={
                "rows_generated": generation.get("rows_generated"),
                "coverage": generation.get("coverage"),
                "solver_calls": generation.get("solver_calls"),
            },
            raw_output=raw,
        )


def _validate_generation_options(options: dict[str, object]) -> None:
    boolean_fields = {"bootstrap_negatives"}
    integer_fields = {
        item.name for item in fields(GenerationConfig)
    } - boolean_fields
    for name in boolean_fields & options.keys():
        if not isinstance(options[name], bool):
            raise AdapterInputError(
                ExecutionStatus.INVALID_INPUT,
                "invalid_backend_options",
                f"ParSEval {name} must be a boolean",
            )
    for name in integer_fields & options.keys():
        value = options[name]
        if name == "max_plan_depth" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise AdapterInputError(
                ExecutionStatus.INVALID_INPUT,
                "invalid_backend_options",
                f"ParSEval {name} must be an integer",
            )


def _counterexample(native: dict) -> dict | None:
    q1 = native.get("q1_result") or {}
    q2 = native.get("q2_result") or {}
    rows_left = q1.get("rows")
    rows_right = q2.get("rows")
    if rows_left is None and rows_right is None:
        return None
    return {
        "query_left_rows": json_safe(rows_left),
        "query_right_rows": json_safe(rows_right),
    }
