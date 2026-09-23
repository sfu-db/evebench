import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import errno
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import time

import httpx
import psycopg2
from psycopg2 import sql
from sqlglot import exp, parse

from validate_counterexample import VALIDATION_POLICY, validate_counterexample


ROOT = Path(__file__).resolve().parent
MODELS = ("gpt-5.6-sol", "gpt-5.6-luna", "gemini-3.7-flash")
PG_BIN = Path("/opt/homebrew/opt/postgresql@16/bin")
DATABASE_LOCKS = {}


def require_disk_space(path):
    free = shutil.disk_usage(path).free
    if free < 2 * 1024**3:
        raise OSError(errno.ENOSPC,
                      f"Less than 2 GiB free ({free / 1024**3:.2f} GiB); resume after freeing disk space")


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")
    temporary.replace(path)


def load_rows(path, limit):
    csv.field_size_limit(10000000)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    if limit:
        rows = rows[:limit]
    if not rows or any(row["dialect"] != "postgres" for row in rows):
        raise ValueError("Expected nonempty PostgreSQL query pairs")
    return rows


def add_csv_constraints(cursor, schema):
    statements = [statement for statement in parse(schema, read="postgres")
                  if statement is not None and statement.key in {"alter", "altertable"}]
    statements.sort(key=lambda statement: statement.find(exp.PrimaryKey) is None)
    added_primary_keys = []
    added_foreign_keys = []
    for statement in statements:
        primary_key = statement.find(exp.PrimaryKey)
        foreign_key = statement.find(exp.ForeignKey)
        if ((primary_key is None and foreign_key is None)
                or any(action.key != "addconstraint" for action in statement.args.get("actions", []))):
            raise ValueError("Expected only primary-key or foreign-key ADD CONSTRAINT statements")
        table = statement.this.sql(dialect="postgres")
        if primary_key is not None:
            cursor.execute("SELECT 1 FROM pg_constraint WHERE contype='p' AND conrelid=to_regclass(%s)",
                           (table,))
            if cursor.fetchone() is None:
                cursor.execute(statement.sql(dialect="postgres"))
                added_primary_keys.append(table)
        else:
            constraint = statement.find(exp.Constraint)
            if constraint is None:
                raise ValueError("Expected a named foreign-key constraint")
            name = constraint.name if constraint.this.args.get("quoted") else constraint.name.lower()
            cursor.execute("SELECT 1 FROM pg_constraint WHERE conname=%s AND conrelid=to_regclass(%s)",
                           (name, table))
            if cursor.fetchone() is None:
                cursor.execute(statement.sql(dialect="postgres"))
                added_foreign_keys.append(name)
    return added_primary_keys, added_foreign_keys


def prepare_databases(rows, output):
    schemas = {}
    for row in rows:
        if row["dbid"] in schemas and schemas[row["dbid"]] != row["schema_ddl"]:
            raise ValueError(f"Multiple CSV schemas for {row['dbid']}")
        schemas[row["dbid"]] = row["schema_ddl"]
    manifest_path = output / "databases.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for dbid, schema in schemas.items():
        target = f"{dbid}_ce_test"
        if len(target.encode()) > 63:
            raise ValueError("Database identifier exceeds PostgreSQL's limit")
        digest = hashlib.sha256(schema.encode()).hexdigest()
        connection = psycopg2.connect(dbname="postgres", connect_timeout=10)
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target,))
                exists = cursor.fetchone() is not None
                if exists:
                    if manifest.get(dbid, {}).get("csv_schema_sha256") != digest:
                        raise ValueError(f"Refusing to reuse unrecognized existing database {target}")
                else:
                    source_path = output / f"{dbid}_source_schema.sql"
                    with source_path.open("wb") as stream:
                        subprocess.run([str(PG_BIN / "pg_dump"), "--schema-only", "--no-owner",
                                        "--no-privileges", "--dbname", dbid], stdout=stream, check=True)
                    cursor.execute("SELECT pg_encoding_to_char(encoding), datcollate, datctype "
                                   "FROM pg_database WHERE datname=%s", (dbid,))
                    encoding, collate, ctype = cursor.fetchone()
                    cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING {} "
                                           "LC_COLLATE {} LC_CTYPE {}").format(
                        sql.Identifier(target), sql.Literal(encoding),
                        sql.Literal(collate), sql.Literal(ctype)))
                    subprocess.run([str(PG_BIN / "psql"), "-X", "-w", "--set", "ON_ERROR_STOP=1",
                                    "--single-transaction", "--dbname", target, "--file", str(source_path)],
                                   check=True, stdout=subprocess.DEVNULL)
                    with psycopg2.connect(dbname=target) as test_connection:
                        with test_connection.cursor() as test_cursor:
                            added_primary_keys, added_foreign_keys = add_csv_constraints(test_cursor, schema)
                    manifest[dbid] = {"target": target, "source": dbid,
                                      "csv_schema_sha256": digest,
                                      "added_csv_primary_keys": added_primary_keys,
                                      "added_csv_foreign_keys": added_foreign_keys,
                                      "source_schema_file": source_path.name}
                    write_json(manifest_path, manifest)
        finally:
            connection.close()
        with psycopg2.connect(dbname=target) as test_connection:
            with test_connection.cursor() as cursor:
                cursor.execute("SELECT schemaname, tablename FROM pg_tables "
                               "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')")
                for namespace, table in cursor.fetchall():
                    cursor.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {}.{})").format(
                        sql.Identifier(namespace), sql.Identifier(table)))
                    if cursor.fetchone()[0]:
                        raise ValueError(f"Test database {target} is not empty")
        DATABASE_LOCKS[dbid] = threading.Lock()
        primary_count = len(manifest[dbid].get("added_csv_primary_keys", []))
        foreign_count = len(manifest[dbid]["added_csv_foreign_keys"])
        print(f"Ready: {target}; added CSV primary keys: {primary_count}; foreign keys: {foreign_count}", flush=True)
    return manifest


def render_prompt(template, row):
    replacements = {"{q1}": row["q1"], "{q2}": row["q2"], "{schema}": row["schema_ddl"]}
    return re.sub(r"\{q1\}|\{q2\}|\{schema\}", lambda match: replacements[match.group()], template)


def parse_response(text, prompt_name):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
    response = json.loads(text)
    if not isinstance(response, dict):
        raise ValueError("Expected a JSON object")
    if prompt_name == "DF" and response.get("equiv") not in ("Y", "N"):
        raise ValueError("DF response requires equiv=Y or N")
    if prompt_name == "DF" and response["equiv"] == "Y":
        return response
    if not isinstance(response.get("counterexample"), str):
        raise ValueError("Expected a counterexample SQL string")
    if prompt_name == "DF" and not response["counterexample"].strip():
        raise ValueError("DF inequivalence response requires nonempty counterexample SQL")
    return response


def restore_empty_counterexample(result, prompt_name):
    if (prompt_name != "CE" or result.get("validation_status") != "generation_error"
            or result.get("finish_reason") != "stop"):
        return result
    try:
        parsed = parse_response(result["raw_response"], prompt_name)
    except ValueError:
        return result
    if not parsed["counterexample"].strip():
        for key in ("counterexample", "explanation", "counterexample_dsp", "equiv"):
            result[key] = parsed.get(key, "")
        result.update(validation_status="no_counterexample", validation_error="")
    return result


def generate(client, model, prompt, max_tokens):
    if model.startswith("gpt-5.6-"):
        response = client.post("/responses", json={
            "model": model, "input": [{"role": "user", "content": prompt}],
            "max_output_tokens": max_tokens, "stream": False,
        })
        response.raise_for_status()
        payload = response.json()
        text = "".join(part.get("text", "") for item in payload.get("output", [])
                       if item.get("type") == "message" for part in item.get("content", [])
                       if part.get("type") == "output_text")
        finish_reason = "stop" if payload.get("status") == "completed" else "length"
        payload["choices"] = [{"message": {"content": text}, "finish_reason": finish_reason}]
        return payload, text
    response = client.post("/chat/completions", json={
        "model": model, "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "stream": False,
    })
    response.raise_for_status()
    payload = response.json()
    message = payload["choices"][0]["message"]
    return payload, message.get("content") or ""


def write_csv(path, results):
    if not results:
        return
    fields = list(dict.fromkeys(key for result in results for key in result))
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, default=str)
                             if isinstance(value, (dict, list, tuple)) else value
                             for key, value in result.items()})
    temporary.replace(path)


def run_group(model, prompt_name, rows, options):
    group = f"{prompt_name}_{model}"
    checkpoint_dir = options.output / "checkpoints" / group
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    template = (ROOT / f"{prompt_name}.txt").read_text()
    results = []
    with httpx.Client(base_url=options.base_url.rstrip("/") + "/",
                      timeout=httpx.Timeout(options.api_timeout, connect=15),
                      trust_env=False) as client:
        for position, row in enumerate(rows, 1):
            checkpoint = checkpoint_dir / f"{position:04d}.json"
            cached = checkpoint.exists()
            if cached:
                saved_result = json.loads(checkpoint.read_text())
                result = restore_empty_counterexample(saved_result.copy(), prompt_name)
                if (result.get("counterexample", "").strip()
                        and not (prompt_name == "DF" and result.get("equiv") == "Y")
                        and (options.revalidate_only
                             or result.get("validation_policy") != VALIDATION_POLICY)):
                    with DATABASE_LOCKS[row["dbid"]]:
                        result.update(validate_counterexample(
                            row["dbid"], row["q1"], row["q2"], result["counterexample"],
                            timeout_ms=options.sql_timeout_ms))
                if result != saved_result:
                    write_json(checkpoint, result)
            else:
                if options.revalidate_only:
                    raise ValueError(f"Missing checkpoint: {checkpoint}; no model requests allowed")
                require_disk_space(options.output)
                result = dict(row, model=model, prompt=prompt_name, counterexample="",
                              counterexample_valid="N", validation_status="generation_error",
                              validation_error="", explanation="", counterexample_dsp="", equiv="",
                              raw_response="", api_model="", finish_reason="", usage={},
                              started_at=datetime.now(timezone.utc).isoformat())
                started = time.monotonic()
                try:
                    payload, text = generate(client, model, render_prompt(template, row), options.max_tokens)
                    result.update(raw_response=text, api_model=payload.get("model", ""),
                                  finish_reason=payload["choices"][0].get("finish_reason", ""),
                                  usage=payload.get("usage", {}))
                    if result["finish_reason"] == "length":
                        raise ValueError("Model response was truncated at the output-token limit")
                    parsed = parse_response(text, prompt_name)
                    for key in ("counterexample", "explanation", "counterexample_dsp", "equiv"):
                        result[key] = parsed.get(key, "")
                    if prompt_name == "DF" and parsed["equiv"] == "Y":
                        result["validation_status"] = "declared_equivalent"
                    elif not result["counterexample"].strip():
                        result["validation_status"] = "no_counterexample"
                    else:
                        with DATABASE_LOCKS[row["dbid"]]:
                            result.update(validate_counterexample(
                                row["dbid"], row["q1"], row["q2"], result["counterexample"],
                                timeout_ms=options.sql_timeout_ms))
                except Exception as error:
                    result["validation_error"] = f"{type(error).__name__}: {error}"
                result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                write_json(checkpoint, result)
            results.append(result)
            flush_results = (not cached or position == len(rows)
                             or not (checkpoint_dir / f"{position + 1:04d}.json").exists())
            if flush_results:
                write_csv(options.output / f"postgres_{group}.csv", results)
                phase = "restored " if cached else ""
                print(f"{group} {phase}{position}/{len(rows)} index={row['index']} "
                      f"{result['validation_status']} ({result.get('elapsed_seconds', 0)}s)", flush=True)
    counts = Counter(result["validation_status"] for result in results)
    generated = sum(bool(result["counterexample"]) for result in results)
    valid = sum(result["counterexample_valid"] == "Y" for result in results)
    summary = {"model": model, "prompt": prompt_name, "pairs": len(rows),
               "validation_policy": VALIDATION_POLICY,
               "shared_limit_removed_pairs": sum(result.get("shared_limit_removed", "") != ""
                                                  for result in results),
               "generated_counterexamples": generated, "valid": valid,
               "valid_rate_all_pairs": valid / len(rows),
               "valid_rate_generated": valid / generated if generated else None,
               "status_counts": dict(counts),
               "valid_labeled_EQ": sum(result["counterexample_valid"] == "Y" and
                                        result["ground_truth"] == "EQ" for result in results),
               "valid_labeled_NEQ": sum(result["counterexample_valid"] == "Y" and
                                         result["ground_truth"] == "NEQ" for result in results),
               "label_conflict_indices": [result["index"] for result in results
                                          if result["counterexample_valid"] == "Y"
                                          and result["ground_truth"] == "EQ"]}
    write_json(options.output / f"report_{group}.json", summary)
    return summary


def run_model(model, rows, options):
    return [run_group(model, prompt_name, rows, options) for prompt_name in ("CE", "DF")]


def run_groups(rows, options):
    summaries = []
    with ThreadPoolExecutor(max_workers=options.workers) as executor:
        futures = {executor.submit(run_group, model, prompt_name, rows, options): f"{prompt_name}_{model}"
                   for prompt_name in ("CE", "DF") for model in MODELS}
        for future in as_completed(futures):
            try:
                summaries.append(future.result())
            except Exception as error:
                print(f"Group failed: {futures[future]}: {type(error).__name__}: {error}", flush=True)
                raise
    return summaries


def report_scope(rows, options):
    selection = "All" if options.limit == 0 else "First"
    databases = ", ".join(f"{name}: {count}" for name, count in sorted(
        Counter(row["dbid"] for row in rows).items()))
    labels = ", ".join(f"{name}: {count}" for name, count in sorted(
        Counter(row["ground_truth"] for row in rows).items()))
    command = shlex.join([
        "/opt/anaconda3/envs/9217/bin/python", "Sep2026/run_counterexamples.py",
        "--input", str(options.input), "--output", str(options.output),
        "--limit", str(options.limit), "--base-url", options.base_url,
        "--max-tokens", str(options.max_tokens), "--sql-timeout-ms", str(options.sql_timeout_ms),
        "--revalidate-only",
    ])
    return (f"# Counterexamples: {selection} {len(rows)} Query Pairs",
            f"Selected pairs: {len(rows)}. Databases: {databases}. Original labels: {labels}.",
            command)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "postgres.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "pilot20")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--base-url", default="http://127.0.0.1:4142/v1")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--api-timeout", type=int, default=600)
    parser.add_argument("--sql-timeout-ms", type=int, default=15000)
    parser.add_argument("--workers", type=int, choices=range(1, 7), default=3,
                        help="Concurrent model/prompt groups (1-6); database validation stays serialized per dbid")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--revalidate-only", action="store_true",
                        help="Revalidate saved counterexamples without any model requests")
    options = parser.parse_args()
    if options.limit < 0:
        parser.error("--limit must be nonnegative; 0 means all pairs")
    rows = load_rows(options.input, options.limit)
    if options.revalidate_only:
        for model in MODELS:
            for prompt_name in ("CE", "DF"):
                for position in range(1, len(rows) + 1):
                    checkpoint = options.output / "checkpoints" / f"{prompt_name}_{model}" / f"{position:04d}.json"
                    if not checkpoint.exists():
                        raise ValueError(f"Missing checkpoint: {checkpoint}; no model requests allowed")
    options.output.mkdir(parents=True, exist_ok=True)
    require_disk_space(options.output)
    manifest = {
        "input_sha256": hashlib.sha256(options.input.read_bytes()).hexdigest(),
        "rows": [{key: row[key] for key in ("dbid", "index", "source_index")} for row in rows],
        "models": MODELS, "prompts": ["CE", "DF"], "base_url": options.base_url,
        "max_tokens": options.max_tokens, "sql_timeout_ms": options.sql_timeout_ms,
        "prompt_sha256": {name: hashlib.sha256((ROOT / f"{name}.txt").read_bytes()).hexdigest()
                          for name in ("CE", "DF")},
        "comparison": "bag: full rows with duplicates; output order ignored",
        "schema_policy": "Clone source schema, then add missing CSV foreign keys to test DB only",
    }
    run_path = options.output / "run.json"
    if run_path.exists() and json.loads(run_path.read_text()) != json.loads(json.dumps(manifest)):
        raise ValueError("Run configuration differs from existing output; use a new output directory")
    write_json(run_path, manifest)
    prepare_databases(rows, options.output)
    if options.prepare_only:
        return
    print(f"Running six model/prompt groups with {options.workers} workers; per-database validation locks enabled", flush=True)
    summaries = run_groups(rows, options)
    write_json(options.output / "summary.json", summaries)
    write_csv(options.output / "summary.csv", summaries)
    title, scope, revalidate_command = report_scope(rows, options)
    lines = [title, "",
             "Each of three models receives the same query pairs with CE and DF separately.",
             "Source databases are unchanged. Test databases contain schema only plus missing CSV primary and foreign keys.",
             "Validity requires successful INSERTs with constraints enabled and different result bags.",
             "Before execution, matching outer LIMIT N clauses (nonnegative integer literals) are removed from both queries.",
             "Unequal or one-sided limits, nested limits, and OFFSET clauses remain unchanged; original q1/q2 are preserved.",
             "shared_limit_removed records N; q1_validation_sql/q2_validation_sql record the queries actually executed.",
             "Errors are not evidence of inequivalence. Equal outputs do not prove equivalence.",
             "No counterexample (N) includes omitted counterexamples and declared equivalence; it does not mean proven equivalence.",
             "no_counterexample means CE returned an empty SQL string, with the model's explanation retained.",
             "generation_error means an API or response-format failure; error means SQL validation failed.",
             "No automatic retries or SQL repairs were performed in this run.",
             scope,
             "Result CSVs include original columns, counterexample, counterexample_valid, status, errors, and raw replies.",
             "Outputs store at most 100 preview rows; comparisons use up to 100,000 complete rows.", "",
             "| Prompt | Model | Pairs | Generated | Valid | Valid / Pairs | Status Counts |",
             "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for summary in sorted(summaries, key=lambda item: (item["prompt"], item["model"])):
        lines.append(f"| {summary['prompt']} | {summary['model']} | {summary['pairs']} | "
                     f"{summary['generated_counterexamples']} | {summary['valid']} | "
                     f"{summary['valid_rate_all_pairs']:.1%} | {summary['status_counts']} |")
    conflicts = sorted({index for summary in summaries for index in summary["label_conflict_indices"]}, key=int)
    lines.extend(["", "## Label Conflicts", "",
                  "Counterexamples are valid under the shared-LIMIT removal policy despite an existing EQ label for indices: " + ", ".join(conflicts) + ".",
                  "When a LIMIT is removed, validity describes the modified query pair, not necessarily the original limited pair.",
                  "Original ground_truth values are preserved. These are schema-only counterexamples; dataset-specific",
                  "assumptions such as calendar surrogate keys matching actual date offsets are not imposed.",
                  "", "## Reproduce", "", "```sh",
                  revalidate_command, "```",
                  f"Revalidation requires all checkpoints and makes no model requests. The independent API is {options.base_url}."])
    (options.output / "report.md").write_text("\n".join(lines) + "\n")
    print(f"Completed. Reports: {options.output}", flush=True)


if __name__ == "__main__":
    main()