import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reduce_simple_sqlite_pairs.py"
spec = importlib.util.spec_from_file_location("reduce_simple_sqlite_pairs", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_logic_tiers_capture_boolean_relational_and_semantic_complexity():
    examples = {
        "simple": "SELECT a FROM t WHERE b = 1",
        "moderate": "SELECT CAST(a AS REAL) / b FROM t WHERE c IS NULL",
        "complex": "SELECT a FROM t WHERE (b = 1 OR c = 2) AND d = 3",
    }
    assert {
        expected: module.logic_tier(module.query_features(sql)[0])
        for expected, sql in examples.items()
    } == {tier: tier for tier in examples}

    correlated = module.query_features(
        "SELECT * FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.id = t.id)"
    )[0]
    assert correlated.subqueries == 1
    assert correlated.correlated_subqueries == 1
    assert module.logic_tier(correlated) == "complex"


def test_profiles_make_pair_level_decisions():
    simple_pair = module.analyze_pair(
        "SELECT a FROM t WHERE b = 1", "SELECT x FROM y WHERE z = 2"
    )
    moderate_pair = module.analyze_pair(
        "SELECT a + 1 FROM t", "SELECT CAST(a AS REAL) FROM t"
    )
    assert simple_pair["same_structure"] is True
    assert module.pair_is_too_simple(simple_pair, "balanced")[0] is True
    assert module.pair_is_too_simple(moderate_pair, "balanced")[0] is False
    assert module.pair_is_too_simple(moderate_pair, "aggressive")[0] is True

    mixed_pair = module.analyze_pair(
        "SELECT a FROM t", "SELECT t.a FROM t JOIN u ON t.id = u.id JOIN v ON u.id = v.id"
    )
    assert module.pair_is_too_simple(mixed_pair, "aggressive")[0] is False
    assert module.pair_is_too_simple(mixed_pair, "complex-only")[0] is True


def test_postgres_distinct_order_by_rule_is_separate_from_parsing():
    assert module.postgres_order_by_error("SELECT a FROM t ORDER BY b") is None
    assert module.postgres_order_by_error("SELECT DISTINCT a FROM t ORDER BY a") is None
    assert module.postgres_order_by_error("SELECT DISTINCT a AS x FROM t ORDER BY x") is None
    assert module.postgres_order_by_error("SELECT DISTINCT a FROM t ORDER BY 1") is None
    assert "must appear in select list" in module.postgres_order_by_error(
        "SELECT DISTINCT a FROM t ORDER BY b"
    )


def test_calcite_translation_requotes_identifiers_without_changing_dialect():
    translated = module.translate_query_for_calcite(
        "SELECT `School Name`, rank FROM schools WHERE `date` != '2020-01-01'"
    )
    assert "`" not in translated
    assert '"School Name"' in translated
    assert '"rank"' in translated
    assert '"date" <> ' in translated
    assert "STRFTIME" in module.translate_query_for_calcite(
        "SELECT strftime('%Y', `date`) FROM events"
    )


def test_calcite_parses_preprocessed_schema_statement_lists():
    schema, _ = module.clean_schema_constraints(
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (pid INT REFERENCES p(id));", mode="all",
    )
    translated = module.translate_schema_for_calcite(schema)
    assert module.calcite_schema_errors([translated]) == [None]


def test_schema_cleanup_defaults_to_foreign_keys_only():
    ddl = (
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (id INT DEFAULT 1 NOT NULL UNIQUE, pid INT REFERENCES p(id), "
        "FOREIGN KEY (pid) REFERENCES p(id), CHECK (id > 0));"
    )
    foreign_only, removed = module.clean_schema_constraints(ddl)
    assert removed == 2
    assert "REFERENCES" not in foreign_only
    assert "FOREIGN KEY" not in foreign_only
    assert "PRIMARY KEY" in foreign_only
    assert "NOT NULL" in foreign_only
    assert "UNIQUE" in foreign_only
    assert "CHECK" in foreign_only

    unconstrained, removed_all = module.clean_schema_constraints(ddl, mode="all")
    assert removed_all > removed
    for constraint in ("REFERENCES", "PRIMARY KEY", "NOT NULL", "UNIQUE", "CHECK", "DEFAULT"):
        assert constraint not in unconstrained


def test_reduce_file_filters_audits_and_reindexes(tmp_path):
    source = tmp_path / "sqlite.csv"
    output = tmp_path / "reduced.csv"
    audit = tmp_path / "audit.csv"
    rows = [
        {
            "dbid": "db", "index": "8", "source_index": "80",
            "schema_ddl": "CREATE TABLE t (a INT, b INT);",
            "q1": "SELECT a FROM t WHERE b = 1",
            "q2": "SELECT a FROM t WHERE b = 2", "dialect": "sqlite",
            "ground_truth": "NEQ", "source": "bird",
        },
        {
            "dbid": "db", "index": "9", "source_index": "90",
            "schema_ddl": "CREATE TABLE t (id INT, a INT); CREATE TABLE u (id INT, x INT);",
            "q1": "SELECT t.a FROM t JOIN u ON t.id = u.id WHERE u.x > 1",
            "q2": "SELECT t.a FROM t JOIN u ON t.id = u.id WHERE u.x >= 2",
            "dialect": "sqlite", "ground_truth": "EQ", "source": "bird",
        },
    ]
    pd.DataFrame(rows).to_csv(source, index=False)

    assert module.reduce_file(source, output, audit=audit, require_calcite=False) == (1, 1)
    result = pd.read_csv(output)
    decisions = pd.read_csv(audit)
    assert result["index"].tolist() == [1]
    assert result["source_index"].tolist() == [90]
    assert result.loc[0, "q1"].startswith("SELECT t.a")
    assert result.loc[0, "schema_ddl"] == rows[1]["schema_ddl"]
    assert decisions["input_index"].tolist() == [8, 9]
    assert decisions["keep"].tolist() == [False, True]
    assert decisions.loc[0, "reason"] == "both_queries_logically_simple"
    assert decisions.loc[1, "q1_logic_tier"] == "moderate"
    assert decisions.loc[1, "q1_joins"] == 1


def test_calcite_failure_removes_whole_pair_and_is_audited(tmp_path, monkeypatch):
    source, output, audit = tmp_path / "in.csv", tmp_path / "out.csv", tmp_path / "audit.csv"
    pd.DataFrame([{
        "index": 1, "schema_ddl": "CREATE TABLE t (a INT);",
        "q1": "SELECT a + 1 FROM t", "q2": "SELECT rank + 1 FROM t",
        "dialect": "sqlite", "ground_truth": "NEQ",
    }]).to_csv(source, index=False)
    monkeypatch.setattr(module, "calcite_syntax_errors", lambda queries: [None, "bad keyword"])
    monkeypatch.setattr(module, "calcite_schema_errors", lambda schemas: [None] * len(schemas))

    assert module.reduce_file(source, output, audit=audit) == (0, 1)
    decision = pd.read_csv(audit, keep_default_na=False).iloc[0]
    assert bool(decision["calcite_q1_ok"]) is True
    assert bool(decision["calcite_q2_ok"]) is False
    assert decision["calcite_q2_error"] == "bad keyword"
    assert '"rank"' in decision["q2_calcite_sql"]
    assert decision["reason"] == "portability_rejected_q2"


def test_reduce_file_validates_paths_columns_and_profile(tmp_path):
    source = tmp_path / "sqlite.csv"
    pd.DataFrame([{"index": 1}]).to_csv(source, index=False)
    with pytest.raises(ValueError, match="output must differ"):
        module.reduce_file(source, source)
    with pytest.raises(ValueError, match="unsupported profile"):
        module.reduce_file(source, tmp_path / "out.csv", profile="unknown")
    with pytest.raises(ValueError, match="unsupported constraint mode"):
        module.reduce_file(source, tmp_path / "out.csv", constraint_mode="unknown")
    with pytest.raises(ValueError, match="missing required columns"):
        module.reduce_file(source, tmp_path / "out.csv")
