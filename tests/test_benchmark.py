from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from evebench.adapters import CheckerRegistry
from evebench.application import CheckService
from evebench.benchmark import BenchmarkConfig, BenchmarkRunner, ResultStore
from evebench.domain import (
    CheckOptions,
    CheckResult,
    CheckerDescriptor,
    CheckerRef,
    EquivalenceCase,
    ExecutionStatus,
    Verdict,
)


class BenchmarkStub:
    def __init__(
        self,
        checker_id: str,
        verdict: Verdict | None,
        status: ExecutionStatus = ExecutionStatus.COMPLETED,
    ) -> None:
        self._verdict = verdict
        self._status = status
        self.descriptor = CheckerDescriptor(
            checker=CheckerRef(checker_id, "test", "1"),
            display_name=checker_id.upper(),
            supported_dialects=("sqlite",),
            supported_semantics=("bag",),
            execution_mode="in_process",
        )

    async def check(self, case: EquivalenceCase, options: CheckOptions) -> CheckResult:
        now = datetime.now(UTC)
        return CheckResult(
            checker=self.descriptor.checker,
            execution_status=self._status,
            verdict=self._verdict,
            duration_ms=10,
            started_at=now,
            finished_at=now,
        )


class CapturingBenchmarkStub(BenchmarkStub):
    def __init__(self) -> None:
        super().__init__("capture", Verdict.EQUIVALENT)
        self.descriptor = CheckerDescriptor(
            checker=CheckerRef("capture", "test", "1"),
            display_name="CAPTURE",
            supported_dialects=("sqlite", "mysql"),
            supported_semantics=("bag", "set"),
            execution_mode="in_process",
        )
        self.captured = None

    async def check(self, case: EquivalenceCase, options: CheckOptions) -> CheckResult:
        self.captured = (case, options)
        return await super().check(case, options)


def write_dataset(path) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "dbid",
                "index",
                "q1",
                "q2",
                "schema_ddl",
                "dialect",
                "ground_truth",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "dbid": "db",
                "index": "1",
                "q1": "SELECT a FROM t",
                "q2": "SELECT a FROM t",
                "schema_ddl": "CREATE TABLE t (a INT);",
                "dialect": "sqlite",
                "ground_truth": "EQ",
            }
        )


@pytest.mark.asyncio
async def test_runner_persists_four_way_ratios(tmp_path) -> None:
    dataset = tmp_path / "cases.csv"
    write_dataset(dataset)
    store = ResultStore(tmp_path / "results.sqlite")
    registry = CheckerRegistry(
        (
            BenchmarkStub("eq", Verdict.EQUIVALENT),
            BenchmarkStub("neq", Verdict.NOT_EQUIVALENT),
            BenchmarkStub("unknown", None, ExecutionStatus.FAILED),
            BenchmarkStub("unsupported", None, ExecutionStatus.UNSUPPORTED),
            BenchmarkStub("timeout", None, ExecutionStatus.TIMEOUT),
        )
    )

    run_id = await BenchmarkRunner(CheckService(registry), store).run_csv(dataset)

    summaries = {row.checker_id: row for row in store.summaries(run_id=run_id)}
    assert summaries["eq"].eq_ratio == 1
    assert summaries["neq"].neq_ratio == 1
    assert summaries["unknown"].unknown_ratio == 1
    assert summaries["unsupported"].unsupported_ratio == 1
    assert store.unsupported_reasons(run_id=run_id) == [
        {
            "checker_id": "unsupported",
            "display_name": "UNSUPPORTED",
            "reason_code": "unspecified",
            "count": 1,
        }
    ]
    assert summaries["timeout"].timeout_ratio == 1
    assert all(row.evaluated == 1 for row in summaries.values())
    assert all(row.run_status == "completed" for row in summaries.values())


@pytest.mark.asyncio
async def test_runner_resumes_without_repeating_saved_results(tmp_path) -> None:
    dataset = tmp_path / "cases.csv"
    write_dataset(dataset)
    store = ResultStore(tmp_path / "results.sqlite")
    checker = BenchmarkStub("eq", Verdict.EQUIVALENT)
    runner = BenchmarkRunner(CheckService(CheckerRegistry((checker,))), store)

    run_id = await runner.run_csv(dataset)
    resumed_id = await runner.run_csv(dataset, run_id=run_id)

    assert resumed_id == run_id
    assert store.summaries(run_id=run_id)[0].evaluated == 1

    with pytest.raises(ValueError, match="different timeout"):
        await runner.run_csv(dataset, run_id=run_id, timeout_ms=1_000)


def test_loads_bird_benchmark_profile() -> None:
    config = BenchmarkConfig.load("configs/benchmarks/bird_baseline.toml")

    assert config.name == "bird_baseline"
    assert config.dataset_path.name == "bird_benchmark.csv"
    assert config.checker_ids == (
        "sqlsolver",
        "spes",
        "verieql",
        "parseval",
    )
    assert config.backends["verieql"].options == {
        "bound_size": 2,
    }
    assert config.strip_shared_order_by_limit is False


def test_loads_postgres_gemini_transform_option() -> None:
    config = BenchmarkConfig.load("configs/benchmarks/postgres_gemini.toml")
    assert config.strip_shared_order_by_limit is True


@pytest.mark.asyncio
async def test_runner_applies_configured_dialect_seed_and_options(
    tmp_path,
) -> None:
    dataset = tmp_path / "cases.csv"
    write_dataset(dataset)
    checker = CapturingBenchmarkStub()
    runner = BenchmarkRunner(
        CheckService(CheckerRegistry((checker,))),
        ResultStore(tmp_path / "results.sqlite"),
    )

    await runner.run_csv(
        dataset,
        checker_ids=["capture"],
        semantics="set",
        seed=99,
        backend_options={"capture": {"custom": "value"}},
    )

    assert checker.captured is not None
    case, options = checker.captured
    assert case.dialect == "sqlite"
    assert case.semantics == "set"
    assert options.seed == 99
    assert options.for_backend("capture") == {"custom": "value"}
    with runner.store.connect() as connection:
        saved = connection.execute(
            "SELECT configuration_json FROM benchmark_runs"
        ).fetchone()
    with runner.store.connect() as connection:
        saved_case = connection.execute(
            "SELECT dialect FROM benchmark_cases"
        ).fetchone()
    assert saved_case["dialect"] == "sqlite"
    configuration = json.loads(saved["configuration_json"])
    assert "backend_dialects" not in configuration
    assert configuration["backend_options"] == {"capture": {"custom": "value"}}


@pytest.mark.asyncio
async def test_runner_strips_shared_clauses_and_records_option(tmp_path) -> None:
    dataset = tmp_path / "cases.csv"
    write_dataset(dataset)
    dataset.write_text(
        dataset.read_text().replace(
            "SELECT a FROM t", "SELECT a FROM t ORDER BY a LIMIT 2"
        )
    )
    checker = CapturingBenchmarkStub()
    store = ResultStore(tmp_path / "results.sqlite")
    runner = BenchmarkRunner(CheckService(CheckerRegistry((checker,))), store)

    run_id = await runner.run_csv(dataset, strip_shared_order_by_limit=True)

    case, _ = checker.captured
    assert case.query_left == "SELECT a FROM t"
    assert case.query_right == "SELECT a FROM t"
    with store.connect() as connection:
        saved = connection.execute(
            "SELECT configuration_json FROM benchmark_runs WHERE id = ?", (run_id,)
        ).fetchone()
    assert json.loads(saved["configuration_json"])["strip_shared_order_by_limit"] is True
    with pytest.raises(ValueError, match="different benchmark options"):
        await runner.run_csv(dataset, run_id=run_id)


@pytest.mark.asyncio
async def test_runner_rejects_legacy_columns(
    tmp_path,
) -> None:
    dataset = tmp_path / "cases.csv"
    with dataset.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "dbid",
                "index",
                "q1",
                "q2",
                "schema_ddl",
                "clean_ddl",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "dbid": "db",
                "index": "1",
                "q1": "SELECT a FROM t",
                "q2": "SELECT a FROM t",
                "schema_ddl": "CREATE TABLE t (a INT, b TEXT);",
                "clean_ddl": "CREATE TABLE t (a INT);",
            }
        )
    checker = CapturingBenchmarkStub()
    runner = BenchmarkRunner(
        CheckService(CheckerRegistry((checker,))),
        ResultStore(tmp_path / "results.sqlite"),
    )

    with pytest.raises(ValueError, match="missing columns"):
        await runner.run_csv(dataset)


@pytest.mark.asyncio
async def test_resume_rejects_changed_queries_with_same_case_ids(tmp_path):
    dataset = tmp_path / "cases.csv"
    write_dataset(dataset)
    runner = BenchmarkRunner(
        CheckService(CheckerRegistry((BenchmarkStub("eq", Verdict.EQUIVALENT),))),
        ResultStore(tmp_path / "results.sqlite"),
    )
    run_id = await runner.run_csv(dataset)
    dataset.write_text(dataset.read_text().replace("SELECT a", "SELECT DISTINCT a"))
    with pytest.raises(ValueError, match="dataset contents"):
        await runner.run_csv(dataset, run_id=run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("durations, expected", [([30, 10, 20], 20), ([40, 10, 20, 30], 25)])
async def test_summary_median_and_result_updates(tmp_path, durations, expected):
    dataset = tmp_path / "cases.csv"
    write_dataset(dataset)
    checker = BenchmarkStub("eq", Verdict.EQUIVALENT)
    store = ResultStore(tmp_path / "results.sqlite")
    run_id = await BenchmarkRunner(CheckService(CheckerRegistry((checker,))), store).run_csv(dataset)
    for index, duration in enumerate(durations, 1):
        case = EquivalenceCase("SELECT 1", "SELECT 1", "", "sqlite", "bag", f"db:{index}")
        result = await checker.check(case, CheckOptions())
        store.save_results(
            run_id=run_id, case=case, dataset_row=index, db_id="db", ground_truth="EQ",
            results=[("EQ", replace(result, duration_ms=duration))],
        )
    summary = store.summaries(run_id=run_id)[0]
    assert summary.evaluated == len(durations)
    assert summary.median_duration_ms == expected
    assert summary.eq_ratio == 1


@pytest.mark.asyncio
async def test_runner_preserves_parseval_options(tmp_path):
    dataset = tmp_path / "cases.csv"
    write_dataset(dataset)
    checker = CapturingBenchmarkStub()
    checker.descriptor = replace(
        checker.descriptor, checker=CheckerRef("parseval", "test", "1")
    )
    runner = BenchmarkRunner(
        CheckService(CheckerRegistry((checker,))),
        ResultStore(tmp_path / "results.sqlite"),
    )
    options = {"parseval": {"custom": "value", "connection_string": "sqlite:///configured.sqlite"}}
    await runner.run_csv(dataset, backend_options=options)
    _, captured = checker.captured
    assert captured.for_backend("parseval")["connection_string"] == "sqlite:///configured.sqlite"
    assert captured.for_backend("parseval")["custom"] == "value"
    assert options == {"parseval": {"custom": "value", "connection_string": "sqlite:///configured.sqlite"}}


@pytest.mark.asyncio
async def test_runner_reports_progress_on_resume(tmp_path):
    dataset = tmp_path / "cases.csv"
    write_dataset(dataset)
    runner = BenchmarkRunner(
        CheckService(CheckerRegistry((BenchmarkStub("eq", Verdict.EQUIVALENT),))),
        ResultStore(tmp_path / "results.sqlite"),
    )
    updates = []
    def progress(position, total, run_id):
        updates.append((position, total, run_id))

    run_id = await runner.run_csv(dataset, progress=progress)
    await runner.run_csv(dataset, run_id=run_id, progress=progress)
    assert updates == [(1, 1, run_id), (1, 1, run_id)]
