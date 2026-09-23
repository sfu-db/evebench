import pytest
from evebench.adapters.qed.schema import compile_schema as compile_qed
from evebench.adapters.spes.schema import compile_schema as compile_spes
from evebench.adapters.sqlsolver.schema import compile_schema as compile_sqlsolver


@pytest.fixture(params=[compile_qed, compile_spes, compile_sqlsolver],
                ids=["qed", "spes", "sqlsolver"])
def compile_schema(request):
    return request.param


def test_preserves_separate_primary_key_alter(compile_schema):
    result = compile_schema(
        "CREATE TABLE t (id INT); ALTER TABLE t ADD PRIMARY KEY (id);", "postgresql"
    )
    assert "PRIMARY KEY (id)" in result
    assert "ALTER TABLE" not in result


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE t (v VARCHAR);",
        "CREATE TABLE p (id INT PRIMARY KEY); CREATE TABLE t (id INT REFERENCES p(id));",
        "CREATE TABLE t (id INT CHECK (id > 0));",
    ],
)
def test_renders_constraints_and_types_for_backend(compile_schema, ddl):
    import sqlglot

    result = compile_schema(ddl, "mysql")
    if compile_schema is compile_qed and "REFERENCES" in ddl:
        assert "REFERENCES" in result
        assert "FOREIGN KEY" in result
        return
    if compile_schema is compile_sqlsolver and "v VARCHAR" in ddl:
        assert "v TEXT" in result
        return
    assert sqlglot.parse(result, read="mysql") == sqlglot.parse(ddl, read="mysql")


def test_parses_postgresql_character_varying(compile_schema):
    result = compile_schema(
        "CREATE TABLE t (label character varying(40));", "postgresql"
    )
    assert "VARCHAR(40)" in result


def test_compilation_does_not_mutate_source_catalog(compile_schema):
    from evebench.adapters.common.schema import parse_schema

    ddl = "CREATE TABLE MixedCase (ID INT PRIMARY KEY);"
    assert "MixedCase" in compile_schema(ddl, "postgresql")
    source = parse_schema(ddl, "postgresql")
    assert source.tables[0].this.this.name == "MixedCase"
    assert source.tables[0].this.expressions[0].name == "ID"


def test_sqlsolver_folds_postgres_alter_only_and_added_columns():
    ddl = (
        "CREATE TABLE t (id INT);"
        "ALTER TABLE ONLY t ADD COLUMN label VARCHAR(40);"
        "ALTER TABLE ONLY t ADD CONSTRAINT t_pk PRIMARY KEY (id);"
    )
    result = compile_sqlsolver(ddl, "postgresql")
    assert "ALTER TABLE" not in result
    assert "label VARCHAR(40)" in result
    assert "CONSTRAINT t_pk PRIMARY KEY (id)" in result


def test_sqlsolver_keeps_unfoldable_alter_actions():
    result = compile_sqlsolver(
        "CREATE TABLE t (id INT, old INT); "
        "ALTER TABLE ONLY t ADD COLUMN label VARCHAR(40); "
        "ALTER TABLE ONLY t DROP COLUMN old;",
        "postgresql",
    )
    assert "label VARCHAR(40)" in result
    assert "ALTER TABLE t DROP COLUMN old" in result
    assert "ADD COLUMN label" not in result


def test_sqlsolver_converts_unbounded_varchar_without_changing_bounded_varchar():
    result = compile_sqlsolver(
        "CREATE TABLE t (a character varying, b character varying(40));",
        "postgresql",
    )
    assert "a TEXT" in result
    assert "b VARCHAR(40)" in result


def test_spes_folds_added_columns_and_strips_foreign_keys_on_request():
    ddl = (
        "CREATE TABLE parent (id INT PRIMARY KEY);"
        "CREATE TABLE child (pid INT REFERENCES parent(id), "
        "other INT, FOREIGN KEY (other) REFERENCES parent(id));"
        "ALTER TABLE ONLY child ADD COLUMN extra INT REFERENCES parent(id);"
        "ALTER TABLE ONLY child ADD CONSTRAINT child_fk FOREIGN KEY (pid) REFERENCES parent(id);"
    )
    retained = compile_spes(ddl, "postgresql")
    assert "REFERENCES" in retained
    assert "extra INT" in retained

    stripped = compile_spes(ddl, "postgresql", strip_foreign_keys=True)
    assert "REFERENCES" not in stripped
    assert "FOREIGN KEY" not in stripped
    assert "pid INT" in stripped
    assert "extra INT" in stripped
    assert "PRIMARY KEY" in stripped


def test_spes_folds_constraints_and_keeps_unfoldable_alters():
    result = compile_spes(
        "CREATE TABLE t (id INT, old INT); "
        "ALTER TABLE ONLY t ADD CONSTRAINT t_pk PRIMARY KEY (id); "
        "ALTER TABLE ONLY t DROP COLUMN old;",
        "postgresql",
    )
    assert "CONSTRAINT t_pk PRIMARY KEY (id)" in result
    assert "ALTER TABLE t DROP COLUMN old" in result
    assert "ADD CONSTRAINT" not in result
