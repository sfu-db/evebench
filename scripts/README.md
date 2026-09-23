# Maintenance scripts

Run commands as modules from the repository root, using the project environment:

```bash
uv run python -m scripts.datasets.preprocess_sqlite_datasets --help
uv run python -m scripts.datasets.preprocess_leetcode_dataset --max-per-group 2
uv run python -m scripts.results.analyze_sqlite_results --help
```

The former top-level script paths have moved; use `python -m scripts.datasets.<name>`
or `python -m scripts.results.<name>` instead of `python scripts/<name>.py`.
Bird and LeetCode preprocessing include optional `--max-per-group` sampling.
Run the corresponding preprocessing command once on the raw CSV; no separate
reduction command or intermediate CSV is needed.

| Location | Purpose |
| --- | --- |
| `datasets/preprocess_*` | Convert Bird, beers, literature, and LeetCode sources into EveBench pairs |
| `datasets/convert_postgres_datasets.py` | Convert PostgreSQL rewrite exports |
| `datasets/relabel_postgres_dataset.py` | Overlay PostgreSQL 16 SPA labels on the processed PostgreSQL dataset |
| `datasets/check_literature_postgres.py` | Report PostgreSQL execution failures |
| `results/analyze_*` | Analyze historical model results |
| `results/import_sqlite_results.py` | Import mapped results into the leaderboard database |
| `results/compare_adapters_llm.py` | Compare adapter and model results |
| `paths.py` | Resolve repository-relative defaults |

Reusable dataset code lives in `datasets/shared`:

- `sqlite.py`: schema execution and pair filtering for SQLite datasets.
- `features.py`: dialect-aware query features; pairs require complex logic on both sides.
- `sampling.py`: deterministic pair deduplication and feature-group sampling.
- `portability.py`: temporary query/schema rendering and PostgreSQL ordering checks.
- `calcite.py` and `CalciteSyntaxCheck.java`: batched Calcite syntax checks.
- `csv_io.py`: canonical dataset columns and atomic CSV writes.

The Calcite runner uses the QED parser JAR. `EVEBENCH_QED_PARSER_JAR` and
`EVEBENCH_QED_JAVA` override its default JAR and Java executable.

Command failures retain their original traceback. Missing CSV columns raise the
underlying lookup error. Checks that define filtering policy, protect source files,
or ensure one Calcite result per input remain explicit. Parsing/execution failures
are caught where they produce a filtering decision or audit record; output cleanup
and transaction rollback run even when processing fails.
