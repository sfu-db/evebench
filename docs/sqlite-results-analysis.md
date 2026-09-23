# Analyze historical SQLite results

Run from the repository root (Python standard library only):

```bash
python -m scripts.results.analyze_sqlite_results
```

Optional flags: `--sqlite-dir PATH` selects a directory containing `evebench/`,
`beers copy/`, and `output/`; `--output-dir PATH` selects the report destination
(default `tmp/sqlite_results_analysis`). Existing report files are replaced.

The script uses the current `evebench/beers.csv`, `literature.csv`, and
`sqlite.csv` as the evaluation population and authoritative ground truth.
It maps their `source_index` to historical `index` (or beers `id`), with `dbid`
for BIRD and literature. It never joins on the renumbered current `index` or
row position. Anonymized (`-dummy`) runs retain source IDs and are reported
separately; this mapping assumes those IDs preserve the original case identity.
Queries and schemas are not re-executed or checked for historical drift.

Outputs:

- `summary.csv`: metrics for each dataset, variant, approach, model, file, and
  verdict channel. Rates range from 0 to 1; undefined rates are blank.
- `mapped_results.csv`: one row per current case per run per verdict channel,
  including current/source IDs, raw verdicts, mapping status and correctness.
- `mapping_audit.csv`: matched, missing, duplicate and excluded historical rows,
  plus auxiliary CSVs skipped because they are not individual approach runs.

Beers approach aliases are `baseline` → `DF`, `baseline2` → `CE`,
`condition-CoT2` → `CE-condition-CoT`, and `error-type-CoT2` →
`CE-NonEqType-CoT`. The original name is retained in `source_approach`.

Four channels are reported separately:

- `explicit_verdict`: `equiv` Y/N becomes EQ/NEQ.
- `validated_counterexample`: `counterexample_valid=Y` becomes NEQ; every
  other value is UNKNOWN. Failure to find a counterexample does not prove EQ.
- `parseval` and `verieql`: embedded historical checker labels, with EQU/NIE
  normalized to EQ/NEQ. These repeated labels are not independent checker runs.

Missing or duplicate matches abstain (UNKNOWN). Missing columns, blank verdicts,
errors, timeouts and unsupported labels also abstain. `accuracy` counts correct
answers over **all current cases**, whereas `decided_accuracy` uses only EQ/NEQ
predictions. `coverage` is the fraction receiving EQ/NEQ. EQ and NEQ recall and
confusion counts expose class imbalance. For the counterexample channel,
NEQ recall measures counterexample discovery; EQ recall is necessarily zero.
No historical label replaces current ground truth. Counterexample validity is
taken from stored outputs, not revalidated against today's schema.

## Import into the leaderboard

With EveBench installed in the active Python environment, run:

```bash
python -m scripts.results.import_sqlite_results --database data/evebench.sqlite
```

The default database is `EVEBENCH_RESULTS_DB` if set in the shell, otherwise
`data/evebench.sqlite`. `--sqlite-dir` has the same meaning as in the analyzer.
The importer regenerates mappings from the source CSVs, then inserts case-level
results and all four channels into three completed runs:
`sqlite_beers_historical`, `sqlite_bird_historical`, and
`sqlite_literature_historical`. Select these datasets on `/leaderboard`.
Approach names include model, approach, original/dummy variant, and channel.
The embedded checker channels repeat historical checker results per source file;
they should not be treated as independent experiments.

Importing the same content again returns the existing runs. Changed datasets or
mapped results produce a new snapshot; the leaderboard shows the latest snapshot.
All three runs are inserted in one transaction, without replacing existing runs.
Source file names, source IDs, raw mapped verdicts and current ground truth are
retained. Import timestamps identify ingestion, not original execution dates.
Historical duration and timeout values are unavailable: stored zero durations are
marked unavailable in metrics and displayed as a dash, never as measured 0 ms.

The leaderboard's Accuracy column counts correct predictions over labeled cases,
including UNKNOWN predictions in the denominator. Counterexample-only channels
therefore measure confirmed NEQ discoveries, not two-sided equivalence accuracy.
