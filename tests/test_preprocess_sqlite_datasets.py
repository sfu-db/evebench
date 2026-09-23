import importlib.util
from pathlib import Path

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preprocess_sqlite_datasets.py"
spec = importlib.util.spec_from_file_location("preprocess_sqlite_datasets", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_calcite_syntax_preserves_sqlite_functions_and_filters_both_sides(tmp_path, caplog):
    source, output = tmp_path / "beers.csv", tmp_path / "out.csv"
    scalar = "SELECT strftime('%Y', '2024-01-01'), printf('%s;%s', 'a', 'b'), iif(1, 2, 3)"
    pairs = [
        (scalar, "SELECT 1"),
        ("SELECT a FROM t INDEXED BY idx", "SELECT a FROM t"),
        ("SELECT a FROM t", "SELECT a FROM t INDEXED BY idx"),
        ("SELECT a FROM t WHERE a != 1", "SELECT a FROM t WHERE a != 2"),
        ("SELECT nonexistent_function(a) FROM t", "SELECT 1"),
        ("SELECT COUNT(*) AS count, MIN(a) AS min, RANK() OVER (ORDER BY a) AS rank FROM t",
         "SELECT count, min, rank FROM (SELECT COUNT(*) AS count, MIN(a) AS min, "
         "RANK() OVER (ORDER BY a) AS rank FROM t) WHERE count != 0"),
    ]
    pd.DataFrame([
        dict(index=i, schema="CREATE TABLE t(a INTEGER); CREATE INDEX idx ON t(a);",
             q1=q1, q2=q2, ground_truth="NEQ")
        for i, (q1, q2) in enumerate(pairs)
    ]).to_csv(source, index=False)
    from scripts.preprocess_beers_dataset import preprocess_file
    assert preprocess_file(source, output) == (3, 3)
    result = pd.read_csv(output)
    assert result.q1.iloc[0] == scalar
    assert result.q1.iloc[1] == pairs[3][0]
    assert result.q2.iloc[1] == pairs[3][1]
    assert result.q1.iloc[2] == pairs[5][0]
    assert result.q2.iloc[2] == pairs[5][1]
    assert result.source_index.tolist() == [0, 3, 5]
    assert result["index"].tolist() == [1, 2, 3]
    assert "q1 Calcite syntax rejection" in caplog.text
    assert "q2 Calcite syntax rejection" in caplog.text


def test_calcite_syntax_only_and_statement_boundaries():
    errors = module.calcite_syntax_errors([
        "SELECT unknown_function(missing_column) FROM missing_table; -- trailing comment",
        "SELECT ';'",
        "WITH t AS (SELECT 1 AS a) SELECT a FROM t ORDER BY a",
        "SELECT 1; SELECT 2",
        "DELETE FROM t",
        "SELECT FROM",
    ])
    assert errors[:3] == [None] * 3
    assert all(isinstance(error, str) for error in errors[3:])


def test_missing_calcite_jar_preserves_output(tmp_path, monkeypatch):
    source, output = tmp_path / "beers.csv", tmp_path / "out.csv"
    output.write_text("existing output")
    pd.DataFrame([dict(index=1, schema="CREATE TABLE t(a INTEGER);",
                       q1="SELECT 1", q2="SELECT 2", ground_truth="NEQ")]).to_csv(source, index=False)
    monkeypatch.setenv("EVEBENCH_QED_PARSER_JAR", str(tmp_path / "missing.jar"))
    from scripts.preprocess_beers_dataset import preprocess_file
    with pytest.raises(ValueError, match="QED parser JAR not found"):
        preprocess_file(source, output)
    assert output.read_text() == "existing output"


def test_preprocess_filters_and_preserves_metadata(tmp_path):
    source, output = tmp_path / "bird.csv", tmp_path / "out.csv"
    pairs = [
        ("SELECT a FROM t -- comment", "SELECT a FROM t WHERE a > 0"),
        ("select a from t", "SELECT a FROM t"),
        ("SELECT missing FROM t", "SELECT a FROM t"),
        ("SELECT a FROM t", "SELECT missing FROM t"),
        ("SELECT a FROM t -- comment", "SELECT a FROM t WHERE a > 0"),
        ("SELECT a FROM t", "SELECT a + 1 FROM t"),
    ]
    pd.DataFrame([
        dict(dbid="test", index=str(i + 10), schema_ddl="CREATE TABLE t (a INTEGER);",
             q1=q1, q2=q2, ground_truth="NEQ")
        for i, (q1, q2) in enumerate(pairs)
    ]).to_csv(source, index=False)
    assert module.preprocess_file(source, output) == (2, 4)
    result = pd.read_csv(output)
    assert tuple(result.columns) == module.COLUMNS
    assert result["index"].tolist() == [1, 2]
    assert result.source_index.tolist() == [10, 15]
    assert result.dialect.tolist() == ["sqlite"] * 2
    assert result.source.tolist() == ["bird"] * 2
    assert result.ground_truth.tolist() == ["NEQ"] * 2
    assert result.q1.iloc[0] == "SELECT a FROM t "
    with pytest.raises(ValueError, match="output must differ"):
        module.preprocess_file(source, source)


def test_sqlite_comments_and_normalization():
    query = "SELECT '--literal', `a--b`, [c--d], \"e--f\" /* -- block */ -- remove\nFROM t"
    assert module.remove_line_comments(query) == query.replace("-- remove", "")
    assert module.normalized_queries_match("select 1; select 2", "SELECT 1; SELECT 2")
    assert not module.normalized_queries_match("select 1; select 2", "SELECT 1; SELECT 3")


def test_invalid_schema_preserves_existing_output(tmp_path):
    source, output = tmp_path / "bird.csv", tmp_path / "out.csv"
    output.write_text("existing output")
    pd.DataFrame([dict(dbid="test", index="1", schema_ddl="invalid ddl",
                       q1="SELECT 1", q2="SELECT 2", ground_truth="EQ")]).to_csv(source, index=False)
    with pytest.raises(ValueError, match="invalid schema"):
        module.preprocess_file(source, output)
    assert output.read_text() == "existing output"


def test_beers_format_filters_missing_invalid_matching_and_duplicate_pairs(tmp_path):
    source, output = tmp_path / "beers.csv", tmp_path / "out.csv"
    pairs = [
        ("SELECT a FROM t", "SELECT a FROM t WHERE a > 0"),
        ("select a from t -- comment", "SELECT a FROM t"),
        ("SELECT a FROM t", "SELECT missing FROM t"),
        ("", "SELECT a FROM t"),
        ("SELECT a FROM t", "SELECT a FROM t WHERE a > 0"),
        ("SELECT a FROM t", "SELECT a + 1 FROM t"),
    ]
    pd.DataFrame([
        dict(index=str(i + 24), schema="CREATE TABLE t (a INTEGER);",
             q1=q1, q2=q2, ground_truth="NEQ", counterexample_valid="N")
        for i, (q1, q2) in enumerate(pairs)
    ]).to_csv(source, index=False)
    assert module.preprocess_file(
        source, output, skip_missing_queries=True, schema_column="schema",
        dbid="beers", source_name="beers",
    ) == (2, 4)
    result = pd.read_csv(output)
    assert tuple(result.columns) == module.COLUMNS
    assert result.source_index.tolist() == [24, 29]
    assert result["index"].tolist() == [1, 2]
    assert result.dbid.tolist() == ["beers"] * 2
    assert result.source.tolist() == ["beers"] * 2
    assert result.ground_truth.tolist() == ["NEQ"] * 2
