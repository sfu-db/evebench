"""Batch syntax checks using the Calcite parser bundled with QED."""
from pathlib import Path
import json
import os
import subprocess

from scripts.paths import ROOT

def _calcite_errors(inputs: list[str], *, statement_lists=False, mysql=False) -> list[str | None]:
    jar = Path(os.environ.get(
        "EVEBENCH_QED_PARSER_JAR",
        str(ROOT / "baselines/qed/parser/target/qed-parser-1.0-SNAPSHOT-jar-with-dependencies.jar"),
    ))
    if not jar.is_file():
        raise ValueError(f"QED parser JAR not found: {jar}; build baselines/qed/parser first")
    command = [os.environ.get("EVEBENCH_QED_JAVA", "java"), "--class-path", str(jar),
               str(Path(__file__).with_name("CalciteSyntaxCheck.java"))]
    if statement_lists:
        command.append("--statements")
    if mysql:
        command.append("--mysql")
    result = subprocess.run(
        command, input=json.dumps(inputs), capture_output=True, text=True,
        timeout=120, check=True,
    )
    errors = json.loads(result.stdout)
    if not isinstance(errors, list) or len(errors) != len(inputs) or any(
        error is not None and not isinstance(error, str) for error in errors
    ):
        raise ValueError("Invalid output from Calcite syntax checker")
    return errors


def calcite_syntax_errors(queries: list[str], *, mysql=False) -> list[str | None]:
    return _calcite_errors(queries, mysql=mysql)


def calcite_schema_errors(schemas: list[str]) -> list[str | None]:
    return _calcite_errors(schemas, statement_lists=True)
