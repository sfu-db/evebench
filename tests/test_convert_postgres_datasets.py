import pandas as pd
import psycopg2

import scripts.datasets.convert_postgres_datasets as module


def test_normalize_query_removes_comments_and_preserves_quoted_double_dashes():
    query = "SELECT '-- text', \"-- column\" -- explanation\nFROM t -- trailing\nWHERE a = 1"
    assert module.normalize_query(query) == (
        "SELECT '-- text', \"-- column\" FROM t WHERE a = 1"
    )


def test_normalized_queries_match_checks_every_statement():
    assert module.normalize_query("select 1; SELECT 2") == module.normalize_query(
        "SELECT 1 ; select 2"
    )
    assert module.normalize_query("SELECT 1; SELECT 2") != module.normalize_query(
        "SELECT 1; SELECT 3"
    )


def test_convert_files_drops_failing_pairs_and_skips_imdb(tmp_path, monkeypatch):
    source = tmp_path / "source.csv"
    output = tmp_path / "result.csv"
    pd.DataFrame([
        dict(db_id="imdb_job", val_id="0", equiv="True", original_sql="bad", rewritten_sql="bad"),
        dict(db_id="tpch", val_id="1", equiv="True", original_sql="SELECT 1", rewritten_sql="SELECT 2"),
        dict(db_id="tpch", val_id="2", equiv="False", original_sql="SELECT bad", rewritten_sql="SELECT 3"),
        dict(db_id="tpch", val_id="3", equiv="True", original_sql="SELECT 4", rewritten_sql="SELECT bad"),
        dict(db_id="tpch", val_id="4", equiv="True", original_sql="SELECT 5", rewritten_sql="SELECT 6"),
        dict(db_id="tpch", val_id="5", equiv="True", original_sql="select 7", rewritten_sql="SELECT   7"),
    ]).to_csv(source, index=False)

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, query):
            executed.append(query)
            if query == "SELECT bad":
                raise psycopg2.ProgrammingError("invalid query")

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            closed.append(True)

    executed, closed, dsns = [], [], []

    def connect(dsn):
        dsns.append(dsn)
        return Connection()

    monkeypatch.setattr(psycopg2, "connect", connect)
    assert module.convert_files(
        [source],
        {"tpch": "CREATE TABLE t (a int)"},
        output,
        connection_string="postgresql://localhost/{dbid}",
    ) == (2, 4)
    assert dsns == ["postgresql://localhost/postgres"]
    assert [query for query in executed if query.startswith("SELECT")] == [
        "SELECT 1", "SELECT 2", "SELECT bad", "SELECT 4", "SELECT bad",
        "SELECT 5", "SELECT 6",
    ]
    assert closed == [True]
    result = pd.read_csv(output)
    assert result["index"].tolist() == [1, 2]
    assert result["source_index"].tolist() == [1, 4]
