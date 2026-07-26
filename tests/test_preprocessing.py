from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from evebench.adapters import (
    ParsevalConfig,
    SPESConfig,
    SQLSolverConfig,
    VeriEQLConfig,
)
from evebench.adapters.core import AdapterInputError
from evebench.adapters.parseval import ParsevalAdapter
from evebench.adapters.spes import SPESAdapter
from evebench.adapters.sqlsolver import SQLSolverAdapter
from evebench.adapters.verieql import VeriEQLAdapter
from evebench.domain import CheckOptions, EquivalenceCase
from evebench.domain.models import ExecutionStatus

ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = Path(__file__).resolve()


def _sqlsolver_adapter() -> SQLSolverAdapter:
    return SQLSolverAdapter(
        SQLSolverConfig(
            jar_path=str(TEST_FILE),
            library_path=str(TEST_FILE.parent),
            working_directory=str(TEST_FILE.parent),
        )
    )


def _spes_adapter() -> SPESAdapter:
    return SPESAdapter(
        SPESConfig(
            jar_path=str(TEST_FILE),
            library_path=str(TEST_FILE.parent),
            working_directory=str(TEST_FILE.parent),
        )
    )


def _verieql_adapter(tmp_path: Path) -> VeriEQLAdapter:
    root = tmp_path / "verieql"
    root.mkdir(exist_ok=True)
    (root / "environment.py").touch()
    return VeriEQLAdapter(VeriEQLConfig(root_path=str(root)))


def _parseval_adapter() -> ParsevalAdapter:
    return ParsevalAdapter(ParsevalConfig(backend_version="test"))


def _case(*, dialect: str = "calcite") -> EquivalenceCase:
    return EquivalenceCase(
        query_left="  SELECT a FROM t  ",
        query_right="\nSELECT a FROM t\n",
        schema_ddl="  CREATE TABLE t (a INT);  ",
        dialect=dialect,
        semantics=" BAG ",
    )


def test_each_adapter_preprocesses_queries_schema_and_options(tmp_path) -> None:
    sqlsolver = _sqlsolver_adapter().preprocess(
        _case(),
        CheckOptions(timeout_ms=1_001),
    )
    spes = _spes_adapter().preprocess(
        _case(),
        CheckOptions(timeout_ms=2_000),
    )
    verieql = _verieql_adapter(tmp_path).preprocess(
        _case(),
        CheckOptions(
            timeout_ms=2_000,
            backend_options={
                "verieql": {"bound_size": 4, "use_constraints": False}
            },
        ),
    )
    connection = f"sqlite:///{tmp_path / 'parseval.sqlite'}"
    parseval = _parseval_adapter().preprocess(
        _case(dialect=" SQLITE "),
        CheckOptions(
            timeout_ms=1_001,
            seed=17,
            backend_options={
                "parseval": {
                    "connection_string": connection,
                    "max_solver_calls": 9,
                }
            },
        ),
    )

    assert sqlsolver.query_left == spes.query_left == "SELECT a FROM t"
    assert sqlsolver.query_right == spes.query_right == "SELECT a FROM t"
    assert sqlsolver.schema_ddl == spes.schema_ddl == "CREATE TABLE t (a INT);"
    assert sqlsolver.timeout_seconds == 2
    assert spes.timeout_seconds == 1.9

    assert verieql.query_left == verieql.query_right == "SELECT a FROM t"
    assert verieql.schema == {"T": {"A": "INT"}}
    assert verieql.constraints == []
    assert verieql.bound_size == 4
    assert verieql.timeout_seconds == 1.9

    assert parseval.query_left == parseval.query_right == "SELECT a FROM t"
    assert parseval.schema_ddl == "CREATE TABLE t (a INT);"
    assert parseval.dialect == "sqlite"
    assert parseval.semantics == "bag"
    assert parseval.connection_string == connection
    assert parseval.timeout_seconds == 2
    assert parseval.generation_config.seed == 17
    assert parseval.generation_config.max_solver_calls == 9


@pytest.mark.parametrize(
    ("case_update", "option_update", "reason_code", "status"),
    [
        ({"query_left": " "}, {}, "empty_query", ExecutionStatus.INVALID_INPUT),
        ({"schema_ddl": " "}, {}, "empty_schema", ExecutionStatus.INVALID_INPUT),
        (
            {"semantics": "set"},
            {},
            "unsupported_semantics",
            ExecutionStatus.UNSUPPORTED,
        ),
        (
            {"parameters": {"value": 1}},
            {},
            "unsupported_parameters",
            ExecutionStatus.UNSUPPORTED,
        ),
        ({}, {"timeout_ms": 0}, "invalid_timeout", ExecutionStatus.INVALID_INPUT),
        (
            {},
            {"memory_limit_mb": 128},
            "unsupported_memory_limit",
            ExecutionStatus.UNSUPPORTED,
        ),
        ({}, {"seed": True}, "invalid_seed", ExecutionStatus.INVALID_INPUT),
    ],
)
def test_shared_preprocessing_rejects_inputs_that_would_be_ignored(
    case_update,
    option_update,
    reason_code,
    status,
) -> None:
    case = replace(_case(), **case_update)
    options = replace(CheckOptions(), **option_update)

    with pytest.raises(AdapterInputError) as caught:
        _sqlsolver_adapter().preprocess(case, options)

    assert caught.value.reason_code == reason_code
    assert caught.value.status == status


def test_adapter_specific_options_are_type_checked(tmp_path) -> None:
    with pytest.raises(AdapterInputError) as caught:
        _verieql_adapter(tmp_path).preprocess(
            _case(),
            CheckOptions(
                backend_options={"verieql": {"use_constraints": 1}}
            ),
        )
    assert caught.value.reason_code == "invalid_backend_options"

    with pytest.raises(AdapterInputError) as caught:
        _parseval_adapter().preprocess(
            _case(dialect="sqlite"),
            CheckOptions(
                backend_options={
                    "parseval": {
                        "connection_string": (
                            f"sqlite:///{tmp_path / 'parseval.sqlite'}"
                        ),
                        "bootstrap_negatives": 1,
                    }
                }
            ),
        )
    assert caught.value.reason_code == "invalid_backend_options"


def test_all_bird_rows_preprocess_for_every_adapter(tmp_path) -> None:
    dataset = ROOT / "datasets" / "sqlite" / "bird_benchmark.csv"
    with dataset.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))

    assert len(rows) == 1_432
    sqlsolver_adapter = _sqlsolver_adapter()
    spes_adapter = _spes_adapter()
    verieql_adapter = _verieql_adapter(tmp_path)
    parseval_adapter = _parseval_adapter()
    for row in rows:
        base = EquivalenceCase(
            query_left=row["q1"],
            query_right=row["q2"],
            schema_ddl=row["schema_ddl"],
            dialect="calcite",
            semantics="bag",
            case_id=f"{row['dbid']}:{row['index']}",
        )
        options = CheckOptions(timeout_ms=30_000, seed=1)

        sqlsolver = sqlsolver_adapter.preprocess(base, options)
        spes = spes_adapter.preprocess(base, options)
        verieql = verieql_adapter.preprocess(
            base,
            replace(
                options,
                backend_options={
                    "verieql": {
                        "bound_size": 2,
                        "use_constraints": True,
                    }
                },
            ),
        )
        parseval = parseval_adapter.preprocess(
            replace(base, dialect="sqlite"),
            replace(
                options,
                backend_options={
                    "parseval": {
                        "connection_string": (
                            f"sqlite:///{tmp_path / 'parseval.sqlite'}"
                        ),
                        "max_solver_calls": 48,
                    }
                },
            ),
        )

        for prepared in (sqlsolver, spes, verieql, parseval):
            assert prepared.query_left == row["q1"].strip()
            assert prepared.query_right == row["q2"].strip()
        assert sqlsolver.schema_ddl
        assert spes.schema_ddl == sqlsolver.schema_ddl
        assert verieql.schema
        assert parseval.schema_ddl == row["schema_ddl"].strip()
