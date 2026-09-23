# Bird query-pair sampling

Bird uses the same feature-group sampling policy as LeetCode, with SQLite
parsing. It is integrated into `scripts/datasets/preprocess_sqlite_datasets.py` as an
optional final stage after the existing complexity and portability filters.

## Run

From the repository root:

```bash
python -m scripts.datasets.preprocess_sqlite_datasets \
  --max-per-group 2 \
  --output-file datasets/sqlite/evebench/sqlite.sampled.csv
```

This reads the raw input `datasets/sqlite/bird_benchmark.csv` and runs SQLite
validation, complexity and portability checks, and sampling in one command.
Without `--output-file`, the final dataset is written to
`datasets/sqlite/evebench/sqlite.csv`. Both queries must contain complex logic.

Omitting `--max-per-group` disables the optional sampling stage. Setting it
to `0` enables duplicate removal only, while a positive value caps ordinary
representatives per group. Negative values are rejected. Sampling requires a
`dbid` column. Each row's dialect selects the parser, defaulting to SQLite when
absent. The existing Calcite parser setup remains required unless explicitly
disabled with the existing `--skip-calcite` option.

## Policy and implementation

Bird and LeetCode preprocessing call `scripts/datasets/shared/sampling.py`. It receives the shared
`query_features` extractor from `scripts/datasets/shared/features.py`, rather than
implementing a separate set of Bird features. The feature definitions and
selection procedure are described in the
[LeetCode design](leetcode-pair-sampling.md) and
[methods paper](leetcode-pair-sampling-paper.md).

Normalized duplicate keys include dialect, database ID, exact original schema
DDL, and the unordered pair of normalized queries. Group keys include dialect,
database ID, exact original schema DDL, source label, and the unordered pair of
14-component bucketed feature vectors. Each component is 0, 1, or 2+.

Within each group, the first two ordinary representatives are retained by
default when the command above is used. Reversed pairs count as duplicates;
conflicting labels on duplicate pairs bypass the cap. Group and conflict analysis
use only rows that pass the earlier filters. Rejected rows do not consume slots
and cannot be reintroduced by sampling.

The shared sampler preserves and flags sampling-stage analysis failures. The
Bird reducer retains its existing fail-fast behavior for failures during its
earlier feature extraction, schema processing, or query translation; sampling
does not override that behavior. Likewise, label conflicts rejected before
sampling are not examined by the sampling stage.

The schema and SQL used in sampling keys come from the original retained row,
not the temporary, constraint-cleaned schema or quoted SQL used by the portability
checks. Preprocessing normalizes SQL and retains schema, source labels, and source indices;
output indices are reassigned sequentially. These are structural diversity
heuristics, not equivalence proofs or evidence that every retained pair is hard.

## Output

Only the reduced CSV is written, using an atomic replacement. No audit file is
saved. Selection is deterministic for a fixed input order and parser version;
it does not impose global EQ/NEQ balance.

## Historical result

The former two-step workflow applied to the Bird artifacts on 2026-09-23 produced:

| Stage | Total | EQ | NEQ |
|---|---:|---:|---:|
| Preprocessed input | 1,265 | 297 | 968 |
| Existing complexity and portability filters | 878 | 170 | 708 |
| Feature-group sampling, cap 2 | 768 | 142 | 626 |

The recorded pre-sampling keep mask matched the historical reduction audit exactly. Sampling
removes another 110 pairs (12.5% of the eligible population), all by the group
cap. All 641 eligible groups retain representatives. This run has no additional
duplicate removals, conflicting-label exceptions, or sampling-analysis failures.

With one representative per group, this eligible population would retain 641
pairs; that would trade additional reduction for less within-group variety. The
provided artifact uses cap 2. Group coverage is defined by the feature abstraction
and does not guarantee coverage of every join subtype, semantic distinction, or
solver failure mode.
