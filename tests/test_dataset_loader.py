import csv
from evebench.benchmark.loader import COLUMNS, read_dataset


def write(path, rows, columns=COLUMNS):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def row():
    return dict(
        dbid="db",
        index="1",
        schema_ddl="CREATE TABLE t (a INT);\n",
        q1=" SELECT a FROM t ",
        q2="SELECT a FROM t",
        dialect="postgresql",
        ground_truth="EQ",
    )


def test_preserves_source_text_and_dialect(tmp_path):
    path = tmp_path / "input.csv"
    write(path, [row()])
    assert read_dataset(path) == [row()]


def test_preserves_empty_sql_and_metadata(tmp_path):
    path = tmp_path / "input.csv"
    record = row() | {"q1": "", "schema_ddl": "", "source": "example"}
    write(path, [record], (*COLUMNS, "source"))
    assert read_dataset(path) == [record]


def test_preserves_duplicate_cases(tmp_path):
    path = tmp_path / "input.csv"
    write(path, [row(), row()])
    assert read_dataset(path) == [row(), row()]


def test_preserves_extra_columns(tmp_path):
    path = tmp_path / "input.csv"
    write(path, [row() | {"clean_ddl": "old"}], (*COLUMNS, "clean_ddl"))
    assert read_dataset(path) == [row() | {"clean_ddl": "old"}]
