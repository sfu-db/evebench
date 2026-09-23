import pytest
from sqlglot import exp
from evebench.adapters.common.schema import parse_schema
from evebench.adapters.parseval.schema import compile_schema as compile_parseval_schema
from evebench.adapters.verieql.schema import compile_verieql_schema, parse_verieql_ddl


def test_separate_constraints_preserved_and_cache_isolated():
    ddl = "CREATE TABLE p (id INT); ALTER TABLE ONLY p ADD CONSTRAINT pk PRIMARY KEY (id); CREATE TABLE c (pid INT); ALTER TABLE c ADD FOREIGN KEY (pid) REFERENCES p(id);"
    catalog = parse_schema(ddl, "postgresql")
    assert len(catalog.statements) == 4
    assert isinstance(catalog.statements[1], exp.AlterTable)
    assert isinstance(catalog.statements[3], exp.AlterTable)
    assert not catalog.statements[0].find(exp.PrimaryKey)
    assert not catalog.statements[2].find(exp.ForeignKey)
    assert len(catalog.tables) == 2
    assert catalog.alters == (catalog.statements[1], catalog.statements[3])
    assert catalog.other_statements == ()
    assert catalog.statements[1].find(exp.PrimaryKey)
    assert catalog.statements[3].find(exp.ForeignKey)
    catalog.tables[0].this.set("expressions", [])
    assert parse_schema(ddl, "postgresql").tables[0].find(exp.ColumnDef)
    native = parse_verieql_ddl(ddl, "postgresql")
    assert {"primary": [{"value": "P__ID"}]} in native.constraints
    assert any("foreign" in c for c in native.constraints)


def test_verieql_compiles_catalog_without_mutating_source():
    ddl = (
        "CREATE TABLE p (id INT PRIMARY KEY);"
        "CREATE TABLE c (pid INT REFERENCES p(id));"
    )
    catalog = parse_schema(ddl, "postgresql")
    native = compile_verieql_schema(catalog)

    assert native == parse_verieql_ddl(ddl, "postgresql")
    assert len(catalog.tables) == 2
    assert catalog.tables[0].find(exp.PrimaryKeyColumnConstraint)
    native.tables["P"]["ID"] = "VARCHAR"
    assert compile_verieql_schema(catalog).tables["P"]["ID"] == "INT"


def test_constraints_remain_in_alter_statements():
    ddl = "CREATE TABLE t (a INT); ALTER TABLE t ADD COLUMN b INT; ALTER TABLE t ADD PRIMARY KEY (b);"
    catalog = parse_schema(ddl, "postgresql")
    assert [type(statement) for statement in catalog.statements] == [
        exp.Create, exp.AlterTable, exp.AlterTable
    ]
    assert not catalog.statements[0].find(exp.PrimaryKey)
    assert not catalog.tables[0].find(exp.PrimaryKey)
    assert catalog.statements[2].find(exp.PrimaryKey)


def test_catalog_groups_udf_and_alter():
    ddl = "CREATE TABLE t (a INT); CREATE FUNCTION f(x INT) RETURNS INT AS $$ SELECT x + 1 $$ LANGUAGE SQL; ALTER TABLE t ADD PRIMARY KEY (a);"
    catalog = parse_schema(ddl, "postgresql")
    assert len(catalog.statements) == 3
    assert len(catalog.tables) == 1
    assert catalog.alters == (catalog.statements[2],)
    assert catalog.functions == (catalog.statements[1],)
    assert catalog.other_statements == ()
    assert "CREATE FUNCTION f" in catalog.functions[0].sql()
    assert isinstance(catalog.alters[0], exp.AlterTable)


def test_functions_include_sqlglot_create_and_command_forms():
    ddl = "CREATE FUNCTION f(x INT) RETURNS INT RETURN x + 1; CREATE OR REPLACE FUNCTION g(x INT) RETURNS INT AS $$ SELECT x + 1 $$ LANGUAGE SQL;"
    catalog = parse_schema(ddl, "postgresql")
    assert len(catalog.functions) == 2
    assert isinstance(catalog.functions[0], exp.Create)
    assert isinstance(catalog.functions[1], exp.Command)


def test_statement_groups_share_asts_and_cover_every_statement():
    catalog = parse_schema(
        "CREATE TABLE t (a INT); ALTER TABLE t ADD PRIMARY KEY (a); CREATE VIEW v AS SELECT a FROM t;",
        "postgresql",
    )
    assert catalog.tables[0] is catalog.statements[0]
    assert catalog.alters[0] is catalog.statements[1]
    assert catalog.other_statements[0] is catalog.statements[2]
    assert len(catalog.statements) == sum(map(len, (
        catalog.tables, catalog.alters, catalog.functions, catalog.other_statements
    )))


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE t (a INT); ALTER TABLE t ADD PRIMARY KEY (missing);",
        "CREATE TABLE t (a INT); CREATE TABLE t (b INT);",
        "CREATE TABLE t (a INT, a INT);",
        "CREATE TABLE t (a INT); DROP TABLE t;",
        "CREATE TABLE IF NOT EXISTS t (a INT DEFAULT 1);",
    ],
)
def test_parser_leaves_schema_validation_to_backend(ddl):
    assert parse_schema(ddl, "postgresql").statements


@pytest.mark.parametrize(
    "kind", ["TIMESTAMP", "DECIMAL(15,2)", "VARCHAR(12)", "CHAR(5)", "BIGINT"]
)
def test_verieql_passes_types_to_backend(kind):
    parsed = parse_verieql_ddl(f"CREATE TABLE t (a {kind});", "postgresql")
    assert parsed.tables["T"]["A"] == exp.DataType.build(kind).sql(dialect="mysql")


def test_composite_fk_is_not_silently_split_during_encoding():
    parsed = parse_verieql_ddl(
        "CREATE TABLE p (a INT, b INT, PRIMARY KEY(a,b)); CREATE TABLE c (a INT, b INT, FOREIGN KEY(a,b) REFERENCES p(a,b));",
        "postgresql",
    )
    assert any("unsupported_schema" in constraint for constraint in parsed.constraints)
    assert not any("foreign" in constraint for constraint in parsed.constraints)


def test_parser_preserves_quoted_punctuation_defaults_and_nested_expressions():
    catalog = parse_schema(
        """-- Commas and semicolons inside tokens are not delimiters.
        CREATE TABLE "odd;table" (
            "id,column" INTEGER PRIMARY KEY,
            label VARCHAR(40) DEFAULT 'a,b;)',
            amount DECIMAL(12, 2),
            CHECK (amount IN (1, 2))
        );""",
        "postgresql",
    )
    table, = catalog.tables
    assert table.this.this.name == "odd;table"
    columns = list(table.find_all(exp.ColumnDef))
    assert [column.name for column in columns] == ["id,column", "label", "amount"]
    assert columns[1].find(exp.DefaultColumnConstraint).this.this == "a,b;)"
    assert len(columns[2].args["kind"].expressions) == 2
    assert table.find(exp.CheckColumnConstraint).find(exp.In)


def test_parser_ignores_commented_out_ddl():
    catalog = parse_schema(
        "CREATE TABLE t (a TEXT DEFAULT '-- keep');\n"
        "-- ALTER TABLE t ADD PRIMARY KEY (missing);\n"
        "/* ALTER TABLE t ADD PRIMARY KEY (also_missing); */\n"
        "ALTER TABLE t ADD PRIMARY KEY (a);",
        "postgresql",
    )
    assert len(catalog.statements) == 2
    assert len(catalog.alters) == 1
    assert "-- keep" in catalog.tables[0].sql()
    assert "missing" not in " ".join(statement.sql() for statement in catalog.statements)


def test_unknown_source_dialect_is_not_aliased():
    with pytest.raises(ValueError, match="Unknown dialect"):
        parse_schema("CREATE TABLE t (a INT);", "calcite")


def test_parseval_relaxes_postgres_character_lengths():
    ddl = (
        "CREATE TABLE t (state CHAR(2), name VARCHAR(40), note TEXT, amount DECIMAL(12,2));"
        "ALTER TABLE t ADD COLUMN code CHARACTER VARYING(3);"
    )
    compiled = compile_parseval_schema(ddl, "postgres")
    assert "state TEXT" in compiled
    assert "name TEXT" in compiled
    assert "code TEXT" in compiled
    assert "note TEXT" in compiled
    assert "DECIMAL(12, 2)" in compiled
    assert "CHAR(2)" in ddl


def test_parseval_relaxes_character_lengths_without_alters():
    compiled = compile_parseval_schema(
        "CREATE TABLE t (state CHAR(2), name VARCHAR(40));", "postgres"
    )
    assert "state TEXT" in compiled
    assert "name TEXT" in compiled
