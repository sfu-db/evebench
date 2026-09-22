from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import evebench.adapters.verieql.adapter as verieql_module
from evebench.adapters.core import execute_lifecycle
from evebench.adapters.verieql import VeriEQLAdapter, VeriEQLConfig
from evebench.adapters.verieql.schema import parse_verieql_ddl
from evebench.domain import CheckOptions, EquivalenceCase
from evebench.domain.models import ExecutionStatus, Verdict


def verieql_case() -> EquivalenceCase:
    return EquivalenceCase(
        query_left="SELECT parent_id FROM child",
        query_right="SELECT parent_id FROM child",
        schema_ddl="""
            CREATE TABLE parent (id INT PRIMARY KEY);
            CREATE TABLE child (
                id INT NOT NULL,
                parent_id INT REFERENCES parent(id)
            );
        """,
        dialect="mysql",
        semantics="bag",
    )


def verieql_adapter() -> VeriEQLAdapter:
    root = Path(__file__).parents[1] / "baselines" / "VeriEQL"
    return VeriEQLAdapter(VeriEQLConfig(root_path=str(root)))


def test_translates_ddl_to_verieql_schema_and_constraints() -> None:
    parsed = parse_verieql_ddl(verieql_case().schema_ddl, "mysql")

    assert parsed.tables == {
        "PARENT": {"ID": "INT"},
        "CHILD": {"ID": "INT", "PARENT_ID": "INT"},
    }
    assert {"primary": [{"value": "PARENT__ID"}]} in parsed.constraints
    assert {"not_null": {"value": "CHILD__ID"}} in parsed.constraints
    assert {
        "foreign": [
            {"value": "CHILD__PARENT_ID"},
            {"value": "PARENT__ID"},
        ]
    } in parsed.constraints


def test_normalizes_verieql_counterexample(monkeypatch) -> None:
    adapter = verieql_adapter()
    monkeypatch.setattr(
        adapter,
        "run",
        lambda *args: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "state": "not_equivalent",
                    "bound_size": 2,
                    "traversing_time_s": 0.1,
                    "solving_time_s": 0.2,
                    "counterexample": {"T": [{"A": "1"}]},
                }
            ),
            stderr="",
        ),
    )

    result = execute_lifecycle(adapter, verieql_case(), CheckOptions())

    assert result.execution_status == ExecutionStatus.COMPLETED
    assert result.verdict == Verdict.NOT_EQUIVALENT
    assert result.witness == {"T": [{"A": "1"}]}
    assert result.metrics["bound_size"] == 2


def test_passes_native_schema_and_bound_to_runner(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout='{"state":"equivalent","bound_size":3}',
            stderr="",
        )

    monkeypatch.setattr(verieql_module.subprocess, "run", fake_run)
    adapter = verieql_adapter()

    result = execute_lifecycle(
        adapter,
        verieql_case(),
        CheckOptions(
            timeout_ms=2_000,
            backend_options={"verieql": {"bound_size": 3}},
        ),
    )

    payload = json.loads(captured["kwargs"]["input"])
    assert result.verdict == Verdict.EQUIVALENT
    assert payload["bound_size"] == 3
    assert payload["schema"]["CHILD"]["PARENT_ID"] == "INT"
    assert payload["constraints"]
    assert captured["kwargs"]["timeout"] == 1.9
    assert captured["command"][-2:] == [
        "-m",
        "evebench.adapters.verieql.runner",
    ]


def test_rejects_invalid_bound_size() -> None:
    result = execute_lifecycle(
        verieql_adapter(),
        verieql_case(),
        CheckOptions(
            backend_options={"verieql": {"bound_size": 0}},
        ),
    )

    assert result.execution_status == ExecutionStatus.INVALID_INPUT
    assert result.reason_code == "invalid_bound_size"


def test_alter_table_constraints_match_inline_constraints() -> None:
    inline = """
        CREATE TABLE parent (id INT, tenant INT, PRIMARY KEY (id, tenant));
        CREATE TABLE child (parent_id INT, FOREIGN KEY (parent_id) REFERENCES parent(id));
    """
    separate = """
        CREATE TABLE parent (id INT, tenant INT);
        CREATE TABLE child (parent_id INT);
        ALTER TABLE ONLY parent ADD CONSTRAINT parent_pk PRIMARY KEY (id, tenant);
        ALTER TABLE child ADD CONSTRAINT child_fk FOREIGN KEY (parent_id) REFERENCES parent(id);
    """
    assert parse_verieql_ddl(separate, "postgresql") == parse_verieql_ddl(
        inline, "postgresql"
    )


def test_alter_table_unsupported_constraints_are_not_ignored() -> None:
    for constraint in ("UNIQUE (id)", "CHECK (id > 0)"):
        parsed = parse_verieql_ddl(
            f"CREATE TABLE t (id INT); ALTER TABLE t ADD CONSTRAINT c {constraint};",
            "postgresql",
        )
        assert any("unsupported_schema" in item for item in parsed.constraints)
