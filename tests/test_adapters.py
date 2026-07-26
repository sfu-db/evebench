from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import evebench.adapters.parseval as parseval_module
import evebench.adapters.spes as spes_module
import evebench.adapters.sqlsolver as sqlsolver_module
from evebench.adapters.parseval import (
    ParsevalAdapter,
    ParsevalConfig,
)
from evebench.adapters.parseval_database import cleanup_database
from evebench.adapters.sqlsolver import SQLSolverAdapter, SQLSolverConfig
from evebench.adapters.spes import SPESAdapter, SPESConfig
from evebench.domain import CheckOptions, EquivalenceCase
from evebench.domain.models import ExecutionStatus, Verdict


def sqlsolver_case() -> EquivalenceCase:
    return EquivalenceCase(
        query_left="SELECT a FROM t",
        query_right="SELECT a FROM t",
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="calcite",
        semantics="bag",
    )


def sqlsolver_adapter() -> SQLSolverAdapter:
    path = Path(__file__)
    return SQLSolverAdapter(
        SQLSolverConfig(
            jar_path=str(path),
            library_path=str(path.parent),
            working_directory=str(path.parent),
        )
    )


def spes_case() -> EquivalenceCase:
    return EquivalenceCase(
        query_left="SELECT a FROM t",
        query_right="SELECT a FROM t",
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="calcite",
        semantics="bag",
    )


def spes_adapter() -> SPESAdapter:
    path = Path(__file__)
    return SPESAdapter(
        SPESConfig(
            jar_path=str(path),
            library_path=str(path.parent),
            working_directory=str(path.parent),
        )
    )


def test_spes_normalizes_equivalence(monkeypatch) -> None:
    adapter = spes_adapter()
    monkeypatch.setattr(
        adapter,
        "_invoke",
        lambda prepared: SimpleNamespace(
            returncode=0,
            stdout='{"decision":"true","plan1":"left","plan2":"right"}\n',
            stderr="",
        ),
    )

    result = adapter.check(spes_case(), CheckOptions())

    assert result.execution_status == ExecutionStatus.COMPLETED
    assert result.verdict == Verdict.EQUIVALENT
    assert result.raw_output is not None


def test_spes_normalizes_unknown_reason(monkeypatch) -> None:
    adapter = spes_adapter()
    monkeypatch.setattr(
        adapter,
        "_invoke",
        lambda prepared: SimpleNamespace(
            returncode=0,
            stdout='{"decision":"unknown","reason":"unsupported feature"}',
            stderr="",
        ),
    )

    result = adapter.check(spes_case(), CheckOptions())

    assert result.execution_status == ExecutionStatus.COMPLETED
    assert result.verdict == Verdict.UNKNOWN
    assert result.reason_code == "backend_unknown"
    assert result.message == "unsupported feature"


def test_spes_passes_case_to_executable_jar(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout='{"decision":"false"}',
            stderr="",
        )

    monkeypatch.setattr(spes_module.subprocess, "run", fake_run)
    adapter = spes_adapter()
    case = EquivalenceCase(
        query_left="SELECT parent_id FROM child",
        query_right="SELECT parent_id FROM child",
        schema_ddl=(
            "CREATE TABLE parent (id INT PRIMARY KEY);"
            "CREATE TABLE child (parent_id INT REFERENCES parent(id));"
        ),
        dialect="calcite",
        semantics="bag",
    )

    result = adapter.check(case, CheckOptions(timeout_ms=2_000))

    assert result.verdict == Verdict.NOT_EQUIVALENT
    assert captured["command"][-6:] == [
        "-sql1",
        case.query_left,
        "-sql2",
        case.query_right,
            "-schema",
            (
                "CREATE TABLE `parent` (`id` INT PRIMARY KEY);\n"
                "CREATE TABLE `child` (`parent_id` INT);"
            ),
    ]
    assert captured["kwargs"]["timeout"] == 1.9
    assert (
        captured["kwargs"]["env"]["LD_LIBRARY_PATH"].split(":")[0]
        == adapter._config.library_path
    )


def test_sqlsolver_normalizes_equivalence(monkeypatch) -> None:
    adapter = sqlsolver_adapter()
    monkeypatch.setattr(adapter, "_verify", lambda prepared: "EQ")

    result = adapter.check(sqlsolver_case(), CheckOptions())

    assert result.execution_status == ExecutionStatus.COMPLETED
    assert result.verdict == Verdict.EQUIVALENT
    assert result.raw_output == "EQ"


def test_sqlsolver_normalizes_backend_timeout(monkeypatch) -> None:
    adapter = sqlsolver_adapter()
    monkeypatch.setattr(adapter, "_verify", lambda prepared: "TIMEOUT")

    result = adapter.check(sqlsolver_case(), CheckOptions())

    assert result.execution_status == ExecutionStatus.TIMEOUT
    assert result.verdict is None
    assert result.reason_code == "backend_timeout"


def test_sqlsolver_passes_queries_schema_and_timeout_to_java(monkeypatch) -> None:
    captured = {}

    class FakeArrayList(list):
        def add(self, value):
            self.append(value)

    class FakeResults:
        def get(self, index):
            assert index == 0
            return "EQ"

    class FakeVerification:
        @staticmethod
        def verify(left, right, schema, timeout):
            captured["verify"] = (list(left), list(right), schema, timeout)
            return FakeResults()

    def fake_jclass(name):
        if name == "sqlsolver.api.entry.Verification":
            return FakeVerification
        if name == "java.util.ArrayList":
            return FakeArrayList
        raise AssertionError(name)

    fake_jpype = SimpleNamespace(
        isJVMStarted=lambda: False,
        getDefaultJVMPath=lambda: "/fake/libjvm.so",
        startJVM=lambda *args, **kwargs: captured.update(
            {"start_args": args, "start_kwargs": kwargs}
        ),
        JClass=fake_jclass,
        JLong=int,
    )
    monkeypatch.setitem(sys.modules, "jpype", fake_jpype)
    monkeypatch.setattr(sqlsolver_module.ctypes, "CDLL", lambda *args, **kwargs: None)
    adapter = sqlsolver_adapter()
    case = EquivalenceCase(
        query_left="SELECT parent_id FROM child",
        query_right="SELECT parent_id FROM child",
        schema_ddl=(
            "CREATE TABLE parent (id INT PRIMARY KEY);"
            "CREATE TABLE child (parent_id INT REFERENCES parent(id));"
        ),
        dialect="calcite",
        semantics="bag",
    )

    prepared = adapter.preprocess(case, CheckOptions(timeout_ms=1_500))
    native = adapter._verify(prepared)

    assert native == "EQ"
    assert captured["verify"] == (
        [case.query_left],
            [case.query_right],
            (
                "CREATE TABLE `parent` (`id` INT PRIMARY KEY);\n"
                "CREATE TABLE `child` (`parent_id` INT);"
            ),
        2,
    )
    assert captured["start_kwargs"]["classpath"] == [
        adapter._config.jar_path
    ]
    assert (
        f"-Djava.library.path={adapter._config.library_path}"
        in captured["start_args"]
    )


def test_parseval_rejects_unknown_backend_option() -> None:
    adapter = ParsevalAdapter(ParsevalConfig(backend_version="test"))
    case = EquivalenceCase(
        query_left="SELECT a FROM t",
        query_right="SELECT a FROM t",
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="sqlite",
        semantics="bag",
    )

    result = adapter.check(
        case,
        CheckOptions(backend_options={"parseval": {"does_not_exist": True}}),
    )

    assert result.execution_status == ExecutionStatus.INVALID_INPUT
    assert result.reason_code == "invalid_backend_options"


def test_parseval_receives_all_backend_parameters(monkeypatch, tmp_path) -> None:
    captured = {}
    connection_string = f"sqlite:///{tmp_path / 'adapter.db'}"

    def fake_disprove(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            to_dict=lambda: {
                "verdict": "eq",
                "generation": {
                    "rows_generated": 0,
                    "coverage": 1.0,
                    "solver_calls": 0,
                },
                "connection_string": "sqlite:///should-not-be-returned.db",
                "error_msg": "",
            }
        )

    monkeypatch.setattr(parseval_module, "disprove", fake_disprove)
    adapter = ParsevalAdapter(ParsevalConfig(backend_version="test"))
    case = EquivalenceCase(
        query_left="SELECT a FROM t",
        query_right="SELECT a FROM t",
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="sqlite",
        semantics="set",
    )

    result = adapter.check(
        case,
        CheckOptions(
            timeout_ms=1_500,
            seed=7,
            backend_options={
                "parseval": {
                    "connection_string": connection_string,
                    "max_solver_calls": 3,
                }
            },
        ),
    )

    assert result.execution_status == ExecutionStatus.COMPLETED
    assert captured["args"][:3] == (
        case.query_left,
        case.query_right,
        case.schema_ddl,
    )
    assert captured["args"][4] == "sqlite"
    assert captured["args"][3] == connection_string
    assert captured["kwargs"]["semantics"] == "set"
    assert captured["kwargs"]["timeout"] == 2
    generation_config = captured["kwargs"]["generation_config"]
    assert generation_config.seed == 7
    assert generation_config.max_solver_calls == 3
    assert "connection_string" not in result.raw_output


def test_parseval_requires_connection_string() -> None:
    adapter = ParsevalAdapter(ParsevalConfig(backend_version="test"))
    case = EquivalenceCase(
        query_left="SELECT a FROM t",
        query_right="SELECT a FROM t",
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="sqlite",
        semantics="bag",
    )

    result = adapter.check(case, CheckOptions())

    assert result.execution_status == ExecutionStatus.INVALID_INPUT
    assert result.reason_code == "missing_connection_string"


def test_parseval_rejects_connection_for_different_dialect() -> None:
    adapter = ParsevalAdapter(ParsevalConfig(backend_version="test"))
    case = EquivalenceCase(
        query_left="SELECT a FROM t",
        query_right="SELECT a FROM t",
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="sqlite",
        semantics="bag",
    )

    result = adapter.check(
        case,
        CheckOptions(
            backend_options={
                "parseval": {
                    "connection_string": "postgresql://localhost/evebench",
                }
            }
        ),
    )

    assert result.execution_status == ExecutionStatus.INVALID_INPUT
    assert result.reason_code == "invalid_connection_string"


def test_cleanup_removes_sqlite_database_and_sidecars(tmp_path) -> None:
    database = tmp_path / "parseval.db"
    sidecar_shm = tmp_path / "parseval.db-shm"
    sidecar_wal = tmp_path / "parseval.db-wal"
    for path in (database, sidecar_shm, sidecar_wal):
        path.write_text("test")

    cleanup_database(f"sqlite:///{database}")

    assert not database.exists()
    assert not sidecar_shm.exists()
    assert not sidecar_wal.exists()


def test_parseval_refuses_protected_database() -> None:
    adapter = ParsevalAdapter(ParsevalConfig(backend_version="test"))
    case = EquivalenceCase(
        query_left="SELECT 1",
        query_right="SELECT 1",
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="postgresql",
        semantics="bag",
    )

    result = adapter.check(
        case,
        CheckOptions(
            backend_options={
                "parseval": {
                    "connection_string": "postgresql://localhost/postgres",
                }
            }
        ),
    )

    assert result.execution_status == ExecutionStatus.INVALID_INPUT
    assert result.reason_code == "invalid_connection_string"
