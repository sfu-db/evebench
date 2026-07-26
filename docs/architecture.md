# EveBench architecture

EveBench runs heterogeneous SQL query-equivalence checkers behind one domain
contract. The same execution path serves interactive HTTP checks and
reproducible CSV benchmark runs.

## Goals

EveBench is designed to:

1. give every checker the same query pair, schema, SQL semantics, and deadline;
2. distinguish a checker's verdict from failures in executing that checker;
3. retain diagnostics, raw output, metrics, and counterexamples;
4. make benchmark runs resumable with an exact configuration snapshot; and
5. expose comparable four-way outcome summaries through the CLI, API, and
   leaderboard.

FastAPI and Pydantic are confined to the HTTP boundary. The application,
adapters, benchmark runner, and result store exchange framework-independent
domain dataclasses.

## System overview

```text
Interactive client
      |
      v
FastAPI routes -------> CheckService -------> CheckerRegistry
                                                |
Benchmark CLI ---> BenchmarkRunner -------------+
      |                                         |
      v                                         v
BenchmarkConfig                           PooledChecker
                                                |
                                                v
                                      shared Pebble process pool
                                                |
                                                v
                                      complete worker-side adapter
                                  preprocess -> run -> postprocess
                                                |
                      +-------------------------+-------------------------+
                      |              |              |          |         |
                      v              v              v          v         v
                  SQLSolver         SPES         VeriEQL    ParSEval    LLM

BenchmarkRunner -----------------> ResultStore -----------------> SQLite
FastAPI / leaderboard -------------> summary queries ----------------^
```

All registered checkers use the same lazily started Pebble process pool. A hard
pool deadline can terminate and replace a worker that is stuck in Java, Z3,
CPU-bound Python, a child process, or a remote request.

Interactive results are returned directly and are not persisted. Benchmark
results are committed to SQLite after each dataset case.

## Domain contract

### Check input

```python
@dataclass(frozen=True, slots=True)
class EquivalenceCase:
    query_left: str
    query_right: str
    schema_ddl: str
    dialect: str
    semantics: str
    case_id: str | None = None
    parameters: dict[str, JSONValue] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class CheckOptions:
    timeout_ms: int = 30_000
    memory_limit_mb: int | None = None
    seed: int | None = None
    backend_options: dict[str, dict[str, JSONValue]] = field(
        default_factory=dict
    )
```

`parameters` is reserved for bound query parameters. Current adapters reject
non-empty parameters. Backend-specific settings belong in `backend_options`,
keyed by stable checker ID.

The HTTP schema accepts a timeout from 100 to 300,000 ms. The
`memory_limit_mb` field is reserved but rejected because the current process
pool does not enforce a memory limit.

### Normalized result

The result separates the backend conclusion from EveBench's ability to execute
the backend:

```python
@dataclass(frozen=True, slots=True)
class CheckResult:
    checker: CheckerRef
    execution_status: ExecutionStatus
    verdict: Verdict | None
    duration_ms: int
    started_at: datetime
    finished_at: datetime
    reason_code: str | None = None
    message: str | None = None
    witness: dict[str, JSONValue] | None = None
    metrics: dict[str, int | float | str | bool | None] = field(
        default_factory=dict
    )
    raw_output: str | None = None
```

Execution statuses are `completed`, `timeout`, `unsupported`,
`invalid_input`, and `failed`. Normal verdicts are `equivalent`,
`not_equivalent`, and `unknown`. A timeout or execution failure normally has no
verdict.

Examples:

- a successful SQLSolver `EQ` becomes `completed` / `equivalent`;
- a backend `UNKNOWN` becomes `completed` / `unknown`;
- a ParSEval mismatch becomes `completed` / `not_equivalent` and may include
  result rows as a witness;
- unsupported SQL features become `unsupported` with no verdict when the
  adapter can identify them; and
- a hard process-pool deadline becomes `timeout` with reason
  `worker_timeout`.

`reason_code` is intended for stable filtering. `message` and `raw_output`
retain human-readable and backend-native diagnostics.

## Adapter lifecycle

Worker-side adapters implement a synchronous three-phase lifecycle:

```python
class BackendAdapter(Protocol[PreparedInput, NativeOutput]):
    @property
    def descriptor(self) -> CheckerDescriptor: ...

    def preprocess(
        self,
        case: EquivalenceCase,
        options: CheckOptions,
    ) -> PreparedInput: ...

    def run(self, prepared: PreparedInput) -> NativeOutput: ...

    def postprocess(
        self,
        native: NativeOutput,
        *,
        started_at: datetime | None = None,
    ) -> CheckResult: ...
```

The shared lifecycle driver calls these phases in order:

1. `preprocess()` validates common input, validates backend options, and
   translates the schema or queries when required;
2. `run()` invokes the native checker; and
3. `postprocess()` maps native output to `CheckResult`.

`AdapterInputError` and `AdapterLifecycleError` carry an execution status,
reason code, message, and optional raw output. Unexpected errors are classified
by phase as `preprocess_error`, `backend_error`, or `postprocess_error`.

The API process holds only a `PooledChecker`: a descriptor, a picklable adapter
configuration, and a reference to the shared pool. The worker entry point
constructs the complete adapter for every task and executes its lifecycle.
This keeps backend imports, native libraries, JVM state, and blocking work out
of the API event loop.

`CheckService` validates selected checker IDs and fans out one coroutine per
checker with `asyncio.gather`. A checker failure is returned as an independent
result and does not cancel successful sibling checks.

### Registry and configuration

The checker registry is built at application or benchmark startup. Clients
select stable IDs; they cannot provide arbitrary import paths.

SQLSolver, SPES, VeriEQL, and ParSEval are always registered, with descriptor
availability reflecting required local artifacts. The optional `llm` checker
is registered only when `EVEBENCH_LLM_MODEL` is non-empty. Environment
variables configure backend installations and LLM connection details.
Benchmark TOML profiles select checkers, dialects, and validated per-backend
options.

Every descriptor contains:

- checker ID, display name, backend version, and adapter version;
- supported dialects and bag/set semantics;
- execution mode; and
- availability plus an unavailable reason.

## Backend integrations

| Checker | Native execution | Dialects | Semantics |
| --- | --- | --- | --- |
| `sqlsolver` | JPype call into an embedded JVM | `calcite` | bag |
| `spes` | executable fat JAR in a child JVM | `calcite` | bag |
| `verieql` | isolated Python subprocess | Calcite, MySQL/MariaDB, PostgreSQL, Oracle | bag |
| `parseval` | direct Python `disprove()` call | SQLite, MySQL, PostgreSQL | bag and set |
| `llm` | OpenAI-compatible Chat Completions HTTP request | SQLite, MySQL, PostgreSQL, Calcite | bag and set |

### SQLSolver and SPES

Both Calcite adapters use a shared SQLGlot preprocessing step. It validates the
case, removes foreign keys that the vendored parsers cannot consume, removes
unsupported inline constraint names, and adds a default length to bare
`VARCHAR` columns.

SQLSolver loads its Z3 native libraries, starts one JVM per pool worker when
needed, and invokes `sqlsolver.api.entry.Verification` through JPype. Its native
`EQ`, `NEQ`, `UNKNOWN`, and `TIMEOUT` values are normalized directly.

SPES launches its executable JAR for each attempt and reads the last JSON object
from standard output. A subprocess timeout is set slightly below the outer pool
deadline so normal timeout diagnostics can be returned before the worker is
terminated.

### VeriEQL

VeriEQL must use its vendored Python Z3 wrapper and native Z3 version, so it
runs in an isolated Python subprocess inside the pool worker. SQLGlot converts
DDL into VeriEQL's table/type dictionary and primary-key, foreign-key, and
not-null constraints.

`backend_options.verieql` accepts:

- `bound_size`, an integer from 1 to 100; and
- `use_constraints`, a boolean controlling whether parsed constraints are
  passed to the verifier.

Generated counterexamples are stored as normalized witnesses.

### ParSEval

ParSEval calls its public `disprove()` API directly in the pool worker.
Generation settings are passed through
`backend_options.parseval` after validation against `GenerationConfig`; the
shared benchmark seed overrides its generation seed.

ParSEval requires a disposable database connection. During CSV benchmarks,
`BenchmarkRunner` creates a unique temporary SQLite path for each case/checker
attempt and injects the connection string. Interactive API callers must provide
`backend_options.parseval.connection_string`.

The adapter removes or drops the disposable database in a `finally` path and
redacts the connection string from errors and raw results. If a hard pool
timeout prevents normal cleanup, `PooledChecker` schedules idempotent cleanup
in a replacement worker.

### LLM counterexample verifier

The LLM adapter calls an OpenAI-compatible `/chat/completions` endpoint. The
model, base URL, API key, default reasoning effort, and default completion
budget are fixed when the registry is constructed. Per-run options may
override `reasoning_effort`, `max_completion_tokens`, `temperature`, and
`prompt`.

The prompt must contain `{q1}`, `{q2}`, and `{schema}`. The completion must be a
JSON object containing exactly:

- `explanation`;
- `counterexample_dsp`; and
- `counterexample`.

The `counterexample` field must be empty or parse as only SQL `INSERT`
statements. A valid completion is normalized to `not_equivalent`; malformed
responses and endpoint failures are failed attempts. EveBench currently checks
the response shape and SQL syntax but does not execute the generated inserts
to independently validate that the two queries return different results.
Consequently, the LLM adapter is a counterexample-producing disprover, not a
proof of equivalence.

## Interactive API

The implemented API surface is:

- `GET /v1/checkers` — checker descriptors, versions, capabilities, and
  availability;
- `POST /v1/checks` — run one case against selected checkers and return all
  results; and
- `GET /v1/benchmarks/summary` — four-way summaries, optionally filtered by
  `dataset` or `run_id`.

`POST /v1/checks` accepts:

```json
{
  "case": {
    "case_id": "example:1",
    "query_left": "SELECT a FROM t",
    "query_right": "SELECT a FROM t",
    "schema_ddl": "CREATE TABLE t (a INT);",
    "dialect": "sqlite",
    "semantics": "bag",
    "parameters": {}
  },
  "checker_ids": ["parseval"],
  "options": {
    "timeout_ms": 30000,
    "seed": 1,
    "backend_options": {
      "parseval": {
        "connection_string": "sqlite:////tmp/evebench-case.db"
      }
    }
  }
}
```

A backend timeout, unsupported input, or failure remains a normal per-checker
result and does not turn the response into a 5xx. Pydantic validation errors
and unknown checker IDs use HTTP validation responses.

The root routes serve the interactive workbench and leaderboard. Structured
request logging assigns or validates `x-request-id` and returns it in the
response header.

The API does not currently create, cancel, or resume benchmark runs. Benchmark
execution belongs to the CLI; the API reads persisted summaries.

## Benchmark pipeline

### Profiles

`BenchmarkConfig` loads versioned TOML profiles. A profile fixes:

- dataset and SQLite result paths;
- dataset name;
- timeout, SQL semantics, and seed;
- ordered checker IDs;
- one dialect and options table per checker; and
- worker count, worker recycling, and progress interval.

Unknown profile keys are rejected. Relative paths are resolved from the
profile file. The CLI can override common execution settings, select a subset
of configured checkers, limit the number of cases, or accept an ad hoc CSV.

### Dataset contract

Canonical datasets are UTF-8 CSV files with this header:

```text
dbid,index,schema_ddl,q1,q2,verieql_label,clean_ddl,parseval_label,ground_truth
```

Each row is one equivalence case. `(dbid, index)` identifies the case as
`dbid:index` and must be unique. `schema_ddl` is the authoritative schema,
while `q1` and `q2` are the query pair. The source verifier labels and
`clean_ddl` are retained for provenance. `ground_truth` is persisted for
evaluation but is never passed to a checker.

The runner requires `dbid`, `index`, `q1`, and `q2`, plus `schema_ddl` or the
legacy `clean_ddl` fallback. The canonical builder emits all nine columns and
validates non-empty input, schema coverage, and unique case IDs.

### Execution and resume

`BenchmarkRunner` reads the CSV, applies any case limit, and resolves a dialect
for every selected checker. For each row it:

1. creates the base `EquivalenceCase`;
2. injects a disposable SQLite connection for ParSEval;
3. runs all pending checkers concurrently through `CheckService`; and
4. commits the case and its results in one SQLite transaction.

The run snapshot includes the dataset SHA-256, case count after limiting,
timeout, selected checker IDs, semantics, seed, backend dialects, backend
options, profile version/path, and worker/progress settings.

To resume, the caller supplies an existing run ID. The store rejects a changed
dataset hash, case count, timeout, or configuration snapshot. Results already
present for a case/checker pair are skipped; only missing checker attempts are
run. The run ends as `completed`, `interrupted`, or `failed`.

## Persistence and summaries

`ResultStore` uses SQLite in WAL mode with foreign keys enabled. It creates
three tables:

| Table | Purpose |
| --- | --- |
| `benchmark_runs` | Dataset identity/hash, total cases, timeout, status, exact configuration JSON, and timestamps. |
| `benchmark_cases` | Per-run case ID, source row, database ID, ground truth, queries, and schema. |
| `evaluation_results` | One case/checker result with versions, normalized status/verdict, timing, diagnostics, witness JSON, metrics JSON, and raw output. |

Primary keys enforce one case per `(run_id, case_id)` and one result per
`(run_id, case_id, checker_id)`. Saving the same result key updates that row;
there is no separate attempt-history or artifact table. Witnesses, metrics, and
raw backend output are stored inline.

For leaderboard display, every result is reduced to one of four categories:

- `eq` for an equivalent verdict;
- `neq` for a non-equivalent verdict;
- `timeout` for a timeout status or verdict; and
- `unknown` for an unknown verdict and all other non-verdict outcomes,
  including unsupported, invalid, and failed executions.

Summary queries report counts and ratios for those categories plus median
duration. With no `run_id`, the API selects the highest run ID for each
dataset. These are outcome-distribution metrics, not ground-truth accuracy
scores.

## Observability

Application events use Python structured logging. Concise console logs and
rotating JSON Lines file logs share contextual fields such as request ID, run
ID, case ID, and checker ID. Benchmark progress and lifecycle events are
logged, while detailed results remain in SQLite.

## Package layout

```text
src/evebench/
  adapters/       lifecycle driver, registry, pooled proxies, and backends
    schema/       Calcite and VeriEQL DDL translation
  api/            FastAPI app, request/response schemas, and routes
  application/    checker selection and concurrent fan-out
  benchmark/      TOML profiles, CSV loading/building, runner, and SQLite store
  domain/         shared dataclasses and checker protocol
  execution/      shared Pebble process pool
  observability/  console and rotating JSONL logging
  web/            workbench and leaderboard static assets
```

The main extension points are a worker-side adapter, its descriptor and
picklable configuration, an `execute_adapter` dispatch entry, and registry
construction. Backend-specific behavior stays in the adapter and its validated
options rather than expanding the common HTTP or benchmark contracts.
