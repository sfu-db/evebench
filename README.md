# EveBench

EveBench evaluates SQL query-equivalence cases with a common interface for
[SQLSolver](https://github.com/SJTU-IPADS/SQLSolver),
[SPES](https://github.com/georgia-tech-db/spes),
[VeriEQL](https://github.com/VeriEQL/VeriEQL), and
[ParSEval](https://github.com/sfu-db/ParSEval), and an optional
OpenAI-compatible LLM endpoint. It normalizes each backend's
answer, persists benchmark attempts in SQLite, and exposes the results through
a CLI summary, HTTP API, and web leaderboard.

## What is included

| Checker ID | Backend source | Preparation | Example source dialects |
| --- | --- | --- | --- |
| `sqlsolver` | `baselines/SQLSolver` submodule | Build a fat JAR | PostgreSQL, MySQL, SQLite |
| `spes` | `baselines/spes` submodule | Build an executable fat JAR | PostgreSQL, MySQL, SQLite |
| `qed` | `baselines/qed` | Build the parser JAR and Rust prover | PostgreSQL, MySQL, SQLite |
| `verieql` | `baselines/VeriEQL` submodule | No compilation | MySQL/MariaDB, PostgreSQL, Oracle |
| `parseval` | Python wheel pinned in `pyproject.toml` | Installed by `uv sync` | `sqlite` |
| `llm` | OpenAI-compatible Chat Completions endpoint | Set environment variables | `sqlite` |

The default benchmark profile is
`configs/benchmarks/bird_baseline.toml`. It evaluates 1,432 BIRD query pairs
with all four adapters.

## Prerequisites

The supported development environment is the included VS Code dev container.
It provides Python 3.11, `uv`, Java 17, and the Gradle versions needed by the
Java adapters.

For a host installation, provide:

- Linux x86-64 (the checked-in Z3 native libraries are Linux x86-64 binaries);
- Python 3.11 or later and `uv`;
- a Java 17 JDK; and
- Git with submodule support.

macOS and Windows users should use the dev container so that SQLSolver and SPES
can load the bundled native libraries. Node.js is present in the container but
is not required to build or run EveBench.

## Initialize the repository

Clone EveBench and all adapter submodules in one command:

```sh
git clone --recurse-submodules https://github.com/sfu-db/evebench.git evebench
cd evebench
```

If the repository was already cloned without submodules, initialize the pinned
adapter revisions from the repository root:

```sh
git submodule sync --recursive
git submodule update --init --recursive
```

To fetch adapters individually instead:

```sh
git submodule update --init baselines/SQLSolver
git submodule update --init baselines/spes
git submodule update --init baselines/VeriEQL
```

Use `git submodule update --init --recursive` rather than cloning the upstream
repositories directly. This keeps each adapter at the revision tested with
EveBench. Confirm that the requested submodules were initialized:

```sh
git submodule status
```

A leading `-` means that a submodule is still missing; a leading `+` means its
checkout differs from the revision pinned by EveBench.

### Use the dev container

The Compose configuration expects an external Docker network named
`shared_network`. Create it once if it does not already exist:

```sh
docker network create shared_network
```

Open the repository in VS Code and run
`Dev Containers: Reopen in Container`. Whether using the container or a
supported Linux host, run the remaining commands from the repository root.

### Install Python dependencies

From the repository root:

```sh
uv sync --frozen
cp .env.example .env
```

`uv sync` installs EveBench, ParSEval, and the Python packages used by the
VeriEQL adapter. VeriEQL does not need a `.pth` file: EveBench adds its
submodule root to the isolated VeriEQL subprocess automatically.

## Compile the adapters

Build SQLSolver first. SPES compiles against the Z3 Java and native libraries
stored in `baselines/SQLSolver/lib`.

### 1. SQLSolver

```sh
cd baselines/SQLSolver
./gradlew fatJar
cd ../..
```

The adapter expects:

```text
baselines/SQLSolver/build/libs/sqlsolver-v1.1.0.jar
baselines/SQLSolver/lib/libz3.so
baselines/SQLSolver/lib/libz3java.so
```

EveBench loads the JAR through JPype and calls
`sqlsolver.api.entry.Verification`; it does not invoke SQLSolver's CLI.

### 2. SPES

```sh
cd baselines/spes
./gradlew clean build
cd ../..
```

The resulting executable fat JAR is:

```text
baselines/spes/build/libs/equitas-1.0-SNAPSHOT.jar
```

EveBench launches this JAR as a child JVM and points it at
`baselines/SQLSolver/lib` for Z3.

### 3. VeriEQL and ParSEval

These adapters do not have a separate compile step:

- VeriEQL runs from `baselines/VeriEQL` in an isolated Python subprocess.
- ParSEval is installed from its pinned wheel by `uv sync`.

VeriEQL uses its vendored Python Z3 wrapper with the native Z3 libraries under
`baselines/SQLSolver/lib`.

### Verify all four adapters

Run the real-backend integration test after building the JARs:

```sh
uv run --env-file .env pytest tests/test_real_backends.py -q
```

This executes the same non-equivalent query pair through SQLSolver, SPES,
VeriEQL, and ParSEval. It also checks that VeriEQL and ParSEval return
counterexamples.
### QED parser
Build the qed parser with:
```sh
cd baselines/qed/parser
JAVA_HOME="$JAVA19_HOME" PATH="$JAVA19_HOME/bin:$PATH" \
  mvn clean package -DskipTests
```

Run it with:
```sh
"$JAVA19_HOME/bin/java" --enable-preview \
  --add-opens=java.base/java.lang.reflect=ALL-UNNAMED \
  -jar target/qed-parser-1.0-SNAPSHOT-jar-with-dependencies.jar \
  example.sql

```


### QED prover and adapter


The QED prover is available in `baselines/qed/prover`. The `qed` adapter runs
the parser on the schema and query pair, then passes the generated JSON to the
prover. It compiles PostgreSQL, MySQL, and SQLite inputs for its parser with `bag` semantics. A successful proof
returns `equivalent`; "not provable" returns `unknown`, not `not_equivalent`.
Both stages share the request timeout and use a temporary directory.
QED keeps foreign keys in the emitted schema by default. Its parser cannot
parse them, so cases with foreign keys can fail at the parser stage. For a
benchmark that intentionally ignores foreign keys, set
`backends.qed.options.strip_foreign_keys = true` in the benchmark TOML (or
`backend_options.qed.strip_foreign_keys = true` in an API request). Record this
choice with the benchmark results because it changes the schema QED sees.

Initialize its pinned source from the repository root:

```sh
git submodule update --init --recursive baselines/qed/prover
```
```sh
cargo +nightly build --release --manifest-path baselines/qed/prover/Cargo.toml
```

This produces `baselines/qed/prover/target/release/qed-prover`. Check that both
runtime solvers are on `PATH`, then run the bundled Calcite cases:

```sh
z3 --version
cvc5 --version
baselines/qed/prover/target/release/qed-prover baselines/qed/prover/tests/calcite/
```

You can also pass individual QED JSON files. Raw SQL must first be converted
with the [QED parser](baselines/qed/parser/README.md). Set `QED_SMT_TIMEOUT` to
change the per-SMT-request timeout in milliseconds (default: `10000`), or
`RUST_LOG=info` for verbose output.

Set `JAVA19_HOME` or `EVEBENCH_QED_JAVA` to select Java 19 for the adapter.
Select checker `qed` in API requests or pass `--checker qed` to the CLI.
Run `uv run pytest tests/test_qed.py -q` to test the adapter, including real
parser/prover checks when its build artifacts and runtime commands are present.

Common build errors:

- **`Unable to find libclang`**: install `libclang-dev`. If Clang is installed
  in a custom location, set `LIBCLANG_PATH` to the directory containing
  `libclang.so`; the variable cannot replace installing the library.
- **`E0554: #![feature] may not be used on the stable release channel`**: use
  `cargo +nightly` as above. The pinned prover still declares nightly feature
  attributes, which stable Rust rejects even when those features have stabilized.

## Benchmark dataset format

EveBench datasets are UTF-8 CSV files with one query-equivalence case per row.
The loader requires exactly these columns (column order is unrestricted):

| Column | Description |
| --- | --- |
| `dbid` | Identifier of the database schema used by the case. |
| `index` | Case identifier within `dbid`; EveBench records the case as `dbid:index`. |
| `schema_ddl` | Complete authoritative DDL used to create the schema. Multiple statements are separated by semicolons. |
| `q1` | First SQL query in the pair. |
| `q2` | Second SQL query in the pair. |
| `dialect` | Source SQL dialect for both schema and queries, e.g. `postgresql` or `sqlite`. |
| `ground_truth` | Reference equivalence label, normally `EQ` or `NEQ`. |

The header is therefore:

```text
dbid,index,schema_ddl,q1,q2,dialect,ground_truth
```

The `(dbid, index)` pair must be unique. SQL and DDL must use normal CSV
quoting when they contain commas, quotes, or newlines. Benchmark execution
uses `schema_ddl`; each adapter then performs its own dialect-specific
compilation. Unknown columns, empty values, duplicate cases, and labels other
than `EQ`/`NEQ` are rejected. No schema discovery or legacy fallback is performed.
`ground_truth` is persisted with the case for evaluation; it is not shown to
the verifier.

## Configure EveBench

The repository-relative defaults work after the setup above. `.env.example`
documents every override; copy it to `.env` and edit only the values you need.
Use absolute paths for custom adapter installations.

| Setting | Default |
| --- | --- |
| `EVEBENCH_SQLSOLVER_JAR` | `baselines/SQLSolver/build/libs/sqlsolver-v1.1.0.jar` |
| `EVEBENCH_SQLSOLVER_LIB` | `baselines/SQLSolver/lib` |
| `EVEBENCH_SQLSOLVER_WORKDIR` | `baselines/SQLSolver` |
| `EVEBENCH_SPES_JAR` | `baselines/spes/build/libs/equitas-1.0-SNAPSHOT.jar` |
| `EVEBENCH_SPES_LIB` | `baselines/SQLSolver/lib` |
| `EVEBENCH_SPES_WORKDIR` | `baselines/spes` |
| `EVEBENCH_QED_PARSER_JAR` | `baselines/qed/parser/target/qed-parser-1.0-SNAPSHOT-jar-with-dependencies.jar` |
| `EVEBENCH_QED_PROVER` | `baselines/qed/prover/target/release/qed-prover` |
| `EVEBENCH_QED_JAVA` | `$JAVA19_HOME/bin/java`, falling back to `EVEBENCH_JAVA` or `java` |
| `EVEBENCH_VERIEQL_ROOT` | `baselines/VeriEQL` |
| `EVEBENCH_VERIEQL_Z3_LIB` | `baselines/SQLSolver/lib` |
| `EVEBENCH_VERIEQL_BOUND_SIZE` | `2` |
| `EVEBENCH_RESULTS_DB` | `data/evebench.sqlite` |
| `EVEBENCH_MAX_WORKERS` | `4` |
| `EVEBENCH_MAX_TASKS_PER_WORKER` | `25` |

Additional overrides include `EVEBENCH_JAVA`,
`EVEBENCH_VERIEQL_PYTHON`, and the logging settings described in
`.env.example`.

The optional `llm` checker is registered when `EVEBENCH_LLM_MODEL` is set.
Configure `EVEBENCH_LLM_BASE_URL` for the provider and either
`EVEBENCH_LLM_API_KEY` or `OPENAI_API_KEY` when authentication is required.
Set `EVEBENCH_LLM_REASONING_EFFORT` to use reasoning-mode request parameters;
when it is omitted, the adapter uses ordinary completion parameters. The
default counterexample prompt can be replaced per run with
`backends.llm.options.prompt`.

### LLM verifier profile

Configure the endpoint in `.env`:

```dotenv
EVEBENCH_LLM_MODEL=your-model-name
EVEBENCH_LLM_BASE_URL=https://api.openai.com/v1
EVEBENCH_LLM_API_KEY=your-api-key
EVEBENCH_LLM_REASONING_EFFORT=medium
EVEBENCH_LLM_MAX_COMPLETION_TOKENS=4096
```

For a local endpoint that does not require authentication, leave
`EVEBENCH_LLM_API_KEY` empty. Add `llm` to the benchmark profile's `checkers`
array and give it a backend table:

```toml
checkers = ["sqlsolver", "spes", "verieql", "parseval", "llm"]

[backends.llm]

[backends.llm.options]
reasoning_effort = "medium"
max_completion_tokens = 4096
```

The `llm` options are `reasoning_effort`, `max_completion_tokens`,
`temperature`, and `prompt`. Profile options override the adapter defaults for
that run. Omit `reasoning_effort` from both `.env` and the profile for an
ordinary Chat Completions model; `temperature` is sent only in that mode. A
custom `prompt` must be a non-empty TOML string containing all three
placeholders: `{q1}`, `{q2}`, and `{schema}`.

The verifier asks the model for an executable, insert-only counterexample. A
valid completion is a JSON object containing exactly `explanation`,
`counterexample_dsp`, and `counterexample`. A valid response is normalized to
`NOT_EQUIVALENT`; malformed or unreachable responses are recorded as failed
attempts rather than equivalence evidence.

`EVEBENCH_SQLSOLVER_WORKDIR` must point to the directory containing
`sqlsolver.properties`. The checked-in file sets SQLSolver's internal Z3
timeout to 10,000 ms. The benchmark's outer per-checker deadline is configured
separately with `timeout_ms` in its TOML profile or `--timeout-ms` on the CLI.
SPES has no per-run adapter options. VeriEQL's `bound_size` and ParSEval's data-generation settings, live under their
respective `[backends.<checker>.options]` tables in the benchmark profile.
Set `strip_shared_order_by_limit = true` at the top level of a benchmark profile
to remove matching top-level `ORDER BY`, `LIMIT`, and `OFFSET` clauses from both
queries before checking them. When both queries have the same `LIMIT` and
`OFFSET` but different `ORDER BY` clauses, the orders remain. The default is
`false`; this option changes the queries being evaluated, so dataset ground-truth labels may
no longer apply.

EveBench does not load `.env` implicitly. The examples below use
`uv run --env-file .env ...` so that the file is applied consistently.

## Run the benchmark

Start with a small end-to-end run:

```sh
uv run --env-file .env python -m evebench benchmark --limit 10
```

Provide a dataset CSV or `--config`; no dataset is selected implicitly.
The example profile is
`configs/benchmarks/bird_baseline.toml`. That profile fixes:

- the generated BIRD dataset and SQLite result paths;
- checker order and backend options;
- the timeout, semantics, seed, and worker-pool settings; and
- VeriEQL bounds and ParSEval generation options.

At completion the CLI prints a summary like:

```text
backend       evaluated      eq     neq  unknown  timeout
sqlsolver            10   ...     ...      ...      ...
spes                 10   ...     ...      ...      ...
verieql              10   ...     ...      ...      ...
parseval              10   ...     ...      ...      ...

SQLite results: /path/to/evebench/data/evebench.sqlite (run 1)
```

Run the complete 1,432-case profile by removing `--limit`:

```sh
uv run --env-file .env python -m evebench benchmark
```

Results are committed after each case, so an interrupted run retains its
progress. Resume a full run with the run ID printed by its original invocation:

```sh
uv run --env-file .env python -m evebench benchmark --run-id 1
```

The dataset hash, timeout, selected checkers, dialects, backend options, and
execution options must match the original run. Repeat the same `--limit` when
resuming a limited run.

Useful development overrides include:

```sh
uv run --env-file .env python -m evebench benchmark \
  --limit 25 \
  --timeout-ms 5000 \
  --max-workers 2 \
  --checker sqlsolver \
  --checker parseval
```

Use another TOML profile with:

```sh
uv run --env-file .env python -m evebench benchmark \
  --config configs/benchmarks/another_dataset.toml
```

Profile paths are resolved relative to the profile file.

For a one-off CSV, pass its path positionally:

```sh
uv run --env-file .env python -m evebench benchmark path/to/cases.csv
```

Custom datasets should follow the canonical CSV format described above.

## View the results

The benchmark command always prints the four-way per-backend summary. It also
stores the complete run, cases, normalized results, diagnostics, timings,
metrics, raw backend output, and available witnesses in
`data/evebench.sqlite` by default.

Start the EveBench API with the same environment:

```sh
uv run --env-file .env python -m evebench
```

Then open:

- workbench: <http://localhost:8000/>
- benchmark leaderboard: <http://localhost:8000/leaderboard>
- OpenAPI documentation: <http://localhost:8000/docs>

In the workbench, select the source dialect once; adapters own target compilation.
ParSEval requires a disposable database URL such as
`sqlite:////tmp/evebench-case.db`; it deletes the named SQLite file, or drops
the named PostgreSQL/MySQL database, after every attempt.

The leaderboard reads the latest persisted run for each dataset from
`EVEBENCH_RESULTS_DB`. Results already committed by an active benchmark become
visible immediately after refreshing the page.

The same summary is available as JSON:

```sh
curl "http://localhost:8000/v1/benchmarks/summary"
curl "http://localhost:8000/v1/benchmarks/summary?run_id=1"
```

Check adapter availability and resolved capabilities with:

```sh
curl "http://localhost:8000/v1/checkers"
```

`unknown` in the leaderboard includes non-verdict outcomes such as unsupported
inputs and backend failures. Their original execution status, reason code, and
diagnostic message remain available in SQLite.


## Development

See [maintenance scripts](scripts/README.md) for dataset preprocessing, pair reduction,
and result analysis commands.

Run the full test suite:

```sh
uv run pytest
```

Preview the MkDocs site:

```sh
uv run mkdocs serve
```

Operational events are printed to the console. Detailed structured JSON Lines
are written to `logs/evebench.jsonl` by default, rotate at 10 MiB, and retain
five backups. Configure or disable file logging with the `EVEBENCH_LOG_*`
settings in `.env.example`; benchmark runs also accept `--log-file` and
`--console-log-level`.

The main project layout is:

```text
src/evebench/adapters/    checker integrations and input normalization
src/evebench/benchmark/   profile loading, execution, and SQLite persistence
src/evebench/api/         FastAPI application and HTTP routes
src/evebench/web/         workbench and leaderboard assets
configs/benchmarks/       reproducible benchmark profiles
datasets/sqlite/          bundled and generated BIRD data
baselines/                pinned adapter submodules
tests/                    unit and real-backend integration tests
```

### Adapter package boundaries

Each backend is a package under `src/evebench/adapters/`, containing its
configuration and lifecycle implementation. Backend schema compilers live in
`sqlsolver/schema.py`, `spes/schema.py`, `qed/schema.py`, and `verieql/schema.py`.
Shared source-DDL parsing lives in `adapters/common/schema/`; the loader and
runner never inspect its catalog. VeriEQL's subprocess runner and ParSEval's
database lifecycle live inside their respective packages.

The SQLGlot schema frontend folds separate `ALTER TABLE ... ADD CONSTRAINT`
statements into table definitions. Each adapter renders its backend's input
format directly. Adapters do not gate source dialects or preflight SQL features,
column types, or schema references; the backend determines whether it can process
the input and returns the result. Parsing and native-format encoding errors are
reported when conversion itself fails.

Configure a disposable `connection_string` in the ParSEval backend options
with an engine matching the dataset dialect. The bundled BIRD and PostgreSQL
profiles use SQLite and PostgreSQL URLs, respectively. The adapter removes
the named database after each attempt, so do not point it at an existing
database or share it between concurrent runs.
