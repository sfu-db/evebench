# Development environment

The supported development setup is the repository's VS Code dev container. It
provides the versions and native environment required by all EveBench
backends:

- Python 3.11 and `uv`;
- Java 17 and Gradle 7.3.3;
- Linux build tools and the SQLite CLI;
- Node.js 24 and `pnpm` for optional web tooling; and
- a persistent command-history volume and `uv` download cache.

The source tree remains on the host and is bind-mounted at
`/workspaces/evebench`, so edits and Git operations survive container rebuilds.
Python packages are installed in `/opt/venv`.

## 1. Install host prerequisites

Before cloning EveBench, install:

1. Git with submodule support.
2. Docker Engine or Docker Desktop with Docker Compose v2.
3. Visual Studio Code.
4. The VS Code **Dev Containers** extension
   (`ms-vscode-remote.remote-containers`).

Start Docker, then verify it from a host terminal:

```sh
docker version
docker compose version
git --version
code --version
```

If the `code` command is not on `PATH`, open the repository with
**File → Open Folder** in VS Code instead. On macOS, VS Code can install the
command from **Shell Command: Install 'code' command in PATH** in the Command
Palette.

EveBench's checked-in Z3 libraries and the JDK installed by the container are
Linux x86-64 artifacts. The container therefore needs a Linux x86-64 Docker
environment. On macOS and Windows, use Docker Desktop rather than trying to run
the native backends directly on the host.

## 2. Clone the repository and submodules

Clone EveBench with all pinned backend repositories:

```sh
git clone --recurse-submodules https://github.com/sfu-db/evebench.git evebench
cd evebench
```

If the main repository is already cloned, initialize or repair its submodules:

```sh
git submodule sync --recursive
git submodule update --init --recursive
```

Verify the checkout:

```sh
git submodule status --recursive
```

The command should list SQLSolver, SPES, VeriEQL, and the QED parser. A leading
`-` means a submodule has not been initialized. A leading `+` means its current
commit differs from the revision pinned by EveBench. Restore the pinned
revisions with:

```sh
git submodule update --init --recursive
```

Do not clone backend repositories manually into `baselines/`; using the pinned
submodule commits keeps local results reproducible.

## 3. Create the shared Docker network

The dev-container Compose file attaches to an external Docker network named
`shared_network`. Check whether it already exists:

```sh
docker network inspect shared_network
```

If Docker reports that it cannot find the network, create it once:

```sh
docker network create shared_network
```

This network is external to the Compose project, so removing or rebuilding the
EveBench container does not remove it.

## 4. Open EveBench in the dev container

From the repository root, open VS Code:

```sh
code .
```

Then:

1. Open the Command Palette with **F1** or
   **Ctrl/Cmd+Shift+P**.
2. Run **Dev Containers: Reopen in Container**.
3. Wait for the image to build and VS Code to reconnect.
4. Open a new integrated terminal after the lower-left status indicator shows
   that VS Code is connected to `evebench`.

Before Compose runs, `initializeCommand` writes the host user and group IDs to
`.devcontainer/.env`. The image creates a matching `user` account and the
container runs as that account. This prevents root-owned build products from
appearing in the host checkout.

The first build downloads the base image, Java 17, Gradle, Node.js, and editor
extensions. Later rebuilds reuse Docker layers, the named `uv_cache` volume,
and the `dev_history` shell-history volume.

## 5. Verify the container toolchain

Run these commands in the VS Code terminal inside the container:

```sh
pwd
python --version
uv --version
java -version
gradle --version
echo "$VIRTUAL_ENV"
```

The important values are:

```text
/workspaces/evebench
Python 3.11.x
Java 17
Gradle 7.3.3
/opt/venv
```

The container places `/opt/venv/bin` on `PATH` and configures
`UV_PROJECT_ENVIRONMENT=/opt/venv`. Do not create or activate a second virtual
environment.

Recheck the submodules from inside the container:

```sh
git submodule status --recursive
```

## 6. Install Python dependencies

From `/workspaces/evebench`, synchronize exactly the versions in `uv.lock`:

```sh
uv sync --frozen
```

This installs EveBench in editable form, its development dependencies, the
pinned ParSEval wheel, and packages required by the VeriEQL adapter.

Use `--frozen` for normal setup and CI parity. Run `uv sync` without
`--frozen` only when intentionally changing dependencies and updating
`uv.lock`.

Confirm that the package imports:

```sh
uv run python -c "import evebench; print(evebench.hello())"
```

## 7. Create the local environment file

Copy the example once:

```sh
cp .env.example .env
```

`.env` is ignored by Git. The repository-relative defaults already point to
the checked-out backends, so no edits are needed for the standard dev
container. Edit it only for custom paths, logging, worker counts, or the
optional LLM verifier.

EveBench does not load `.env` automatically. Prefix commands that need it with
`uv run --env-file .env`, as shown below.

To enable the optional LLM verifier, set at least:

```dotenv
EVEBENCH_LLM_MODEL=your-model-name
EVEBENCH_LLM_BASE_URL=https://api.openai.com/v1
EVEBENCH_LLM_API_KEY=your-api-key
```

Leave these settings commented or empty when developing only the local
backends.

## 8. Build SQLSolver

Build SQLSolver before SPES because SPES uses the Z3 Java and native libraries
under `baselines/SQLSolver/lib`:

```sh
cd baselines/SQLSolver
./gradlew fatJar
cd ../..
```

Verify the required artifacts:

```sh
test -f baselines/SQLSolver/build/libs/sqlsolver-v1.1.0.jar
test -f baselines/SQLSolver/lib/libz3.so
test -f baselines/SQLSolver/lib/libz3java.so
```

Each command exits silently when the file exists. SQLSolver is loaded through
JPype inside EveBench's worker processes.

## 9. Build SPES

After SQLSolver succeeds:

```sh
cd baselines/spes
./gradlew clean build
cd ../..
```

Verify the executable JAR:

```sh
test -f baselines/spes/build/libs/equitas-1.0-SNAPSHOT.jar
```

VeriEQL and ParSEval need no separate build step. VeriEQL runs from its
submodule in an isolated Python subprocess, and ParSEval was installed by
`uv sync`.

## 10. Run the tests

Run the complete test suite, including the real-backend integration test:

```sh
uv run --env-file .env pytest -q
```

To focus on adapter setup or see each backend's result, run only the
real-backend integration test with output enabled:

```sh
uv run --env-file .env pytest tests/test_real_backends.py -q -s
```

The integration test sends one non-equivalent pair through SQLSolver, SPES,
VeriEQL, and ParSEval. It also verifies counterexamples from VeriEQL and
ParSEval and confirms that ParSEval removes its temporary database.

## 11. Run EveBench locally

Start the API and web interface:

```sh
uv run --env-file .env python -m evebench
```

The server listens on `0.0.0.0:8000`. VS Code normally offers to forward the
port. If it does not, open the **Ports** panel, choose **Forward a Port**, and
enter `8000`.

Open the forwarded address:

- `/` for the interactive workbench;
- `/leaderboard` for persisted benchmark summaries; and
- `/docs` for the generated OpenAPI interface.

Stop the server with **Ctrl+C**.

For a quick benchmark smoke test:

```sh
uv run --env-file .env python -m evebench benchmark --limit 10
```

This uses `configs/benchmarks/bird_baseline.toml` and writes results to
`data/evebench.sqlite` by default. The `data/` directory and local `.env` file
are ignored by Git.

## 12. Preview the documentation

Run MkDocs on a different port if the EveBench API is still running:

```sh
uv run mkdocs serve --dev-addr 0.0.0.0:8001
```

Forward port `8001` from the VS Code **Ports** panel and open the displayed
address. Before submitting documentation changes, run:

```sh
uv run mkdocs build --strict
```

## Daily workflow

After the first setup, a typical development session is:

```sh
git submodule update --init --recursive
uv sync --frozen
uv run --env-file .env pytest -q
```

Rebuild SQLSolver or SPES only after changing its source, changing its pinned
submodule commit, or deleting its build output.

Useful commands:

```sh
# Run one test module
uv run --env-file .env pytest tests/test_benchmark.py -q

# Run one named test
uv run --env-file .env pytest tests/test_api.py -q -k checker

# Start a small benchmark with fewer workers
uv run --env-file .env python -m evebench benchmark \
  --limit 10 \
  --max-workers 2

# Inspect the result database
sqlite3 data/evebench.sqlite
```

## Rebuilding or reopening the container

Use **Dev Containers: Rebuild Container** after changing
`.devcontainer/Dockerfile`, `.devcontainer/docker-compose.yml`, or the
container user configuration. Use **Dev Containers: Reopen Folder Locally** to
leave the container without stopping Docker manually.

A rebuild does not delete the host checkout, command history, or `uv` cache.
The `/opt/venv` environment belongs to the image/container, so run
`uv sync --frozen` again after a fresh rebuild.

## Troubleshooting

### The container cannot find `shared_network`

Create the external network on the host, then rebuild:

```sh
docker network create shared_network
```

### A submodule directory is empty

From the repository root:

```sh
git submodule sync --recursive
git submodule update --init --recursive
git submodule status --recursive
```

### Files created in the container have the wrong owner

Close the dev container and run **Dev Containers: Rebuild Container**. The
repository's `initializeCommand` regenerates `.devcontainer/.env` with the
current host UID and GID before the rebuild.

### SQLSolver is unavailable or its JAR is missing

Rebuild SQLSolver and verify its output:

```sh
cd baselines/SQLSolver
./gradlew fatJar
cd ../..
test -f baselines/SQLSolver/build/libs/sqlsolver-v1.1.0.jar
```

### SPES cannot load Z3

SPES is using the same Z3 libraries as SQLSolver. Please confirm that SQLSolver's native libraries exist, then rebuild SPES:

```sh
test -f baselines/SQLSolver/lib/libz3.so
test -f baselines/SQLSolver/lib/libz3java.so
cd baselines/spes
./gradlew clean build
cd ../..
```

The default EveBench configuration points both Java backends at
`baselines/SQLSolver/lib`.

### Python imports resolve outside `/opt/venv`

Open a new VS Code terminal and check:

```sh
which python
echo "$VIRTUAL_ENV"
echo "$UV_PROJECT_ENVIRONMENT"
```

They should resolve to `/opt/venv/bin/python`, `/opt/venv`, and `/opt/venv`.
If they do not, run **Dev Containers: Rebuild Container** rather than creating
another virtual environment.

### Port 8000 is not reachable

Confirm that the application is still running, then forward container port
`8000` in VS Code's **Ports** panel. The Compose file intentionally does not
publish host ports directly.

### Start over without losing source changes

Use **Dev Containers: Rebuild Container**. The workspace is a bind mount, so
tracked and untracked source files remain in the host checkout. If a full
Docker cleanup is needed, first inspect and preserve any data under
`data/`; it is part of the workspace and is not stored in a Docker volume.
