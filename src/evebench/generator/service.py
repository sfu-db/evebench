from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Any

import sqlglot

from evebench.application import CheckService
from evebench.domain.models import CheckOptions, EquivalenceCase
from evebench.observability import get_logger, log_context

from .models import (
    GenerateAndEvaluateResult,
    GeneratedPair,
    PairEvaluation,
    QualityGateResult,
)
from .teacher import LLMTeacher, TeacherInputError, TeacherRuntimeError

logger = get_logger(__name__)


class GenerationUnavailableError(RuntimeError):
    pass


class GenerationValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GenerateRequest:
    schema_ddl: str
    dialect: str
    semantics: str
    num_pairs: int
    complexity: str
    checker_ids: list[str] | None
    options: CheckOptions
    teacher_options: dict[str, Any]
    include_rejected: bool


class GenerationService:
    def __init__(
        self,
        check_service: CheckService,
        teacher: LLMTeacher,
        *,
        max_concurrency: int = 2,
    ) -> None:
        self._check_service = check_service
        self._teacher = teacher
        self._max_concurrency = max(1, max_concurrency)

    async def generate_and_evaluate(self, request: GenerateRequest) -> GenerateAndEvaluateResult:
        if not self._teacher.is_available():
            raise GenerationUnavailableError(
                "LLM teacher is not configured; set EVEBENCH_LLMGEN_MODEL"
            )
        try:
            generated = await asyncio.to_thread(
                self._teacher.generate_pairs,
                schema_ddl=request.schema_ddl,
                dialect=request.dialect,
                num_pairs=request.num_pairs,
                complexity=request.complexity,
                seed=request.options.seed,
                timeout_ms=request.options.timeout_ms,
                options=request.teacher_options,
            )
        except TeacherInputError as exc:
            raise GenerationValidationError(str(exc)) from exc
        except TeacherRuntimeError as exc:
            raise GenerationUnavailableError(str(exc)) from exc

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def guarded(index: int, pair: GeneratedPair) -> PairEvaluation:
            async with semaphore:
                return await self._evaluate_one(index, pair, request)

        evaluations = list(
            await asyncio.gather(
                *(guarded(index + 1, pair) for index, pair in enumerate(generated))
            )
        )
        accepted = sum(1 for item in evaluations if item.quality_gate.accepted)
        rejected = len(evaluations) - accepted
        if not request.include_rejected:
            evaluations = [item for item in evaluations if item.quality_gate.accepted]
        return GenerateAndEvaluateResult(
            model=self._teacher.model,
            requested_pairs=request.num_pairs,
            generated_pairs=len(generated),
            accepted_pairs=accepted,
            rejected_pairs=rejected,
            evaluations=evaluations,
        )

    async def _evaluate_one(
        self,
        index: int,
        pair: GeneratedPair,
        request: GenerateRequest,
    ) -> PairEvaluation:
        with log_context(case_id=f"generated:{index}"):
            gate = await asyncio.to_thread(
                deterministic_quality_gate,
                schema_ddl=request.schema_ddl,
                dialect=request.dialect,
                pair=pair,
            )
            if not gate.accepted:
                logger.debug(
                    "generated pair rejected",
                    extra={
                        "event": "generator.pair_rejected",
                        "index": index,
                        "reason_code": gate.reason_code,
                    },
                )
                return PairEvaluation(
                    index=index,
                    pair=pair,
                    quality_gate=gate,
                    results=[],
                )

            case = EquivalenceCase(
                case_id=f"generated:{index}",
                query_left=pair.query_left,
                query_right=pair.query_right,
                schema_ddl=request.schema_ddl,
                dialect=request.dialect,
                semantics=request.semantics,
                parameters={
                    "source": "llm_teacher",
                    "complexity": request.complexity,
                },
            )
            results = await self._check_service.check(
                case,
                request.options,
                checker_ids=request.checker_ids,
            )
            logger.debug(
                "generated pair evaluated",
                extra={
                    "event": "generator.pair_evaluated",
                    "index": index,
                    "result_count": len(results),
                },
            )
            return PairEvaluation(
                index=index,
                pair=pair,
                quality_gate=gate,
                results=results,
            )


def deterministic_quality_gate(
    *,
    schema_ddl: str,
    dialect: str,
    pair: GeneratedPair,
) -> QualityGateResult:
    if pair.query_left.strip() == pair.query_right.strip():
        return QualityGateResult(
            accepted=False,
            reason_code="duplicate_pair",
            message="query_left and query_right are identical",
        )

    sqlglot_dialect = _sqlglot_dialect(dialect)
    try:
        left_sqlite = _to_sqlite_query(pair.query_left, sqlglot_dialect)
        right_sqlite = _to_sqlite_query(pair.query_right, sqlglot_dialect)
        schema_sqlite = _to_sqlite_schema(schema_ddl, sqlglot_dialect)
    except Exception as exc:
        return QualityGateResult(
            accepted=False,
            reason_code="invalid_sql",
            message=str(exc),
        )

    try:
        with sqlite3.connect(":memory:") as connection:
            connection.executescript(schema_sqlite)
            left_first = connection.execute(left_sqlite).fetchall()
            left_second = connection.execute(left_sqlite).fetchall()
            right_first = connection.execute(right_sqlite).fetchall()
            right_second = connection.execute(right_sqlite).fetchall()
    except sqlite3.Error as exc:
        return QualityGateResult(
            accepted=False,
            reason_code="execution_error",
            message=f"SQLite execution failed: {exc}",
        )

    if left_first != left_second or right_first != right_second:
        return QualityGateResult(
            accepted=False,
            reason_code="non_deterministic_query",
            message="Query execution is not deterministic across repeated runs",
        )
    return QualityGateResult(accepted=True)


def _to_sqlite_schema(schema_ddl: str, dialect: str | None) -> str:
    statements = sqlglot.transpile(schema_ddl, read=dialect, write="sqlite")
    retained = [statement.strip() for statement in statements if statement and statement.strip()]
    if not retained:
        raise ValueError("Schema did not produce executable statements")
    return ";\n".join(retained) + ";"


def _to_sqlite_query(query: str, dialect: str | None) -> str:
    text = query.strip()
    if not text:
        raise ValueError("Query must be non-empty")
    parsed = sqlglot.parse_one(text, read=dialect)
    if parsed is None:
        raise ValueError("Query is not valid SQL")
    root = text.lstrip().lower()
    if not (root.startswith("select") or root.startswith("with")):
        raise ValueError("Only SELECT queries are allowed")
    statements = sqlglot.transpile(text, read=dialect, write="sqlite")
    if len(statements) != 1:
        raise ValueError("Only a single SQL statement is allowed")
    statement = statements[0].strip()
    if not statement:
        raise ValueError("Query transpilation produced an empty statement")
    return statement


def _sqlglot_dialect(dialect: str) -> str | None:
    value = dialect.strip().lower()
    if value in {"postgres", "postgresql"}:
        return "postgres"
    if value in {"mysql", "sqlite", "duckdb"}:
        return value
    return None
