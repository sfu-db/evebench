from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import evebench.adapters.qed.adapter as qed_module
from evebench.adapters import BackendAdapter
from evebench.adapters.core import execute_lifecycle
from evebench.adapters.pooled import execute_adapter
from evebench.adapters.qed import QEDAdapter, QEDConfig
from evebench.adapters.qed.queries import compile_query
from evebench.adapters.qed.schema import compile_schema
from evebench.domain import CheckOptions, EquivalenceCase
from evebench.domain.models import ExecutionStatus, Verdict


def case(right="SELECT a FROM t"):
    return EquivalenceCase(
        query_left="SELECT a FROM t", query_right=right,
        schema_ddl="CREATE TABLE t (a INT);", dialect="mysql", semantics="bag",
    )


def test_qed_schema_folds_keys_and_controls_foreign_keys():
    ddl = """CREATE TABLE p (id INT PRIMARY KEY);
    CREATE TABLE t (id INT REFERENCES p(id), value INT);
    ALTER TABLE t ADD CONSTRAINT t_key UNIQUE (value);
    ALTER TABLE t ADD CONSTRAINT t_fk FOREIGN KEY (id) REFERENCES p(id);"""
    compiled = compile_schema(ddl, "mysql", strip_foreign_keys=True)
    assert compiled.count("CREATE TABLE") == 2
    assert "UNIQUE (value)" in compiled
    assert "FOREIGN KEY" not in compiled
    assert "REFERENCES" not in compiled
    assert "ALTER TABLE" not in compiled
    retained = compile_schema(ddl, "mysql")
    assert "FOREIGN KEY" in retained
    assert "REFERENCES" in retained


def test_qed_schema_forwards_unhandled_statements():
    ddl = """CREATE TABLE t (id INT);
    ALTER TABLE t ADD COLUMN extra INT;
    CREATE VIEW v AS SELECT id FROM t;
    ALTER TABLE missing ADD PRIMARY KEY (id);"""
    compiled = compile_schema(ddl, "mysql")
    assert "ALTER TABLE t ADD COLUMN extra INT" in compiled
    assert "CREATE VIEW v AS SELECT id FROM t" in compiled
    assert "ALTER TABLE missing ADD PRIMARY KEY (id)" in compiled


def test_qed_strip_foreign_keys_option(adapter):
    ddl = "CREATE TABLE p (id INT); CREATE TABLE t (id INT REFERENCES p(id));"
    original = replace(case(), schema_ddl=ddl)
    assert "FOREIGN KEY" in adapter.preprocess(original, CheckOptions()).sql
    stripped = adapter.preprocess(original, CheckOptions(
        backend_options={"qed": {"strip_foreign_keys": True}}
    )).sql
    assert "FOREIGN KEY" not in stripped


def test_qed_queries_require_one_select():
    assert compile_query("SELECT 1;", "mysql") == "SELECT 1"
    with pytest.raises(ValueError, match="exactly one"):
        compile_query("SELECT 1; SELECT 2", "mysql")


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(qed_module.shutil, "which", lambda command: command)
    return QEDAdapter(QEDConfig(parser_jar_path=__file__, prover_path="qed-prover"))


@pytest.mark.parametrize("provable,verdict", [(True, Verdict.EQUIVALENT), (False, Verdict.UNKNOWN)])
def test_pipeline_and_worker_dispatch(adapter, monkeypatch, provable, verdict):
    paths = []
    timeouts = []

    def run(command, **kwargs):
        path = Path(command[-1])
        paths.append(path)
        timeouts.append(kwargs["timeout"])
        if path.suffix == ".sql":
            sql = path.read_text()
            assert "CREATE TABLE t (a INT)" in sql
            assert sql.count("SELECT a FROM t") == 2
            assert "--enable-preview" in command
            path.with_suffix(".json").write_text('{"queries": []}')
        else:
            assert path == paths[0].with_suffix(".json")
            assert json.loads(path.read_text()) == {"queries": []}
            path.with_suffix(".result").write_text(json.dumps({
                "provable": provable, "panicked": False,
            }))
        return SimpleNamespace(returncode=0, stdout="stage output", stderr="")

    monkeypatch.setattr(qed_module.subprocess, "run", run)
    assert isinstance(adapter, BackendAdapter)
    result = execute_adapter("qed", adapter._config, case(), CheckOptions(timeout_ms=2000))
    assert result.execution_status == ExecutionStatus.COMPLETED
    assert result.verdict == verdict
    assert 0 < timeouts[1] <= timeouts[0] <= 1.9
    assert all(not path.parent.exists() for path in paths)
    assert json.loads(result.raw_output)["result"]["provable"] == provable


@pytest.mark.parametrize("failure", ["parser_missing", "parser_exit", "prover_exit", "missing_result", "malformed_result", "panic", "timeout"])
def test_pipeline_failures(adapter, monkeypatch, failure):
    paths = []

    def run(command, **kwargs):
        path = Path(command[-1])
        paths.append(path)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        stage = "parser" if path.suffix == ".sql" else "prover"
        if failure == f"{stage}_exit":
            return SimpleNamespace(returncode=1, stdout="", stderr="stage failed")
        if stage == "parser" and failure != "parser_missing":
            path.with_suffix(".json").write_text("{}")
        elif stage == "prover" and failure != "missing_result":
            path.with_suffix(".result").write_text(
                "bad json" if failure == "malformed_result" else
                '{"provable": false, "panicked": true}'
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="diagnostic")

    monkeypatch.setattr(qed_module.subprocess, "run", run)
    result = execute_lifecycle(adapter, case(), CheckOptions())
    assert result.execution_status == (ExecutionStatus.TIMEOUT if failure == "timeout" else ExecutionStatus.FAILED)
    assert result.verdict is None
    assert all(not path.parent.exists() for path in paths)
    if failure.startswith("parser"):
        assert len(paths) == 1
        assert result.reason_code == "parser_error"


@pytest.mark.parametrize("stats", [{}, {"provable": "false", "panicked": False}, []])
def test_invalid_result_fields(adapter, monkeypatch, stats):
    monkeypatch.setattr(adapter, "run", lambda prepared: {"result": stats})
    result = execute_lifecycle(adapter, case(), CheckOptions())
    assert result.reason_code == "unparseable_output"


def test_unavailable_backend():
    adapter = QEDAdapter(QEDConfig("/missing/qed.jar", "/missing/qed-prover"))
    result = execute_lifecycle(adapter, case(), CheckOptions())
    assert result.reason_code == "backend_unavailable"


def test_unknown_options_do_not_prevent_qed_run(adapter, monkeypatch):
    called = []
    monkeypatch.setattr(adapter, "run", lambda prepared: called.append(prepared) or {
        "result": {"provable": True, "panicked": False}
    })
    result = execute_lifecycle(adapter, case(), CheckOptions(backend_options={"qed": {"unknown": 1}}))
    assert called
    assert result.verdict == Verdict.EQUIVALENT


def test_unsupported_semantics(adapter):
    result = execute_lifecycle(adapter, replace(case(), semantics="set"), CheckOptions())
    assert result.reason_code == "unsupported_semantics"


@pytest.mark.parametrize("right,verdict", [("SELECT a FROM t", Verdict.EQUIVALENT), ("SELECT a FROM t WHERE a > 0", Verdict.UNKNOWN)])
def test_real_qed(right, verdict):
    root = Path(__file__).resolve().parents[1] / "baselines/qed"
    java_home = os.environ.get("JAVA19_HOME")
    adapter = QEDAdapter(QEDConfig(
        parser_jar_path=str(root / "parser/target/qed-parser-1.0-SNAPSHOT-jar-with-dependencies.jar"),
        prover_path=str(root / "prover/target/release/qed-prover"),
        java_command=os.environ.get("EVEBENCH_QED_JAVA", str(Path(java_home) / "bin/java") if java_home else "java"),
    ))
    if not adapter.descriptor.available:
        pytest.skip(adapter.descriptor.unavailable_reason)
    result = execute_lifecycle(adapter, case(right), CheckOptions(timeout_ms=30000))
    assert result.execution_status == ExecutionStatus.COMPLETED, result
    assert result.verdict == verdict


def test_default_registry_includes_qed(monkeypatch):
    from evebench.api.app import default_registry

    monkeypatch.setenv("EVEBENCH_QED_PARSER_JAR", __file__)
    monkeypatch.setenv("EVEBENCH_QED_PROVER", "/custom/qed-prover")
    monkeypatch.setenv("EVEBENCH_QED_JAVA", "/custom/java19")
    checker = default_registry(SimpleNamespace()).get("qed")
    assert checker._adapter_config == QEDConfig(
        parser_jar_path=__file__, prover_path="/custom/qed-prover",
        java_command="/custom/java19",
    )
