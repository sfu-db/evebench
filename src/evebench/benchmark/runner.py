from __future__ import annotations

import asyncio
import csv
import hashlib
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evebench.application import CheckService
from evebench.domain.models import (
    JSONValue,
    CheckOptions,
    CheckResult,
    EquivalenceCase,
)
from evebench.observability import get_logger, log_context

from .store import ResultStore

ProgressCallback = Callable[[int, int, int], None]
logger = get_logger(__name__)


class BenchmarkRunner:
    def __init__(self, service: CheckService, store: ResultStore) -> None:
        self.service = service
        self.store = store

    async def run_csv(
        self,
        dataset_path: str | Path,
        *,
        dataset_name: str | None = None,
        checker_ids: list[str] | None = None,
        timeout_ms: int = 30_000,
        semantics: str = "bag",
        seed: int | None = 1,
        backend_dialects: dict[str, str] | None = None,
        backend_options: dict[str, dict[str, JSONValue]] | None = None,
        execution_options: dict[str, JSONValue] | None = None,
        limit: int | None = None,
        run_id: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> int:
        path = Path(dataset_path).resolve()
        rows = _read_rows(path)
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            rows = rows[:limit]
        selected = checker_ids or [
            checker.descriptor.checker.checker_id
            for checker in self.service.registry.all()
            if checker.descriptor.available
        ]
        selected = list(dict.fromkeys(selected))
        unknown = [item for item in selected if item not in self.service.registry.ids()]
        if unknown:
            raise ValueError(f"unknown checker(s): {', '.join(unknown)}")
        if semantics not in {"bag", "set"}:
            raise ValueError("semantics must be 'bag' or 'set'")
        configured_dialects = backend_dialects or {}
        configured_options = backend_options or {}
        unused_dialects = sorted(set(configured_dialects) - set(selected))
        unused_options = sorted(set(configured_options) - set(selected))
        if unused_dialects or unused_options:
            unused = sorted(set(unused_dialects + unused_options))
            raise ValueError(
                "configuration supplied for unselected checker(s): "
                + ", ".join(unused)
            )
        dialects: dict[str, str] = {}
        for checker_id in selected:
            checker = self.service.registry.get(checker_id)
            dialect = configured_dialects.get(checker_id)
            if dialect is None:
                dialect = (
                    "sqlite"
                    if "sqlite" in checker.descriptor.supported_dialects
                    else checker.descriptor.supported_dialects[0]
                )
            dialect = dialect.lower()
            if dialect not in checker.descriptor.supported_dialects:
                raise ValueError(
                    f"{checker_id} does not support dialect {dialect!r}"
                )
            dialects[checker_id] = dialect

        configuration: dict[str, Any] = {
            "checker_ids": selected,
            "semantics": semantics,
            "seed": seed,
            "backend_dialects": dialects,
            "backend_options": configured_options,
            "execution_options": execution_options or {},
        }

        dataset_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if run_id is None:
            run_id = self.store.create_run(
                dataset=dataset_name or path.stem,
                dataset_path=str(path),
                dataset_sha256=dataset_sha256,
                total_cases=len(rows),
                timeout_ms=timeout_ms,
                configuration=configuration,
                started_at=datetime.now(UTC).isoformat(),
            )
            logger.info(
                "benchmark run created",
                extra={
                    "event": "benchmark.created",
                    "run_id": run_id,
                    "dataset": dataset_name or path.stem,
                    "dataset_path": str(path),
                    "total_cases": len(rows),
                    "checker_ids": selected,
                    "timeout_ms": timeout_ms,
                },
            )
        else:
            self.store.validate_run(
                run_id,
                dataset_sha256=dataset_sha256,
                total_cases=len(rows),
                timeout_ms=timeout_ms,
                configuration=configuration,
            )
            logger.info(
                "benchmark run resumed",
                extra={
                    "event": "benchmark.resumed",
                    "run_id": run_id,
                    "total_cases": len(rows),
                    "checker_ids": selected,
                },
            )

        terminal_status = "completed"
        with log_context(run_id=run_id):
            try:
                with tempfile.TemporaryDirectory(
                    prefix="evebench-parseval-"
                ) as temp:
                    for position, row in enumerate(rows, start=1):
                        case_id = f"{row['dbid']}:{row['index']}"
                        with log_context(case_id=case_id):
                            completed = self.store.completed_checker_ids(
                                run_id, case_id
                            )
                            pending = [
                                item for item in selected if item not in completed
                            ]
                            if not pending:
                                logger.debug(
                                    "benchmark case already complete",
                                    extra={
                                        "event": "benchmark.case_skipped",
                                        "dataset_row": position,
                                    },
                                )
                                if progress:
                                    progress(position, len(rows), run_id)
                                continue

                            base_case = EquivalenceCase(
                                query_left=row["q1"],
                                query_right=row["q2"],
                                schema_ddl=_schema_ddl(row),
                                dialect="sqlite",
                                semantics=semantics,
                                case_id=case_id,
                            )
                            tasks = [
                                self._run_checker(
                                    checker_id,
                                    base_case,
                                    timeout_ms,
                                    Path(temp)
                                    / f"{position}-{checker_id}.sqlite",
                                    dialect=dialects[checker_id],
                                    seed=seed,
                                    backend_options=configured_options.get(
                                        checker_id, {}
                                    ),
                                )
                                for checker_id in pending
                            ]
                            checked = await asyncio.gather(*tasks)
                            results = [
                                (
                                    self.service.registry.get(
                                        checker_id
                                    ).descriptor.display_name,
                                    result,
                                )
                                for checker_id, result in zip(
                                    pending, checked, strict=True
                                )
                            ]
                            self.store.save_results(
                                run_id=run_id,
                                case=base_case,
                                dataset_row=position,
                                db_id=row.get("dbid"),
                                ground_truth=row.get("ground_truth"),
                                results=results,
                            )
                            logger.debug(
                                "benchmark case persisted",
                                extra={
                                    "event": "benchmark.case_persisted",
                                    "dataset_row": position,
                                    "result_count": len(results),
                                },
                            )
                            if progress:
                                progress(position, len(rows), run_id)
            except (KeyboardInterrupt, asyncio.CancelledError):
                terminal_status = "interrupted"
                logger.warning(
                    "benchmark run interrupted",
                    extra={"event": "benchmark.interrupted"},
                )
                raise
            except Exception:
                terminal_status = "failed"
                logger.exception(
                    "benchmark run failed",
                    extra={"event": "benchmark.failed"},
                )
                raise
            finally:
                self.store.finish_run(
                    run_id,
                    status=terminal_status,
                    finished_at=datetime.now(UTC).isoformat(),
                )
                logger.info(
                    "benchmark run finished",
                    extra={
                        "event": "benchmark.finished",
                        "status": terminal_status,
                    },
                )
        return run_id

    async def _run_checker(
        self,
        checker_id: str,
        base_case: EquivalenceCase,
        timeout_ms: int,
        parseval_database: Path,
        *,
        dialect: str,
        seed: int | None,
        backend_options: dict[str, JSONValue],
    ) -> CheckResult:
        case = EquivalenceCase(
            query_left=base_case.query_left,
            query_right=base_case.query_right,
            schema_ddl=base_case.schema_ddl,
            dialect=dialect,
            semantics=base_case.semantics,
            case_id=base_case.case_id,
            parameters=base_case.parameters,
        )
        selected_options = dict(backend_options)
        if checker_id == "parseval":
            selected_options["connection_string"] = (
                f"sqlite:///{parseval_database}"
            )
        results = await self.service.check(
            case,
            CheckOptions(
                timeout_ms=timeout_ms,
                seed=seed,
                backend_options={checker_id: selected_options},
            ),
            [checker_id],
        )
        return results[0]


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"dbid", "index", "q1", "q2"}
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"dataset is missing column(s): {', '.join(missing)}")
        if not {"schema_ddl", "clean_ddl"} & set(reader.fieldnames or ()):
            raise ValueError(
                "dataset requires a schema_ddl or clean_ddl column"
            )
        rows = list(reader)
    for position, row in enumerate(rows, start=1):
        if not _schema_ddl(row).strip():
            raise ValueError(f"dataset row {position} has empty schema DDL")
    return rows


def _schema_ddl(row: dict[str, str]) -> str:
    return row.get("schema_ddl") or row.get("clean_ddl") or ""
