import pandas as pd
import pytest


import scripts.datasets.preprocess_sqlite_datasets as module
from scripts.datasets.shared import features, portability


@pytest.mark.parametrize("query, expected", [
    ("SELECT a FROM t WHERE b = 1", False),
    ("SELECT CAST(a AS REAL) / b FROM t WHERE c IS NULL", False),
    ("SELECT a FROM t WHERE (b = 1 OR c = 2) AND d = 3", True),
    ("SELECT * FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.id = t.id)", True),
])
def test_complex_query_criteria(query, expected):
    assert features.is_complex(features.query_features(query)[0]) is expected


@pytest.mark.parametrize("first, second, remove", [
    ("SELECT 1", "SELECT 2", True),
    ("SELECT DISTINCT a FROM t", "SELECT 1", True),
    ("SELECT 1", "SELECT DISTINCT a FROM t", True),
    ("SELECT DISTINCT a FROM t", "SELECT DISTINCT b FROM t", False),
])
def test_only_pairs_complex_on_both_sides_are_kept(first, second, remove):
    details = features.analyze_pair(first, second)
    assert features.pair_is_too_simple(details)[0] is remove


def test_postgres_distinct_order_by_rule_is_separate_from_parsing():
    assert portability.postgres_order_by_error("SELECT a FROM t ORDER BY b") is None
    assert portability.postgres_order_by_error("SELECT DISTINCT a FROM t ORDER BY a") is None
    assert portability.postgres_order_by_error("SELECT DISTINCT a AS x FROM t ORDER BY x") is None
    assert portability.postgres_order_by_error("SELECT DISTINCT a FROM t ORDER BY 1") is None
    assert "must appear in select list" in portability.postgres_order_by_error(
        "SELECT DISTINCT a FROM t ORDER BY b"
    )


def test_calcite_translation_requotes_identifiers_without_changing_dialect():
    translated = portability.translate_query_for_calcite(
        "SELECT `School Name`, rank FROM schools WHERE `date` != '2020-01-01'"
    )
    assert "`" not in translated
    assert '"School Name"' in translated
    assert '"rank"' in translated
    assert '"date" <> ' in translated
    assert "STRFTIME" in portability.translate_query_for_calcite(
        "SELECT strftime('%Y', `date`) FROM events"
    )


def test_calcite_parses_preprocessed_schema_statement_lists():
    schema, _ = portability.clean_schema_constraints(
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (pid INT REFERENCES p(id));", mode="all",
    )
    translated = portability.translate_schema_for_calcite(schema)
    assert module.calcite_schema_errors([translated]) == [None]


def test_schema_cleanup_defaults_to_foreign_keys_only():
    ddl = (
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (id INT DEFAULT 1 NOT NULL UNIQUE, pid INT REFERENCES p(id), "
        "FOREIGN KEY (pid) REFERENCES p(id), CHECK (id > 0));"
    )
    foreign_only, removed = portability.clean_schema_constraints(ddl)
    assert removed == 2
    assert "REFERENCES" not in foreign_only
    assert "FOREIGN KEY" not in foreign_only
    assert "PRIMARY KEY" in foreign_only
    assert "NOT NULL" in foreign_only
    assert "UNIQUE" in foreign_only
    assert "CHECK" in foreign_only

    unconstrained, removed_all = portability.clean_schema_constraints(ddl, mode="all")
    assert removed_all > removed
    for constraint in ("REFERENCES", "PRIMARY KEY", "NOT NULL", "UNIQUE", "CHECK", "DEFAULT"):
        assert constraint not in unconstrained


def test_reduce_file_filters_and_reindexes(tmp_path):
    source = tmp_path / "sqlite.csv"
    output = tmp_path / "reduced.csv"
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
            "q1": "SELECT t.a FROM t LEFT JOIN u ON t.id = u.id WHERE u.x > 1",
            "q2": "SELECT t.a FROM t LEFT JOIN u ON t.id = u.id WHERE u.x >= 2",
            "dialect": "sqlite", "ground_truth": "EQ", "source": "bird",
        },
    ]
    pd.DataFrame(rows).to_csv(source, index=False)

    assert module.reduce_file(source, output, require_calcite=False) == (1, 1)
    result = pd.read_csv(output)
    assert result["index"].tolist() == [1]
    assert result["source_index"].tolist() == [90]
    assert result.loc[0, "q1"].startswith("SELECT t.a")
    assert result.loc[0, "schema_ddl"] == rows[1]["schema_ddl"]


def test_calcite_failure_removes_whole_pair(tmp_path, monkeypatch):
    source, output = tmp_path / "in.csv", tmp_path / "out.csv"
    pd.DataFrame([{
        "index": 1, "schema_ddl": "CREATE TABLE t (a INT);",
        "q1": "SELECT DISTINCT a + 1 FROM t", "q2": "SELECT DISTINCT rank + 1 FROM t",
        "dialect": "sqlite", "ground_truth": "NEQ",
    }]).to_csv(source, index=False)
    monkeypatch.setattr(module, "calcite_syntax_errors", lambda queries: [None, "bad keyword"])
    monkeypatch.setattr(module, "calcite_schema_errors", lambda schemas: [None] * len(schemas))

    assert module.reduce_file(source, output) == (0, 1)


def test_reduce_file_validates_paths_and_columns(tmp_path):
    source = tmp_path / "sqlite.csv"
    pd.DataFrame([{"index": 1}]).to_csv(source, index=False)
    with pytest.raises(ValueError, match="output must differ"):
        module.reduce_file(source, source)
    with pytest.raises(ValueError, match="unsupported constraint mode"):
        portability.clean_schema_constraints("", mode="unknown")
    with pytest.raises(KeyError, match="q1"):
        module.reduce_file(source, tmp_path / "out.csv")


def test_bird_sampling_after_filters_preserves_context_and_query_text(tmp_path, monkeypatch):
    source, output = (tmp_path / name for name in ("in.csv", "out.csv"))
    query = "SELECT t.a FROM t LEFT JOIN u ON t.a = u.a WHERE t.a > {value}"
    base = dict(dbid="bird_db", schema_ddl="CREATE TABLE t(a INT); CREATE TABLE u(a INT);",
                dialect="sqlite", ground_truth="NEQ", source="bird")
    pairs = [(query.format(value=1), query.format(value=2)),
             (query.format(value=2), query.format(value=1)),
             (query.format(value=3), query.format(value=4)),
             (query.format(value=5), query.format(value=6)),
             (query.format(value=7), query.format(value=8))]
    rows = [{**base, "index": str(i + 10), "source_index": str(i + 100), "q1": q1, "q2": q2}
            for i, (q1, q2) in enumerate(pairs)]
    # Different label, database, and schema must receive independent allocations.
    rows.extend([
        {**rows[3], "index": "15", "ground_truth": "EQ",
         "q1": query.format(value=9), "q2": query.format(value=10)},
        {**rows[3], "index": "16", "dbid": "another_db"},
        {**rows[3], "index": "17", "schema_ddl": base["schema_ddl"] + " CREATE TABLE v(a INT);"},
        {**rows[3], "index": "18", "q1": "SELECT a FROM t", "q2": "SELECT a FROM u"},
    ])
    pd.DataFrame(rows).to_csv(source, index=False)
    original = source.read_bytes()
    # Reject the first complex pair for portability. It must not consume a slot
    # or cause its reversed pair to be treated as a duplicate of a removed row.
    monkeypatch.setattr(module, "calcite_syntax_errors", lambda queries: ["bad", None] + [None] * (len(queries) - 2))
    monkeypatch.setattr(module, "calcite_schema_errors", lambda schemas: [None] * len(schemas))
    assert module.reduce_file(source, output, max_per_group=2) == (5, 4)
    result = pd.read_csv(output, dtype=str)
    assert result.q1.tolist() == [rows[i]["q1"] for i in [1, 2, 5, 6, 7]]
    assert result["index"].tolist() == ["1", "2", "3", "4", "5"]
    assert result.schema_ddl.tolist() == [rows[i]["schema_ddl"] for i in [1, 2, 5, 6, 7]]
    assert source.read_bytes() == original


def test_bird_sampling_dedup_conflicts_and_disabled_mode(tmp_path):
    source, output = (tmp_path / name for name in ("in.csv", "out.csv"))
    base = dict(dbid="db", schema_ddl="CREATE TABLE t(a INT);", ground_truth="NEQ",
                q1="SELECT DISTINCT a FROM t", q2="SELECT DISTINCT a + 1 FROM t")
    rows = [{**base, "index": "1"},
            {**base, "index": "2", "q1": base["q2"], "q2": base["q1"]},
            {**base, "index": "3", "q2": "SELECT DISTINCT a + 2 FROM t"},
            {**base, "index": "4", "q2": "SELECT DISTINCT a + 2 FROM t", "ground_truth": "EQ"}]
    pd.DataFrame(rows).to_csv(source, index=False)
    kwargs = dict(require_calcite=False)
    assert module.reduce_file(source, output, **kwargs) == (4, 0)
    assert module.reduce_file(source, output, max_per_group=0, **kwargs) == (3, 1)
    with pytest.raises(ValueError, match="nonnegative"):
        module.reduce_file(source, output, max_per_group=-1, **kwargs)


def test_bird_sampling_with_no_eligible_pairs(tmp_path):
    source, output = (tmp_path / name for name in ("in.csv", "out.csv"))
    pd.DataFrame([dict(dbid="db", index="1", schema_ddl="CREATE TABLE t(a INT);",
                       q1="SELECT 1", q2="SELECT 2", ground_truth="NEQ")]).to_csv(source, index=False)
    assert module.reduce_file(source, output, require_calcite=False, max_per_group=2) == (0, 1)
    assert pd.read_csv(output).empty
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.csv", "out.csv"]
