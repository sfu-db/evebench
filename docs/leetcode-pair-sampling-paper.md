# Pair-Aware Structural Sampling for SQL Equivalence Benchmarks

*A methods draft based on the current EveBench LeetCode implementation.*

## Abstract

SQL equivalence benchmarks consist of query pairs evaluated under a database
schema. Reducing such a benchmark requires a selection policy that accounts for
both queries and the context in which their equivalence is judged. We describe
a deterministic method that combines normalized duplicate detection with
schema- and label-conditioned structural grouping. Each query is represented
by a bucketed vector of 14 syntactic features, and each pair is represented by
the unordered combination of its two vectors. The method retains a bounded
number of representatives per group while preserving analysis failures and
conflicting labels for investigation. Applied to a processed LeetCode dataset,
the implementation selects 595 of 974 pairs, a 38.9% reduction, while retaining
all 431 observed groups under the chosen representation. The subset contains
293 equivalent and 302 non-equivalent pairs. These results establish a reduction
in dataset size with coverage of the defined structural groups; they do not
establish that retained pairs are uniformly difficult or that solver performance
on the subset estimates performance on the full dataset.

## 1. Motivation

An equivalence benchmark asks whether two queries produce the same observable
result across the database instances allowed by a schema and its constraints.
The reasoning required can depend on joins, aggregation, duplicate behavior,
NULL handling, nested queries, and interactions between these constructs.
Consequently, query length alone provides an incomplete basis for selecting
benchmark cases. More importantly, the evaluation unit is a pair: a query that
is complicated in isolation may be compared with an almost identical query,
while a comparatively small pair may expose a subtle semantic difference.

The practical objective of this work is to obtain a smaller evaluation set
without selecting cases solely by their textual size or their position in the
input. Our implementation still uses input order to choose representatives,
but constrains that choice through groups defined by the structures of both
queries, their schema context, and their source label. This limits the number
of representatives assigned to repeated feature combinations and makes the
selection policy inspectable.

The central idea is to distinguish three questions: whether a case is eligible
for the benchmark, whether its observable structural pattern is represented,
and whether it is empirically difficult for an equivalence checker. The current
pipeline addresses eligibility and structural representation. Difficulty remains
an empirical question requiring separate solver experiments.

## 2. Problem formulation and design objectives

Let a benchmark record be `r = (b, S, q1, q2, y)`, where `b` identifies the
database, `S` is its schema DDL, and `y` is the supplied EQ or NEQ label. Given
an ordered dataset `D`, the task is to select a subset `R` while retaining the
original queries, schema, label, and source provenance of each selected record.
This work assumes the labels supplied by the dataset; it does not independently
prove them.

We adopt four design objectives:

1. **Pair awareness.** Selection should represent the structures on both sides
   of the equivalence question, rather than classify each query independently.
2. **Context preservation.** Pairs from different databases, schemas, or label
   strata should not compete for the same representative allocation.
3. **Controlled reduction.** A simple parameter should limit representation of
   frequently occurring structural groups without requiring a target dataset size.
4. **Auditability.** Every input record should receive a recorded decision, and
   uncertain analysis or conflicting labels should remain visible.

These objectives define a structural selection method, not a search for the
smallest semantically complete benchmark. In particular, preserving a group
under an abstraction does not imply preserving all behaviors represented by
its members.

## 3. Method

### 3.1 Eligibility before sampling

The sampler operates on a preprocessed dataset. The upstream LeetCode pipeline
standardizes metadata, removes incomplete and formatting-identical pairs,
excludes window functions under the current benchmark policy, and checks query
syntax through Calcite. A temporary SQLGlot rendering quotes identifiers for
that check, avoiding collisions such as a column named `FREE` or an alias named
`DAY`; stored query text is preserved. A configurable upstream complexity filter
uses structural features to exclude selected simple pairs.

This separation matters for interpreting the result. Sampling does not repair
schemas, execute queries, validate equivalence labels, or establish MySQL runtime
validity. Its population is the processed input, not the entire legacy corpus.
Changes to eligibility rules may change both the population and the sampled
subset.

### 3.2 Normalized pair identity

Let `N(q)` denote SQLGlot's MySQL rendering of one parsed query with comments
removed. We define a normalized duplicate key as:

```text
Kdup(r) = (b, S, sort(N(q1), N(q2)))
```

The ordering operation makes the key invariant to exchanging the two queries.
Equivalence is symmetric, so reversed pairs ask the same question, although the
original direction remains in the stored record. Exact schema text is retained
in the key because constraints can affect the interpretation of equivalence.
Identifier case and literal values remain significant; there is no alias erasure
or semantic rewrite proof.

The source label is excluded from the duplicate key. A preliminary pass therefore
can detect a normalized pair appearing with multiple labels. All records in such
a conflict are preserved and flagged. Normalized identity is an operational
criterion for duplication, not a guarantee derived from independently verified
SQL semantics.

### 3.3 Structural representation of individual queries

SQLGlot supplies a query AST and scope information. The implementation extracts
14 quantities, grouped conceptually as follows:

| Aspect | Features |
|---|---|
| Relational structure | Joins, outer joins, subqueries, correlated subqueries, set operations |
| Aggregation and multiplicity | GROUP BY, HAVING, DISTINCT, aggregate functions |
| Conditional behavior | NULL-sensitive constructs, CASE, maximum boolean depth |
| Result selection | ORDER BY, LIMIT |

For feature `i` with nonnegative value `fi(q)`, define:

```text
phi_i(q) = min(fi(q), 2)
phi(q)   = (phi_1(q), ..., phi_14(q))
```

Thus each component records absence, a single occurrence, or multiple occurrences.
This coarse representation reduces the fragmentation that exact counts would
introduce. It intentionally sacrifices detail: two and five joins receive the
same join bucket, and LEFT, RIGHT, and FULL joins share an outer-join component.
The feature order and exact extraction rules are specified in the
[implementation design](leetcode-pair-sampling.md).

### 3.4 Pair groups and representative allocation

The sampling key combines both query vectors with their context:

```text
Kgroup(r) = (b, S, y, sort(phi(q1), phi(q2)))
```

Sorting the vectors makes grouping insensitive to pair direction without
collapsing the two vectors into a single summed feature count. Keeping the label
in the group key gives EQ and NEQ examples separate allocations. It does not
force global label balance or equal representation of databases.

A configurable cap `k` controls how many ordinary representatives are retained
per group. The default is `k = 2`. Rows are processed in input order, and the first
eligible representatives are retained. The choice of two is an operational
default allowing more than one example per group; no empirical optimality claim
is made. The implementation is deterministic, rather than a probability sample.
A cap of zero is a special mode that disables group sampling and performs only
duplicate removal with the preservation rules.

### 3.5 Selection procedure and exceptions

The implementation performs two passes. The first parses records, constructs
keys, and collects labels per duplicate key. The second applies the following
procedure:

```text
for each record r in input order:
    if structural analysis failed:
        retain r and record the analysis error
    else if Kdup(r) has conflicting labels:
        retain r and flag the conflict
    else if Kdup(r) belongs to an ordinary retained representative:
        discard r as a duplicate
    else if sampling is disabled or Kgroup(r) has fewer than k representatives:
        retain r; update the duplicate lookup and group count
    else:
        discard r because its group is full
```

Preserved failures and label conflicts bypass the group cap and do not increment
ordinary group counts. This makes uncertainty visible rather than interpreting
an analysis failure as evidence that a case is uninteresting. It also means the
cap is not an absolute bound on every retained record in a group.

For positive `k`, every successfully analyzed group with an ordinary candidate
receives a representative; conflict records also remain present. This is a
coverage property of the selected abstraction. It is not a coverage guarantee
for SQL semantics, operator subtypes, or solver failure modes.

## 4. Illustrative grouping example

Consider two pairs under the same schema and source label. Pair A compares a
query using one LEFT JOIN with another query using one correlated subquery.
Pair B compares queries with the same respective feature vectors but different
predicates or column references. They are not necessarily normalized duplicates,
and their equivalence arguments may differ. Nevertheless, if all 14 bucketed
components match on the respective sides, they belong to the same sampling group.

With `k = 2`, both can be retained. A third distinct pair with that group key is
removed by the cap. A pair assigned the opposite EQ/NEQ label or a different
schema belongs to another group and receives a separate allocation. Reversing
the two queries does not change either pair's group.

The example captures both the benefit and the limitation of the method: it
controls repeated structural patterns while allowing distinct semantic cases
to be discarded within each pattern.

## 5. Observed dataset reduction

The current artifacts, inspected on 2026-09-23, provide the following descriptive
results for the processed LeetCode input and a cap of two:

| Population | Pairs | EQ | NEQ |
|---|---:|---:|---:|
| Processed input | 974 | 545 | 429 |
| Selected subset | 595 | 293 | 302 |

The subset retains all 431 observed feature groups. The historical audit recorded 595
representatives, 372 removals by the group cap, and 7 duplicate removals, with no
preserved analysis failures or label conflicts in this snapshot. Overall, 379
pairs are removed, reducing the dataset by 38.9%. The retained label distribution
is 49.2% EQ and 50.8% NEQ; this near balance is an observed outcome, not an enforced
quota.

The duplicate-removal count describes decisions made by this procedure. It is
not an exhaustive duplicate census: only retained ordinary representatives enter
the duplicate lookup, so repeats of previously discarded records can receive
the group-cap reason instead.

These observations demonstrate size reduction and coverage of the defined
groups. They do not measure solver runtime savings, preserve a known distribution
of difficulty, or show that checker rankings remain unchanged. No such solver
evaluation is reported here.

## 6. Limitations and evaluation agenda

The main limitation is the gap between syntax and reasoning difficulty. A
syntactically complex pair may admit an immediate equivalence argument; a short
pair may depend on subtle NULL or duplicate semantics. The current vectors also
omit expression identities and operator placement. Scope-based correlation
counts are heuristic, and different join and set-operation variants can share
a signature. Feature coverage should therefore not be presented as semantic or
behavioral coverage.

Input-order selection introduces another limitation. Given identical input,
code, and parser versions, selection is reproducible. Reordering the input can
change representatives, and the first examples may reflect biases in how the
legacy corpus was assembled. Furthermore, group conditioning changes the
relative weight of databases and labels. Accuracy on this subset is a measure
on the selected population, not an unbiased estimate of accuracy on the full
processed corpus.

A subsequent evaluation should test three questions rather than assume them:

- **Coverage sensitivity:** How do cap values and finer signatures change size,
  operator coverage, and the retained patterns of query rewrites?
- **Selection sensitivity:** How stable are results under alternative input
  orderings or a separately implemented seeded random policy within groups?
- **Solver sensitivity:** Across several equivalence checkers, how do full-input
  and subset results compare in accuracy, runtime, timeouts, and failure categories?

Solver experiments should distinguish parsing and unsupported-feature failures
from reasoning failures. Ground-truth disagreements require independent review.
An empirical hard subset could later be defined using recorded checker outcomes
and resource limits, but that would be an additional selection stage rather than
a conclusion of the structural method.

## 7. Reproducibility and conclusion

The implementation is integrated into
`scripts/datasets/preprocess_leetcode_dataset.py`. Running
`python -m scripts.datasets.preprocess_leetcode_dataset --max-per-group 2`
filters and samples the raw input in one pass. No audit file is saved. Retained
SQL, schemas, and labels are unchanged; output indices are reassigned while
source indices remain available. Reproduction requires the
exact input order, parser version, code revision, and cap, not merely the source
dataset name.

The method offers a transparent way to reduce a SQL equivalence benchmark by
allocating representatives to the joint structures of query pairs. Its present
contribution is an implemented, auditable selection policy and a measured
reduction on the LeetCode artifacts. The appropriate claim is structural group
coverage in a smaller subset. Demonstrating semantic coverage or empirical
hardness requires further evaluation.
