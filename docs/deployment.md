# Deployment

EveBench currently supports a single-host deployment: one API process, one
shared backend process pool, and a local SQLite result database. This guide
uses a Linux x86-64 host, systemd, and Nginx as a concrete reference
deployment.

The repository does not currently ship a production Dockerfile, Kubernetes
manifest, authentication layer, or distributed benchmark queue. The
`.devcontainer` image is intended for development and should not be treated as
a hardened production image.

## Deployment topology

```text
Users
  |
  v
TLS and access control
  |
  v
Nginx on ports 80/443
  |
  v
Uvicorn on 127.0.0.1:8000
  |
  +---- shared Pebble worker pool
  |       +-- SQLSolver / embedded JVM
  |       +-- SPES / child JVM
  |       +-- VeriEQL / child Python process
  |       +-- ParSEval
  |       +-- optional LLM HTTP requests
  |
  +---- /var/lib/evebench/evebench.sqlite
  +---- /var/log/evebench/evebench.jsonl
```

Run one Uvicorn worker. Each Uvicorn process constructs its own backend process
pool, JVM workers, and SQLite store. Increasing Uvicorn's `--workers` value
therefore multiplies resource use and does not provide a coordinated
distributed executor.

## 1. Choose the host and capacity

The host must provide:

- a Linux x86-64 environment;
- enough CPU and memory for the configured number of concurrent checker
  workers;
- local persistent storage for SQLite, logs, and backups;
- outbound HTTPS during installation;
- outbound access to an LLM endpoint if the optional `llm` checker is enabled;
  and
- inbound access only through the chosen reverse proxy or private network.

SQLSolver and SPES use the checked-in x86-64 Z3 libraries. Do not place the
SQLite database on NFS or another network filesystem with uncertain locking
semantics. Use a local persistent disk and back it up regularly.

The default worker count is four. Each worker may host a JVM or invoke Z3, so a
smaller initial value such as two is safer on a modest server. Increase it only
after observing memory, CPU, and timeout behavior.

## 2. Install operating-system packages

The following example is for Debian or Ubuntu. Use equivalent packages on
another distribution:

```sh
sudo apt-get update
sudo apt-get install --yes \
  build-essential \
  ca-certificates \
  curl \
  git \
  nginx \
  openjdk-17-jdk \
  sqlite3
```

Verify Java and Git:

```sh
java -version
git --version
```

Java must report version 17. The Gradle wrappers in the SQLSolver and SPES
submodules download their own compatible Gradle distributions.

## 3. Create the service account and directories

Create a non-login account:

```sh
sudo useradd \
  --system \
  --create-home \
  --home-dir /var/lib/evebench \
  --shell /usr/sbin/nologin \
  evebench
```

If the account already exists, do not recreate it. Create the application,
configuration, state, log, and backup directories:

```sh
sudo install -d -o evebench -g evebench /opt/evebench
sudo install -d -o evebench -g evebench /var/lib/evebench
sudo install -d -o evebench -g evebench /var/log/evebench
sudo install -d -o evebench -g evebench /var/backups/evebench
sudo install -d -o root -g evebench -m 0750 /etc/evebench
```

The intended ownership is:

| Path | Owner | Purpose |
| --- | --- | --- |
| `/opt/evebench/app` | `evebench` | Source, submodules, virtual environment, and built adapters |
| `/etc/evebench` | `root:evebench` | Deployment environment and benchmark profiles |
| `/var/lib/evebench` | `evebench` | SQLite database and service home |
| `/var/log/evebench` | `evebench` | Rotating JSON Lines logs |
| `/var/backups/evebench` | `evebench` | SQLite backups |

## 4. Install `uv` and Python

Install `uv` for the service account:

```sh
sudo -u evebench -H sh -c \
  'curl -LsSf https://astral.sh/uv/install.sh | sh'
```

The default installer places the binary under
`/var/lib/evebench/.local/bin`. Verify it and install a managed Python 3.11:

```sh
sudo -u evebench -H /var/lib/evebench/.local/bin/uv --version
sudo -u evebench -H /var/lib/evebench/.local/bin/uv python install 3.11
```

Using a managed Python avoids depending on the distribution's default Python
minor version.

## 5. Check out a pinned EveBench revision

Clone all submodules:

```sh
sudo -u evebench -H git clone \
  --recurse-submodules \
  https://github.com/sfu-db/evebench.git \
  /opt/evebench/app
```

Select the exact tag or commit approved for deployment:

```sh
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && git checkout DEPLOYED_TAG_OR_COMMIT'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && git submodule sync --recursive'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && git submodule update --init --recursive'
```

Replace `DEPLOYED_TAG_OR_COMMIT` with a real immutable tag or full commit SHA.
Record it in the deployment change log. Verify the result:

```sh
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && git status --short'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && git submodule status --recursive'
```

The main worktree should be clean. A leading `-` in submodule status means a
submodule is missing; a leading `+` means it is not at the revision pinned by
the selected EveBench commit.

## 6. Install Python dependencies

Create the project virtual environment from the lock file:

```sh
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && /var/lib/evebench/.local/bin/uv sync \
  --frozen --python 3.11'
```

The initial sync includes development dependencies so the deployment can be
tested before activation. The environment is created at
`/opt/evebench/app/.venv`.

Verify the import:

```sh
sudo -u evebench -H \
  /opt/evebench/app/.venv/bin/python \
  -c 'import evebench; print(evebench.hello())'
```

## 7. Build the native backends

Build SQLSolver first:

```sh
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app/baselines/SQLSolver && ./gradlew fatJar'
```

Then build SPES:

```sh
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app/baselines/spes && ./gradlew clean build'
```

Verify every required artifact:

```sh
sudo -u evebench test -f \
  /opt/evebench/app/baselines/SQLSolver/build/libs/sqlsolver-v1.1.0.jar
sudo -u evebench test -f \
  /opt/evebench/app/baselines/SQLSolver/lib/libz3.so
sudo -u evebench test -f \
  /opt/evebench/app/baselines/SQLSolver/lib/libz3java.so
sudo -u evebench test -f \
  /opt/evebench/app/baselines/spes/build/libs/equitas-1.0-SNAPSHOT.jar
```

VeriEQL runs directly from its submodule. ParSEval is installed from the wheel
pinned in `pyproject.toml`.

## 8. Test the release before activation

Run the complete suite:

```sh
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && .venv/bin/pytest -q'
```

The suite includes the real-backend integration test. To diagnose only native
adapter setup:

```sh
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && .venv/bin/pytest \
  tests/test_real_backends.py -q -s'
```

After tests pass, remove development-only packages:

```sh
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && /var/lib/evebench/.local/bin/uv sync \
  --frozen --no-dev --python 3.11'
```

## 9. Create the production environment file

Create `/etc/evebench/evebench.env`:

```dotenv
EVEBENCH_MAX_WORKERS=2
EVEBENCH_MAX_TASKS_PER_WORKER=25

EVEBENCH_RESULTS_DB=/var/lib/evebench/evebench.sqlite

EVEBENCH_LOG_FILE=/var/log/evebench/evebench.jsonl
EVEBENCH_LOG_LEVEL=INFO
EVEBENCH_CONSOLE_LOG_LEVEL=INFO
EVEBENCH_LOG_CONSOLE=true
EVEBENCH_LOG_MAX_BYTES=10485760
EVEBENCH_LOG_BACKUP_COUNT=10

EVEBENCH_PYTHON=/opt/evebench/app/.venv/bin/python

EVEBENCH_SQLSOLVER_JAR=/opt/evebench/app/baselines/SQLSolver/build/libs/sqlsolver-v1.1.0.jar
EVEBENCH_SQLSOLVER_LIB=/opt/evebench/app/baselines/SQLSolver/lib
EVEBENCH_SQLSOLVER_WORKDIR=/opt/evebench/app/baselines/SQLSolver

EVEBENCH_SPES_JAR=/opt/evebench/app/baselines/spes/build/libs/equitas-1.0-SNAPSHOT.jar
EVEBENCH_SPES_LIB=/opt/evebench/app/baselines/SQLSolver/lib
EVEBENCH_SPES_WORKDIR=/opt/evebench/app/baselines/spes
EVEBENCH_JAVA=/usr/bin/java

EVEBENCH_VERIEQL_ROOT=/opt/evebench/app/baselines/VeriEQL
EVEBENCH_VERIEQL_Z3_LIB=/opt/evebench/app/baselines/SQLSolver/lib
EVEBENCH_VERIEQL_PYTHON=/opt/evebench/app/.venv/bin/python
EVEBENCH_VERIEQL_BOUND_SIZE=2
```

To enable the optional LLM verifier, append:

```dotenv
EVEBENCH_LLM_MODEL=your-model-name
EVEBENCH_LLM_BASE_URL=https://api.openai.com/v1
EVEBENCH_LLM_API_KEY=your-api-key
EVEBENCH_LLM_REASONING_EFFORT=medium
EVEBENCH_LLM_MAX_COMPLETION_TOKENS=4096
```

Do not enable the LLM variables if the deployment does not use that checker.
Restrict the environment file because it may contain an API key:

```sh
sudo chown root:evebench /etc/evebench/evebench.env
sudo chmod 0640 /etc/evebench/evebench.env
```

Environment files use `KEY=value` syntax, not shell commands. Use absolute
paths and do not add `export`.

## 10. Install the systemd API service

Create `/etc/systemd/system/evebench.service`:

```ini
[Unit]
Description=EveBench API and web interface
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=evebench
Group=evebench
WorkingDirectory=/opt/evebench/app
EnvironmentFile=/etc/evebench/evebench.env
ExecStart=/opt/evebench/app/.venv/bin/uvicorn evebench.api:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillSignal=SIGTERM
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

Reload systemd, enable the service at boot, and start it:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now evebench.service
sudo systemctl status evebench.service
```

Follow startup logs:

```sh
sudo journalctl -u evebench.service -f
```

From the server, verify liveness and checker registration:

```sh
curl --fail --silent --show-error \
  http://127.0.0.1:8000/healthz
curl --fail --silent --show-error \
  http://127.0.0.1:8000/v1/checkers
```

`/healthz` confirms that the HTTP process is responsive. It does not execute
the backends. `/v1/checkers` reports artifact availability and registered
capabilities; use the real-backend integration test for an end-to-end check.

## 11. Configure the reverse proxy

Keep port 8000 private. The reference systemd unit binds it to loopback so only
software on the same host can reach Uvicorn.

Create `/etc/nginx/sites-available/evebench`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name evebench.example.com;

    client_max_body_size 2m;

    location / {
        # Replace this example range with the deployment's trusted network,
        # or configure the organization's authentication proxy.
        allow 10.0.0.0/8;
        deny all;

        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 330s;
        proxy_send_timeout 330s;
    }
}
```

Replace `evebench.example.com` and the example private-network allowlist. Then
enable and validate the site:

```sh
sudo ln -s /etc/nginx/sites-available/evebench \
  /etc/nginx/sites-enabled/evebench
sudo nginx -t
sudo systemctl reload nginx
```

Configure TLS using the organization's certificate automation or load
balancer. Redirect plain HTTP to HTTPS after certificate issuance. Expose only
ports 80 and 443 through the host or cloud firewall; do not expose port 8000.

### Access-control requirement

EveBench does not implement login, authorization, rate limiting, or tenant
isolation. Do not expose it directly to the public internet. Put it behind an
authenticated reverse proxy, VPN, or strict network allowlist.

This is especially important for ParSEval: interactive requests can provide a
disposable SQLite, PostgreSQL, or MySQL connection string, and the adapter
cleans up that named database after the attempt. The service account and
network must not have credentials or connectivity to production databases.

## 12. Create a production benchmark profile

Benchmark profiles contain their own database path. Setting
`EVEBENCH_RESULTS_DB` changes the API store but does not override the
`database` field in a TOML benchmark profile.

Copy the default profile:

```sh
sudo cp \
  /opt/evebench/app/configs/benchmarks/bird_baseline.toml \
  /etc/evebench/bird_baseline.toml
sudo chown root:evebench /etc/evebench/bird_baseline.toml
sudo chmod 0640 /etc/evebench/bird_baseline.toml
```

Edit these two fields to use absolute deployment paths:

```toml
dataset = "/opt/evebench/app/datasets/sqlite/bird_benchmark.csv"
database = "/var/lib/evebench/evebench.sqlite"
```

Keep the remaining checker, dialect, timeout, seed, and backend options pinned.
For the optional LLM checker, add `llm` to `checkers` and add the
`[backends.llm]` tables described in the project README.

## 13. Install the benchmark service

The API only reads benchmark summaries; it does not enqueue benchmark jobs.
Run benchmarks as a separate one-shot service.

Create `/etc/evebench/benchmark.env` so benchmark-specific settings override
the API defaults:

```dotenv
EVEBENCH_MAX_WORKERS=1
EVEBENCH_LOG_FILE=/var/log/evebench/benchmark.jsonl
```

Protect it with the same ownership and mode as the main environment file:

```sh
sudo chown root:evebench /etc/evebench/benchmark.env
sudo chmod 0640 /etc/evebench/benchmark.env
```

Create `/etc/systemd/system/evebench-benchmark.service`:

```ini
[Unit]
Description=EveBench benchmark run
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=evebench
Group=evebench
WorkingDirectory=/opt/evebench/app
EnvironmentFile=/etc/evebench/evebench.env
EnvironmentFile=/etc/evebench/benchmark.env
ExecStart=/opt/evebench/app/.venv/bin/python -m evebench benchmark --config /etc/evebench/bird_baseline.toml
Nice=10
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
```

Reload systemd and start a run:

```sh
sudo systemctl daemon-reload
sudo systemctl start evebench-benchmark.service
sudo journalctl -u evebench-benchmark.service -f
```

The benchmark service and API can share the SQLite file because
`ResultStore` enables WAL mode. Running them together still combines their CPU
and memory use. The example gives the benchmark one worker to reduce
contention.

After completion:

```sh
sudo systemctl status evebench-benchmark.service
curl --fail --silent --show-error \
  http://127.0.0.1:8000/v1/benchmarks/summary
```

For an interrupted run, add `--run-id RUN_ID` to `ExecStart`, run
`systemctl daemon-reload`, and start the one-shot service again. The dataset
hash, limit, timeout, selected checkers, and effective configuration must match
the original run.

## 14. Back up SQLite

Create backups with SQLite's online backup command instead of copying the
database file while a benchmark may be writing:

```sh
sudo -u evebench sqlite3 \
  /var/lib/evebench/evebench.sqlite \
  ".backup '/var/backups/evebench/evebench-YYYYMMDD-HHMMSS.sqlite'"
```

Replace the timestamp placeholder with the actual UTC backup time. Verify the
backup:

```sh
sudo -u evebench sqlite3 \
  /var/backups/evebench/evebench-YYYYMMDD-HHMMSS.sqlite \
  "PRAGMA integrity_check;"
```

The result must be `ok`. Copy backups to storage outside the EveBench host and
apply the organization's retention and encryption policy. Back up
`/etc/evebench` separately, excluding or encrypting the LLM API key.

### Restore a backup

Stop both writers/readers:

```sh
sudo systemctl stop evebench-benchmark.service
sudo systemctl stop evebench.service
```

Preserve the current database, then restore the selected backup:

```sh
sudo mv \
  /var/lib/evebench/evebench.sqlite \
  /var/lib/evebench/evebench.sqlite.before-restore
sudo cp \
  /var/backups/evebench/evebench-YYYYMMDD-HHMMSS.sqlite \
  /var/lib/evebench/evebench.sqlite
sudo chown evebench:evebench /var/lib/evebench/evebench.sqlite
sudo chmod 0640 /var/lib/evebench/evebench.sqlite
```

Check it and restart:

```sh
sudo -u evebench sqlite3 \
  /var/lib/evebench/evebench.sqlite \
  "PRAGMA integrity_check;"
sudo systemctl start evebench.service
curl --fail --silent --show-error \
  http://127.0.0.1:8000/healthz
```

Keep `evebench.sqlite.before-restore` until the restored service has been
validated.

## 15. Monitor the deployment

At minimum, monitor:

- `GET /healthz` for HTTP liveness;
- `GET /v1/checkers` for registered checker availability;
- systemd restart count and process exit status;
- CPU, memory, worker timeouts, and disk space;
- `/var/log/evebench/evebench.jsonl` and the system journal;
- SQLite integrity and backup freshness; and
- LLM HTTP errors, latency, and token-usage metrics when enabled.

Useful commands:

```sh
sudo systemctl status evebench.service
sudo journalctl -u evebench.service --since today
sudo tail -f /var/log/evebench/evebench.jsonl
sudo du -h /var/lib/evebench/evebench.sqlite
sudo -u evebench sqlite3 \
  /var/lib/evebench/evebench.sqlite \
  "PRAGMA integrity_check;"
```

The health endpoint is intentionally shallow. Backend readiness requires an
actual representative check or the real-backend integration test.

## 16. Upgrade EveBench

Use a maintenance window:

1. Record the currently deployed commit and submodule revisions.
2. Stop any benchmark run.
3. Create and verify a SQLite backup.
4. Stop the API.
5. Check out the new pinned revision and update submodules.
6. synchronize dependencies, rebuild adapters, and run tests;
7. start the API and verify health, checkers, and one representative request.

Commands:

```sh
sudo systemctl stop evebench-benchmark.service
sudo systemctl stop evebench.service

sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && git fetch --tags --prune'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && git checkout NEW_TAG_OR_COMMIT'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && git submodule sync --recursive'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && git submodule update --init --recursive'

sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && /var/lib/evebench/.local/bin/uv sync \
  --frozen --python 3.11'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app/baselines/SQLSolver && ./gradlew fatJar'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app/baselines/spes && ./gradlew clean build'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && .venv/bin/pytest -q'
sudo -u evebench -H sh -c \
  'cd /opt/evebench/app && /var/lib/evebench/.local/bin/uv sync \
  --frozen --no-dev --python 3.11'

sudo systemctl start evebench.service
curl --fail --silent --show-error \
  http://127.0.0.1:8000/healthz
curl --fail --silent --show-error \
  http://127.0.0.1:8000/v1/checkers
```

Replace `NEW_TAG_OR_COMMIT` before running the commands.

## 17. Roll back

If post-deployment checks fail:

1. stop the API;
2. check out the previously recorded commit;
3. restore its pinned submodules;
4. synchronize its locked dependencies;
5. rebuild SQLSolver and SPES;
6. restore the pre-upgrade SQLite backup if the new version changed persistent
   data incompatibly; and
7. restart and validate.

Use the same checkout, submodule, build, and test commands from the upgrade
section with `PREVIOUS_TAG_OR_COMMIT`.

Do not use `git reset --hard` on the deployment checkout. A clean deployment
should make an ordinary `git checkout` sufficient, while preserving evidence
if an unexpected file was modified.

## Production checklist

Before announcing the service:

- [ ] The deployed main commit and every submodule revision are recorded.
- [ ] The full test suite passes on the deployment host.
- [ ] SQLSolver, SPES, VeriEQL, and ParSEval pass an end-to-end check.
- [ ] `/healthz` and `/v1/checkers` respond through the reverse proxy.
- [ ] Uvicorn listens only on loopback and port 8000 is not externally open.
- [ ] TLS and proxy-level authentication or a trusted-network allowlist are
      enabled.
- [ ] The EveBench account cannot reach production databases.
- [ ] Environment files and LLM credentials have restricted permissions.
- [ ] SQLite is on persistent local storage.
- [ ] Backups have been created, integrity-checked, and copied off-host.
- [ ] Log rotation, disk-space monitoring, and systemd alerts are active.
- [ ] Worker count and proxy timeouts match the host's capacity and the
      configured maximum checker timeout.
