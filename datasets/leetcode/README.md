# LeetCode preprocessing

From the repository root, run:

```bash
python -m scripts.datasets.preprocess_leetcode_dataset
```

The default input is `datasets/raw/old_evebench_output/leetcode/leetcode_CE_deepseek-chat.csv`;
the output is `datasets/leetcode/evebench/leetcode.csv`. Override either path with
`--input-file` and `--output-file`.

Output columns match the SQLite and Postgres datasets:
`dbid,index,source_index,schema_ddl,q1,q2,dialect,ground_truth,source`.
The original `index` becomes `source_index`; output indices start at 1.
`schema` becomes `schema_ddl`; SQL and `ground_truth` labels are preserved.
`dialect` is `mysql` and `source` is `leetcode`.

Incomplete pairs, exact duplicates within a database/schema, SQLGlot
formatting-identical pairs, pairs containing window functions on either side,
and pairs with either query rejected by Calcite
are removed. Calcite uses MySQL lexical rules, including backtick identifiers,
and requires exactly one query per side. Before checking, SQLGlot renders a
temporary MySQL copy with quoted identifiers. This avoids keyword collisions
such as aliases named `DAY` and columns named `FREE`, without quoting interval
units or changing literals. Original query text is retained in the output.
SQLGlot parse failures, non-query statements, and multiple statements are sent
to Calcite unchanged. Rejections refer to the checked copy and are logged to stderr.
Valid MySQL syntax unsupported by Calcite can also be removed. This is syntax
filtering only: schemas, function resolution, execution, and equivalence labels
are not independently validated. Legacy model responses are not used as labels.
Window functions (including named `OVER` windows and those inside subqueries)
are excluded as a benchmark restriction; PostgreSQL itself supports them.

Build `baselines/qed/parser` first so its dependency-bundled parser JAR exists.
`EVEBENCH_QED_PARSER_JAR` and `EVEBENCH_QED_JAVA` can override the JAR and Java paths.
A parser setup failure stops conversion without replacing an existing output.

SQLGlot pair complexity filtering requires complex logic in both queries, using
`scripts/datasets/shared/features.py` with MySQL parsing. Original SQL, schemas,
and labels are preserved. Rejection reasons are logged to stderr, including
SQLGlot analysis failures. Complexity is a difficulty heuristic, not a proof
of equivalence or non-equivalence. There are no selectable complexity profiles.

Complex features include subqueries, set operations, outer or multiple joins,
grouping, HAVING, DISTINCT, CASE, deeper predicates, and ORDER BY with LIMIT.
Simple projections, filters, arithmetic, single inner joins, and ungrouped
aggregates alone do not meet the complexity requirement.

## Optional representative sampling

See the [grouping and sampling design](../../docs/leetcode-pair-sampling.md)
for feature definitions, selection rules and interpretation limits.

Pass `--max-per-group 2` to the preprocessing command to sample the eligible
pairs before writing `leetcode.csv`. This avoids an intermediate processed CSV.
Sampling removes duplicate pairs after MySQL formatting normalization and
treating query order as interchangeable, then keeps the first two pairs per
feature group. Identifier case and literals remain significant for deduplication.

Groups share database ID, exact schema, label, and an unordered pair of feature
vectors. Features are joins, outer joins, subqueries, correlated subqueries,
set operations, groups, HAVING, DISTINCT, NULL-sensitive constructs, CASE,
ordering, limits, aggregates, and boolean depth. Each count is bucketed as
0, 1, or 2+. This is deterministic diversity sampling, not proof of redundancy:
it can remove distinct bugs and reasoning patterns within a group. It does not
guarantee coverage of every join subtype or operator. Original SQL, schema,
labels, and source indices are preserved. Conflicting labels on duplicate pairs
and analysis failures are retained.

Omitting the option disables sampling. Use `--max-per-group 1` for a smaller
subset, `3` for broader coverage, or `0` for duplicate removal only. No per-row
audit file is saved.
