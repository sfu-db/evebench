"""Prepare table DDL for ParSEval's database schema loader."""

from __future__ import annotations

from sqlglot import exp

from evebench.adapters.common.schema import parse_schema


def compile_schema(schema_ddl: str, dialect: str) -> str:
    catalog = parse_schema(schema_ddl, dialect)
    relax_character_lengths = dialect == "postgres"
    if not catalog.alters and not relax_character_lengths:
        return schema_ddl

    write = {"postgresql": "postgres", "mariadb": "mysql"}.get(dialect, dialect)
    statements: list[exp.Expression] = []
    tables: dict[str, exp.Create] = {}
    for statement in catalog.statements:
        if statement in catalog.tables:
            table = statement.copy()
            tables[table.this.this.sql(dialect=write).lower()] = table
            statements.append(table)
        elif isinstance(statement, exp.AlterTable):
            table = tables.get(statement.this.sql(dialect=write).lower())
            remaining: list[exp.Expression] = []
            for action in statement.args.get("actions") or []:
                if table is not None and isinstance(action, exp.ColumnDef):
                    table.this.append("expressions", action.copy())
                elif table is not None and isinstance(action, exp.AddConstraint):
                    for constraint in action.expressions:
                        table.this.append("expressions", constraint.copy())
                elif (
                    table is not None
                    and isinstance(action, exp.Anonymous)
                    and action.name.upper() == "UNIQUE"
                ):
                    table.this.append(
                        "expressions",
                        exp.UniqueColumnConstraint(
                            this=exp.Schema(expressions=[item.copy() for item in action.expressions])
                        ),
                    )
                else:
                    remaining.append(action.copy())
            if remaining:
                alter = statement.copy()
                alter.set("actions", remaining)
                statements.append(alter)
        else:
            statements.append(statement.copy())
    if relax_character_lengths:
        for statement in statements:
            for column in statement.find_all(exp.ColumnDef):
                datatype = column.args.get("kind")
                if datatype is not None and datatype.this in {
                    exp.DataType.Type.CHAR,
                    exp.DataType.Type.VARCHAR,
                    exp.DataType.Type.NCHAR,
                    exp.DataType.Type.NVARCHAR,
                } and datatype.expressions:
                    column.set("kind", exp.DataType.build("TEXT"))
    return ";\n".join(statement.sql(dialect=write) for statement in statements) + ";"
