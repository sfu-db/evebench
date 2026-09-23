import pandas as pd
import pytest

from scripts.datasets import preprocess_leetcode_dataset as module


def test_filters_both_queries_and_preserves_source_data(tmp_path):
    source, output = tmp_path / "input.csv", tmp_path / "output.csv"
    pairs = [
        ("SELECT DISTINCT `a` FROM `t`", "SELECT DISTINCT `a` + 1 FROM `t`"),
        ("SELECT FROM", "SELECT 1"),
        ("SELECT 1", "SELECT FROM"),
        ("select 1 -- comment", "SELECT 1"),
        ("", "SELECT 1"),
        ("SELECT DISTINCT `a` FROM `t`", "SELECT DISTINCT `a` + 1 FROM `t`"),
        ("SELECT 1; SELECT 2", "SELECT 3"),
        ("DELETE FROM t", "SELECT 1"),
        ("SELECT DISTINCT unknown_function(a) FROM t", "SELECT DISTINCT a FROM t"),
    ]
    pd.DataFrame([
        dict(dbid="1084", index=str(i + 10), schema="CREATE TABLE t(a INT);",
             q1=q1, q2=q2, ground_truth="NEQ", verieql_label="EQU")
        for i, (q1, q2) in enumerate(pairs)
    ]).to_csv(source, index=False)
    assert module.preprocess_file(source, output) == (2, 7)
    result = pd.read_csv(output, dtype=str)
    assert tuple(result.columns) == module.COLUMNS
    assert result["index"].tolist() == ["1", "2"]
    assert result.source_index.tolist() == ["10", "18"]
    assert result.dbid.tolist() == ["1084"] * 2
    assert result.dialect.tolist() == ["mysql"] * 2
    assert result.source.tolist() == ["leetcode"] * 2
    assert result.ground_truth.tolist() == ["NEQ"] * 2
    assert result.q1.iloc[0] == pairs[0][0]
    assert result.schema_ddl.iloc[0] == "CREATE TABLE t(a INT);"


def test_parser_failure_preserves_output(tmp_path, monkeypatch):
    source, output = tmp_path / "input.csv", tmp_path / "output.csv"
    pd.DataFrame([dict(dbid="1", index="1", schema="CREATE TABLE t(a INT);",
                       q1="SELECT 1", q2="SELECT 2", ground_truth="EQ")]).to_csv(source, index=False)
    output.write_text("existing output")
    monkeypatch.setenv("EVEBENCH_QED_PARSER_JAR", str(tmp_path / "missing.jar"))
    with pytest.raises(ValueError, match="QED parser JAR not found"):
        module.preprocess_file(source, output)
    assert output.read_text() == "existing output"


@pytest.mark.parametrize("query", [
    "SELECT ROW_NUMBER() OVER (ORDER BY a) FROM t",
    "SELECT SUM(a) OVER () FROM t",
    "SELECT SUM(a) OVER w FROM t WINDOW w AS (ORDER BY a)",
    "SELECT * FROM (SELECT RANK() OVER (ORDER BY a) AS r FROM t) s",
])
@pytest.mark.parametrize("side", ["q1", "q2"])
def test_window_pairs_removed(tmp_path, query, side):
    source, output = tmp_path / "input.csv", tmp_path / "output.csv"
    row = dict(dbid="1", index="1", schema="CREATE TABLE t(a INT);",
               q1="SELECT 1", q2="SELECT 2", ground_truth="NEQ")
    row[side] = query
    pd.DataFrame([row]).to_csv(source, index=False)
    assert module.preprocess_file(source, output) == (0, 1)
    assert pd.read_csv(output).empty


@pytest.mark.parametrize("query", [
    "SELECT SUM(a) FROM t",
    "SELECT 'ROW_NUMBER() OVER ()'",
    "SELECT `over` FROM t",
    "SELECT a /* SUM(a) OVER () */ FROM t -- OVER w",
])
def test_window_detection_ignores_non_window_uses(query):
    assert not module.contains_window_function(query)


def test_complex_only_filter_and_parse_failure():
    simple = "SELECT a FROM t WHERE a > 1"
    complex_query = "SELECT a FROM t WHERE a IN (SELECT a FROM u)"
    assert module.simple_pair_reason(simple, simple)
    assert module.simple_pair_reason(simple, complex_query)
    assert module.simple_pair_reason(complex_query, simple)
    assert module.simple_pair_reason(complex_query, complex_query) is None
    assert module.simple_pair_reason("SELECT FROM", complex_query) == "complexity_analysis_failed"


def test_default_complexity_filter_preserves_complex_pair(tmp_path):
    source, output = tmp_path / "input.csv", tmp_path / "output.csv"
    pd.DataFrame([
        dict(dbid="1", index=str(i), schema="CREATE TABLE t(a INT);",
             q1="SELECT DISTINCT a FROM t", q2=q2, ground_truth="NEQ")
        for i, q2 in enumerate([
            "SELECT a + 1 FROM t",
            "SELECT a FROM t WHERE a IN (SELECT a FROM t WHERE a > 1)",
        ])
    ]).to_csv(source, index=False)
    assert module.preprocess_file(source, output) == (1, 1)
    result = pd.read_csv(output)
    assert result.source_index.tolist() == [1]
    assert result["index"].tolist() == [1]


def test_calcite_identifier_quoting_preserves_units_and_original_sql(tmp_path):
    source, output = tmp_path / "input.csv", tmp_path / "output.csv"
    pairs = [
        ("SELECT DISTINCT ACTIVITY_DATE AS DAY FROM ACTIVITY",
         "SELECT DISTINCT ACTIVITY_DATE AS DAY FROM ACTIVITY WHERE ACTIVITY_DATE > '2019-07-27' - INTERVAL 29 DAY"),
        ("SELECT DISTINCT O1.FREE FROM CINEMA O1 WHERE O1.FREE = 1",
         "SELECT DISTINCT FREE FROM CINEMA WHERE FREE = 0"),
    ]
    queries = [query for pair in pairs for query in pair]
    rendered = [module.query_for_calcite(query) for query in queries]
    assert "AS `DAY`" in rendered[1]
    assert "INTERVAL '29' DAY" in rendered[1] or "INTERVAL 29 DAY" in rendered[1]
    assert module.calcite_syntax_errors(rendered, mysql=True) == [None] * 4
    literal = "SELECT 'FREE DAY' AS DAY"
    assert "'FREE DAY'" in module.query_for_calcite(literal)
    pd.DataFrame([
        dict(dbid="1", index=str(i), schema="CREATE TABLE t(a INT);",
             q1=q1, q2=q2, ground_truth="NEQ")
        for i, (q1, q2) in enumerate(pairs)
    ]).to_csv(source, index=False)
    assert module.preprocess_file(source, output) == (2, 0)
    result = pd.read_csv(output)
    assert list(zip(result.q1, result.q2)) == pairs


@pytest.mark.parametrize("query", ["SELECT FROM", "SELECT 1; SELECT 2", "DELETE FROM t"])
def test_calcite_rendering_does_not_hide_invalid_input(query):
    assert module.query_for_calcite(query) == query


def test_optional_sampling_is_part_of_preprocessing(tmp_path, monkeypatch):
    source, output = tmp_path / "input.csv", tmp_path / "output.csv"
    pairs = [
        ("SELECT a FROM t", "SELECT b FROM t", "NEQ"),
        ("SELECT b FROM t", "SELECT a FROM t", "NEQ"),
        ("SELECT a FROM t", "SELECT c FROM t", "NEQ"),
        ("SELECT a FROM t", "SELECT d FROM t", "NEQ"),
        ("SELECT a FROM t", "SELECT e FROM t", "EQ"),
        ("SELECT e FROM t", "SELECT a FROM t", "NEQ"),
        ("SELECT FROM", "SELECT 1", "NEQ"),
    ]
    pd.DataFrame([
        dict(dbid="1", index=str(i), schema="DDL", q1=q1, q2=q2, ground_truth=label)
        for i, (q1, q2, label) in enumerate(pairs)
    ]).to_csv(source, index=False)
    monkeypatch.setattr(
        module, "calcite_syntax_errors", lambda queries, mysql: [None] * len(queries),
    )
    monkeypatch.setattr(module, "simple_pair_reason", lambda q1, q2: None)

    assert module.preprocess_file(source, output, max_per_group=2) == (5, 2)
    result = pd.read_csv(output)
    assert result.source_index.tolist() == [0, 2, 4, 5, 6]
    assert result["index"].tolist() == [1, 2, 3, 4, 5]

    assert module.preprocess_file(source, output, max_per_group=0) == (6, 1)
