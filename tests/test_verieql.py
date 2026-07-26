from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import evebench.adapters.verieql as verieql_module
from evebench.adapters.core import execute_lifecycle
from evebench.adapters.verieql import VeriEQLAdapter, VeriEQLConfig
from evebench.adapters.schema import parse_verieql_ddl
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
        dialect="calcite",
        semantics="bag",
    )


def verieql_adapter() -> VeriEQLAdapter:
    root = Path(__file__).parents[1] / "baselines" / "VeriEQL"
    return VeriEQLAdapter(VeriEQLConfig(root_path=str(root)))


def test_translates_ddl_to_verieql_schema_and_constraints() -> None:
    parsed = parse_verieql_ddl(verieql_case().schema_ddl, "calcite")

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
        "evebench.adapters.verieql_runner",
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
