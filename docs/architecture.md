# EveBench backend and API design

## Goals

EveBench runs the same SQL-equivalence case through multiple heterogeneous
checkers and preserves enough information to:

1. return each checker's answer to an API client;
2. diagnose unsupported inputs, failures, and timeouts;
3. reproduce a result using the same checker version and configuration; and
4. compute a fair leaderboard against a versioned, labelled benchmark.

The API maps every backend answer into a small common result without exposing
backend-specific result classes.

## Core decisions

- Keep FastAPI/Pydantic models at the HTTP boundary. Adapters consume and return
  framework-independent domain models.
- Give every backend a small adapter, registered by a stable checker ID.
- Run all backend attempts through one application-owned Pebble process pool.
  An API coroutine timeout alone cannot stop Java, Z3, or CPU-bound Python.
- Store raw backend output as well as the normalized result.
- Treat benchmark execution as a durable asynchronous job. A process-local
  FastAPI background task is not durable enough for leaderboard runs.
- Version inputs, checker artifacts, adapter code, and effective configuration.
- Compute leaderboard rows from immutable attempts, not from mutable aggregate
  counters.

## Domain contract

### Check request

```python
class EquivalenceCase:
    case_id: str | None
    query_left: str
    query_right: str
    schema_ddl: str
    dialect: str
    semantics: Literal["bag", "set"]
    parameters: dict[str, JSONValue]

class CheckOptions:
    timeout_ms: int
    memory_limit_mb: int | None
    seed: int | None
    backend_options: dict[str, dict[str, JSONValue]]
```

`parameters` is reserved for typed query parameters or future schema metadata;
it must not become a bag of backend-specific flags. Backend-specific settings
belong in `backend_options`, keyed by checker ID.

### Normalized result

Keep the backend's conclusion separate from whether EveBench successfully ran
the backend.

```python
class CheckResult:
    checker: CheckerRef
    execution_status: Literal[
        "completed", "timeout", "unsupported", "invalid_input", "failed"
    ]
    verdict: Literal["equivalent", "not_equivalent", "unknown"] | None
    duration_ms: int
    started_at: datetime
    finished_at: datetime
    reason_code: str | None
    message: str | None
    witness: Counterexample | None
    metrics: dict[str, int | float | str | bool | None]
    raw: RawArtifactRef | None
```

Examples:

- A successful SQLSolver `EQ` maps to `equivalent`.
- A checker `UNKNOWN` is a completed execution with verdict `unknown`.
- A ParSEval mismatch maps to `not_equivalent` and may include its witness.
- A ParSEval match maps to `equivalent`.
- A killed process maps to execution status `timeout` and no verdict.
- Unsupported SQL maps to `unsupported`, rather than `unknown`, when the
  adapter can identify that condition.

`reason_code` is a stable, queryable value such as `unsupported_join`,
`parse_error`, `worker_timeout`, or `backend_crash`. `message` is diagnostic
text and is not part of the public compatibility contract.

## Adapter contract

```python
class BackendAdapter(Protocol):
    def check(
        self,
        case: EquivalenceCase,
        options: CheckOptions,
    ) -> CheckResult: ...
```

Backend adapters are synchronous because the complete adapter is constructed
and executed inside a Pebble worker. The API process registers lightweight
asynchronous `PooledChecker` proxies containing only a descriptor, picklable
backend configuration, and the shared pool reference.

`CheckerDescriptor` includes:

- stable ID, display name, backend version, and adapter version;
- supported dialects and bag/set semantics;
- execution mode (`process_pool` or `remote`);
- availability and health details.

The adapter returns an `unsupported` result directly when it cannot handle a
dialect, semantics, feature, or configuration. There is no separate support
preflight.

Adapters own only backend translation:

1. translate the common case and options into backend input;
2. invoke the backend directly inside the current pool worker;
3. parse backend output;
4. normalize it into `CheckResult`.

They do not select other adapters, write leaderboard records, create HTTP
responses, or retry non-idempotent work.

### Registry and execution

```text
FastAPI route
    |
    v
Run service -----> checker registry -----> pooled checker proxy
    |                                              |
    v                                              v
run repository                         global backend process pool
    |                                              |
    v                                              v
result/artifact store                    construct + run adapter
                                         |                 |
                                         v                 v
                                  ParSEval call      embedded JVM call
```

The registry is constructed at application startup from configuration, rather
than from arbitrary import paths supplied by clients. This keeps checker IDs
stable and avoids code-loading vulnerabilities.

The run service:

- validates checker IDs and request size limits;
- snapshots the effective checker configuration;
- fans out one attempt per selected checker;
- submits all backend attempts to the shared process pool;
- records each result independently, including partial completion;
- never cancels completed sibling results because one backend failed.

The global pool owns concurrency, task timeouts, worker replacement, and worker
recycling. The whole adapter—not merely a backend helper—runs in the worker.
ParSEval calls `disprove()` directly. SQLSolver uses JPype to start a JVM inside
the Python worker and calls the Java verification API directly, so neither
adapter launches another OS process. Pebble can terminate and replace the
containing worker when its wall-clock deadline expires.

## API

### Interactive checks

- `GET /v1/checkers` — descriptors, capabilities, versions, and availability.
- `POST /v1/checks` — run a small case against selected checkers and wait up to
  a bounded API deadline.

`POST /v1/checks` request:

```json
{
  "case": {
    "query_left": "SELECT a FROM t",
    "query_right": "SELECT a FROM t",
    "schema_ddl": "CREATE TABLE t (a INT);",
    "dialect": "postgres",
    "semantics": "bag",
    "parameters": {}
  },
  "checker_ids": ["sqlsolver", "parseval"],
  "options": {
    "timeout_ms": 30000,
    "backend_options": {}
  }
}
```

The response contains one result per checker. A backend timeout or failure is a
normal per-checker result and does not turn the whole HTTP response into a 5xx.
Malformed requests, unknown checker IDs, and server-level failures still use
appropriate HTTP error statuses.

### Benchmark runs

- `POST /v1/runs` — enqueue a benchmark snapshot and return `202` plus run ID.
- `GET /v1/runs/{run_id}` — status and progress counts.
- `GET /v1/runs/{run_id}/results` — paginated attempt results.
- `POST /v1/runs/{run_id}/cancel` — request cancellation of queued/running
  attempts.
- `GET /v1/leaderboards/{benchmark_id}` — aggregate a completed, published run
  or a specified set of comparable runs.

The initial API can use polling. Server-sent events can be added later without
changing the run/result model.

## Persistence

Recommended logical tables:

- `benchmark`: named, versioned benchmark metadata;
- `benchmark_case`: immutable queries, schema, dialect, semantics, expected
  label, tags, and content hash;
- `run`: benchmark version, state, environment fingerprint, timestamps;
- `run_checker`: checker artifact/version plus effective configuration snapshot;
- `attempt`: one case × checker execution, normalized fields and timings;
- `artifact`: content-addressed raw output, logs, proof, or counterexample.

Use a unique key on `(run_id, checker_id, case_id, attempt_number)` and an
idempotency key on run creation. Keep large logs and witnesses in object
storage; keep their hashes and metadata in the relational database.

Queries and schemas may contain sensitive or adversarial text. Logs should use
case IDs and hashes by default, with SQL logging explicitly enabled only in a
trusted deployment.

## Leaderboard semantics

Only compare runs with the same:

- benchmark content hash and expected-label policy;
- SQL semantics and dialect policy;
- timeout/resource class;
- allowed checker configuration policy.

Publish multiple metrics instead of a single opaque score:

- overall accuracy on labelled, eligible cases;
- equivalent and non-equivalent recall;
- false-equivalent and false-non-equivalent counts;
- decisive coverage: conclusive verdicts / eligible cases;
- attempted coverage: supported cases / total cases;
- timeout, unsupported, invalid-input, and failure rates;
- median and p95 duration, plus total compute time;
- counterexample/proof validation rate when independently checkable.

`unknown`, `unsupported`, `timeout`, and `failed` are not incorrect predictions,
but they reduce coverage. A separate optional competition score may penalize
abstention, but its formula must be versioned and displayed.

## Proposed package layout

```text
src/evebench/
  api/
    app.py
    dependencies.py
    routes/checkers.py
    routes/checks.py
    routes/runs.py
    schemas.py
  domain/
    models.py
    ports.py
    errors.py
  application/
    check_service.py
    run_service.py
    leaderboard_service.py
  adapters/
    registry.py
    sqlsolver.py
    parseval.py
    verieql.py
  execution/
    base.py
    pool.py
    container.py
  infrastructure/
    config.py
    repositories.py
    artifacts.py
  workers/
    tasks.py
```

`domain/ports.py` contains the checker, repository, executor, and artifact-store
protocols. This makes adapters unit-testable with fake executors and keeps the
first implementation from locking the project to a particular queue or
database.

## Backend-specific notes

### SQLSolver

Use JPype inside the pool worker to call SQLSolver's batch-capable Java API
directly. Pass both SQL strings, schema DDL, and the timeout overload rather
than scraping CLI output. Snapshot the JAR hash, Java version, Z3
version/configuration, Calcite dialect policy, and timeout. Normalize `EQ`,
`NEQ`, `UNKNOWN`, and `TIMEOUT`, retaining SQLSolver's raw enum result.

### VeriEQL

Run VeriEQL in an isolated Python subprocess because it requires its vendored
Z3 Python wrapper and a different native Z3 version from ParSEval. Translate
schema DDL with SQLGlot into VeriEQL's table/type dictionary and integrity
constraints. The adapter exposes the bounded row count as
`backend_options.verieql.bound_size` and returns generated counterexamples as
normalized witnesses.

### ParSEval

Its public `disprove(...)` API accepts queries, schema, connection string,
dialect, semantics, generation configuration, and timeout. The adapter requires
`backend_options.parseval.connection_string`, validates its SQLAlchemy backend
against the requested dialect, and passes it unchanged to `disprove()`. It does
not create an implicit temporary database. Connection strings are removed from
raw results to avoid returning credentials. The adapter always deletes the
SQLite file or drops the PostgreSQL/MySQL database in a `finally` path.
After a hard worker timeout, the pooled proxy schedules the same idempotent
cleanup in a replacement worker. System databases are rejected and identifiers
are dialect-quoted. Cleanup is destructive, so deployments must provision a
separate disposable database for every concurrent attempt.

### VeriEQL and future backends

Add each as another adapter plus an execution image. Do not add backend fields
to the common request unless they describe SQL semantics shared by multiple
tools; use validated backend options for tool-specific tuning.

## Delivery sequence

1. Implement domain models, adapter protocol, registry, fake checker, and
   interactive `/v1/checkers` + `/v1/checks`.
2. Add the global Pebble process pool and construct each complete adapter in its
   workers.
3. Add the embedded-JVM SQLSolver adapter and enforce its hard pool timeout.
4. Add durable run/attempt persistence and asynchronous benchmark workers.
5. Add benchmark import, metric computation, and leaderboard publication.
6. Add VeriEQL and other adapters using conformance tests.

Every adapter should pass the same contract suite: supported/unsupported input,
each normalized verdict, invalid input, timeout, crash, oversized output,
version capture, and cancellation.
