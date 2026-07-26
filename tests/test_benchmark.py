from __future__ import annotations

import csv
import json
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

    async def check(
        self, case: EquivalenceCase, options: CheckOptions
    ) -> CheckResult:
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
            supported_dialects=("sqlite", "calcite"),
            supported_semantics=("bag", "set"),
            execution_mode="in_process",
        )
        self.captured = None

    async def check(
        self, case: EquivalenceCase, options: CheckOptions
    ) -> CheckResult:
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
                "clean_ddl",
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
                "clean_ddl": "CREATE TABLE t (a INT);",
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
            BenchmarkStub("timeout", None, ExecutionStatus.TIMEOUT),
        )
    )

    run_id = await BenchmarkRunner(CheckService(registry), store).run_csv(dataset)

    summaries = {row.checker_id: row for row in store.summaries(run_id=run_id)}
    assert summaries["eq"].eq_ratio == 1
    assert summaries["neq"].neq_ratio == 1
    assert summaries["unknown"].unknown_ratio == 1
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
    config = BenchmarkConfig.load(
        "configs/benchmarks/bird_baseline.toml"
    )

    assert config.name == "bird_baseline"
    assert config.dataset_path.name == "bird_benchmark.csv"
    assert config.checker_ids == (
        "sqlsolver",
        "spes",
        "verieql",
        "parseval",
    )
    assert config.backends["parseval"].dialect == "sqlite"
    assert config.backends["sqlsolver"].dialect == "calcite"
    assert config.backends["verieql"].options == {
        "bound_size": 2,
        "use_constraints": True,
    }


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
        backend_dialects={"capture": "calcite"},
        backend_options={"capture": {"custom": "value"}},
    )

    assert checker.captured is not None
    case, options = checker.captured
    assert case.dialect == "calcite"
    assert case.semantics == "set"
    assert options.seed == 99
    assert options.for_backend("capture") == {"custom": "value"}
    with runner.store.connect() as connection:
        saved = connection.execute(
            "SELECT configuration_json FROM benchmark_runs"
        ).fetchone()
    configuration = json.loads(saved["configuration_json"])
    assert configuration["backend_dialects"] == {"capture": "calcite"}
    assert configuration["backend_options"] == {
        "capture": {"custom": "value"}
    }


@pytest.mark.asyncio
async def test_runner_prefers_full_schema_ddl_over_legacy_clean_ddl(
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

    await runner.run_csv(dataset)

    assert checker.captured is not None
    case, _ = checker.captured
    assert case.schema_ddl == "CREATE TABLE t (a INT, b TEXT);"
