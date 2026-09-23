# LeetCode query-pair grouping and sampling

For the motivation and research framing, see the
[paper-style methods draft](leetcode-pair-sampling-paper.md).

The LeetCode preprocessor can build a smaller, structurally diverse dataset in
the same pass that filters the raw SQL-equivalence data. It combines normalized
duplicate detection with a fixed number of representatives per feature group.
Its unit of selection is a **query pair**, including its database schema and
EQ/NEQ label.

The result should be described as a **structurally filtered, feature-diverse
subset**. Neither the structural filter nor sampling establishes that every
retained pair is hard for equivalence checking. Two pairs with the same feature
signature can require different reasoning and expose different bugs.

## Pipeline and implementation boundaries

1. `scripts/datasets/preprocess_leetcode_dataset.py` converts legacy results into the
   standard EveBench columns, sets `source=leetcode` and `dialect=mysql`, and
   preserves source SQL, schemas, and labels. It filters incomplete, duplicate,
   formatting-identical, window-function, and Calcite-unparseable pairs. Calcite
   checks a temporary MySQL rendering with quoted identifiers to avoid keyword
   collisions such as `DAY` and `FREE`. It requires complex logic in both queries; this is a
   structural heuristic, not a solver.
2. `scripts/datasets/shared/features.py` supplies the shared SQLGlot feature
   extractor, `query_features`. The LeetCode preprocessor calls it with `dialect="mysql"`.
   Feature extraction is independent of schema cleanup and portability checks.
3. When `--max-per-group` is supplied, preprocessing calls the shared
   `scripts/datasets/shared/sampling.py` sampler after the Calcite and complexity
   filters, then writes the final CSV. The [Bird preprocessor](bird-pair-sampling.md)
   uses the same sampler after its existing filters.

The shared implementation prefixes both keys below with the dialect to prevent
mixing dialects. For the LeetCode input this is always `mysql`, so its addition
does not change groups or decisions.

The preprocessor preserves output column order and retained values, except that output
`index` is reassigned sequentially from 1. `source_index` remains unchanged.
The raw input file is not modified.

## Duplicate detection

For each query, SQLGlot parses exactly one MySQL query and renders it back to
MySQL with comments disabled. Let this rendering be `N(q)`. The duplicate key is:

```text
(dbid, exact schema_ddl, sorted(N(q1), N(q2)))
```

Sorting makes query order interchangeable: `(A, B)` and `(B, A)` describe the
same equivalence question. Stored query order is not changed. Database and schema
are part of the key because equivalence can depend on schema constraints.
Schema text is compared exactly; equivalent DDL with different formatting does
not merge into one group.

Identifier case and literal values remain significant. The implementation does
not erase aliases, reorder predicates, use schema constraints to prove rewrites,
or establish semantic equivalence between different normalized strings.
SQLGlot rendering can canonicalize syntax; this is a normalization-based duplicate
criterion, not an independently verified semantic proof.

Labels are deliberately excluded from this key. The first pass collects all
labels associated with each duplicate key. If a key has conflicting labels, all
its rows are retained and flagged rather than silently choosing a label.

## Query feature vectors

Each query receives a 14-component vector in the following order. Counts cover
the complete AST, including nested queries. Each value `v` is bucketed as
`min(v, 2)`: **0**, **1**, or **2 or more**. Boolean depth is bucketed the same way.

| Position | Feature | Current extraction rule |
|---|---|---|
| 1 | `joins` | Number of SQLGlot `Join` nodes |
| 2 | `outer_joins` | Join nodes whose side is LEFT, RIGHT, or FULL |
| 3 | `subqueries` | Maximum of explicit `Subquery` count and SELECT count minus one |
| 4 | `correlated_subqueries` | Nested scopes with qualified external column references |
| 5 | `set_operations` | UNION, INTERSECT, and EXCEPT nodes combined |
| 6 | `groups` | GROUP BY nodes |
| 7 | `having` | HAVING nodes |
| 8 | `distinct` | DISTINCT nodes, including those within aggregate expressions |
| 9 | `null_sensitive` | NULL nodes and COALESCE, IFNULL, or null-safe comparison nodes |
| 10 | `cases` | CASE nodes |
| 11 | `order_by` | ORDER nodes |
| 12 | `limits` | LIMIT nodes |
| 13 | `aggregates` | Nodes classified by SQLGlot as aggregate functions |
| 14 | `max_boolean_depth` | Maximum AND/OR/NOT count along an AST ancestry path |

These are implementation features rather than exhaustive semantic categories.
For example, the correlation detector can miss unqualified outer references;
NULL-sensitive behavior is broader than the listed nodes. Join count does not
distinguish inner, cross, or natural joins, and outer-join count combines LEFT,
RIGHT, and FULL. Set-operation count combines operator types and does not encode
ALL versus DISTINCT. The signature also omits literal values, identifiers,
comparison operators, arithmetic, casts, scalar-function identities, and the
location of each feature in the query.

Bucketing reduces fragmentation, but loses distinctions: a query with two joins
and one with five joins have the same join bucket. Other feature components may
still distinguish them.

## Pair groups

Let `F(q)` be the bucketed feature vector. The sampling group key is:

```text
(dbid, exact schema_ddl, ground_truth, sorted(F(q1), F(q2)))
```

The two query vectors are sorted lexicographically. This preserves the feature
combination on both sides while making the group insensitive to pair direction.
The label is part of this key, so EQ and NEQ pairs receive separate allocations.
This does not enforce a global 50/50 label balance or equal database coverage.

For example, under one schema and label, these pairs share a feature group:

```sql
-- Pair A
SELECT a FROM t WHERE a > 1;
SELECT a FROM t WHERE a > 2;

-- Pair B
SELECT a FROM t WHERE a > 10;
SELECT a FROM t WHERE a > 20;
```

They are not normalized duplicates because their literals differ. The feature
vectors are identical because neither query has any of the 14 counted features.
The example illustrates grouping in isolation; upstream complexity filtering
would normally exclude such simple pairs. The same grouping principle applies
to more complex pairs with matching counts.

## Selection policy

When enabled, sampling processes rows in input order and applies the following
precedence:

1. Preserve SQLGlot analysis failures and record the error.
2. Preserve every row belonging to a duplicate key with conflicting labels.
3. Remove a normalized duplicate of an already retained ordinary representative.
4. Otherwise, retain the row if its feature group has fewer than `cap` ordinary
   representatives; remove it if the group is full.

Only ordinary retained representatives populate the duplicate lookup and group
counts. Preserved conflicts and analysis failures bypass the cap, so the cap is
not an absolute maximum on all retained rows in a group. A duplicate of an
already discarded row may receive `feature_group_cap`, rather than
`duplicate_pair`, because discarded rows are not added to the duplicate lookup.
Audit reason counts therefore are not an exhaustive census of duplicate keys.

Setting the cap to zero disables feature sampling while retaining duplicate
removal and the preservation rules. Negative caps are rejected.

Selection is deterministic for fixed input order, code, and SQLGlot version.
It is **first-in-order selection**, not random sampling or a ranking of difficulty.
Reordering the source may change representatives. Group IDs are assigned in
encounter order and are local to a run, not stable cross-version identifiers.

## Running and reproducing sampling

Run from the repository root:

```bash
python -m scripts.datasets.preprocess_leetcode_dataset --max-per-group 2
```

Default paths:

| Artifact | Path |
|---|---|
| Raw input | `datasets/raw/old_evebench_output/leetcode/leetcode_CE_deepseek-chat.csv` |
| Output | `datasets/leetcode/evebench/leetcode.csv` |

Use `--input-file` and `--output-file` to override paths. Use
`--max-per-group 1` for stronger sampling, `3` for broader coverage, or `0` for
duplicate removal only. Omitting the option disables sampling. The raw input
must contain `dbid,index,schema,q1,q2,ground_truth`; preprocessing assigns the
MySQL dialect and LeetCode source fields.

The output CSV is written through a temporary file and atomically replaced. For
a reproducible experiment, retain the exact raw input and its row order, code
revision, SQLGlot version, cap, and output. Changing preprocessing criteria or
parser settings can change the eligible population and resulting counts.

The command saves only the final CSV. No per-row audit file is saved.

## Historical artifact snapshot

The files inspected when this document was written contain:

| Population | Total | EQ | NEQ |
|---|---:|---:|---:|
| Processed input | 974 | 545 | 429 |
| Reduced subset | 595 | 293 | 302 |

The historical audit recorded 431 groups, 595 ordinary representatives, 372 removals by group
cap, and 7 duplicate removals. No analysis-failure or conflicting-label exceptions
appear in this snapshot. The size reduction is 38.9%; the retained label split is
49.2% EQ and 50.8% NEQ. These are observed artifact statistics, not guarantees of
the sampling policy. The near-balanced labels result from this input and grouping.

## Interpretation and future difficulty evaluation

Sampling reduces repeated feature patterns while retaining representatives from
each observed database/schema/label/signature group. It does not guarantee
coverage of every SQL operator, join subtype, bug, or equivalence-reasoning pattern.
It can discard distinct difficult cases and retain easy cases. First-in-order
selection can also inherit ordering bias from the legacy results. Scores on the
subset describe that selected population, not an unbiased estimate for the full
input dataset.

A claim of empirical difficulty requires a separate experiment: run multiple
equivalence checkers under recorded resource limits, distinguish unsupported
syntax and features from reasoning failures or timeouts, and investigate label
conflicts or uncertain ground truth. Structural complexity, parser compatibility,
feature diversity, and observed solver difficulty should be reported separately.

The regression test in `tests/test_preprocess_leetcode_dataset.py` checks
reversed-pair deduplication, the group cap, conflict and parse-failure
preservation, output reindexing, and deduplication-only mode.
