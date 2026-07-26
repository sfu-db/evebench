# EveBench

EveBench evaluates SQL query-equivalence cases with a common interface for
[SQLSolver](https://github.com/SJTU-IPADS/SQLSolver),
[SPES](https://github.com/georgia-tech-db/spes),
[VeriEQL](https://github.com/VeriEQL/VeriEQL), and
[ParSEval](https://github.com/sfu-db/ParSEval). It normalizes each backend's
answer, persists benchmark attempts in SQLite, and exposes the results through
a CLI summary, HTTP API, and web leaderboard.

## What is included

| Checker ID | Backend source | Preparation | Default dialect |
| --- | --- | --- | --- |
| `sqlsolver` | `baselines/SQLSolver` submodule | Build a fat JAR | `calcite` |
| `spes` | `baselines/spes` submodule | Build an executable fat JAR | `calcite` |
| `verieql` | `baselines/VeriEQL` submodule | No compilation | `calcite` |
| `parseval` | Python wheel pinned in `pyproject.toml` | Installed by `uv sync` | `sqlite` |

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
git clone --recurse-submodules <repository-url> evebench
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
EveBench. Confirm that all three were initialized:

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
| `EVEBENCH_VERIEQL_ROOT` | `baselines/VeriEQL` |
| `EVEBENCH_VERIEQL_Z3_LIB` | `baselines/SQLSolver/lib` |
| `EVEBENCH_VERIEQL_BOUND_SIZE` | `2` |
| `EVEBENCH_RESULTS_DB` | `data/evebench.sqlite` |
| `EVEBENCH_MAX_WORKERS` | `4` |
| `EVEBENCH_MAX_TASKS_PER_WORKER` | `25` |

Additional overrides include `EVEBENCH_JAVA`,
`EVEBENCH_VERIEQL_PYTHON`, and the logging settings described in
`.env.example`.

`EVEBENCH_SQLSOLVER_WORKDIR` must point to the directory containing
`sqlsolver.properties`. The checked-in file sets SQLSolver's internal Z3
timeout to 10,000 ms. The benchmark's outer per-checker deadline is configured
separately with `timeout_ms` in its TOML profile or `--timeout-ms` on the CLI.
SPES has no per-run adapter options. VeriEQL's `bound_size` and
`use_constraints`, and ParSEval's data-generation settings, live under their
respective `[backends.<checker>.options]` tables in the benchmark profile.

EveBench does not load `.env` implicitly. The examples below use
`uv run --env-file .env ...` so that the file is applied consistently.

## Run the benchmark

Start with a small end-to-end run:

```sh
uv run --env-file .env python -m evebench benchmark --limit 10
```

With no dataset or `--config` argument, the CLI loads
`configs/benchmarks/bird_baseline.toml`. That profile fixes:

- the generated BIRD dataset and SQLite result paths;
- checker order and per-checker SQL dialect;
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

The CSV must contain `dbid`, `index`, `q1`, and `q2`, plus either
`schema_ddl` or `clean_ddl`. An optional `ground_truth` column is persisted
with each case.

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

In the workbench, use the `calcite` dialect override for SQLSolver and SPES.
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

## Dataset maintenance

`datasets/sqlite/bird_benchmark.csv` combines query pairs and labels from
`bird_baseline.csv` with the authoritative full SQLite DDL from
`bird_dev_schema.json`. Rebuild it deterministically after either source
changes:

```sh
uv run python scripts/build_bird_dataset.py
```

Benchmark execution uses `schema_ddl`. Each adapter performs its own
backend-specific normalization; for example, Calcite adapters remove foreign
keys that their parsers cannot consume. `clean_ddl` is retained only for
comparison with earlier reduced-schema evaluations.

## Development

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
