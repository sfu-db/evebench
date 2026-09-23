# Counterexamples: All 709 Query Pairs

Each of three models receives the same query pairs with CE and DF separately.
Source databases are unchanged. Test databases contain schema only plus missing CSV primary and foreign keys.
Validity requires successful INSERTs with constraints enabled and different result bags.
Before execution, matching outer LIMIT N clauses (nonnegative integer literals) are removed from both queries.
Unequal or one-sided limits, nested limits, and OFFSET clauses remain unchanged; original q1/q2 are preserved.
shared_limit_removed records N; q1_validation_sql/q2_validation_sql record the queries actually executed.
Errors are not evidence of inequivalence. Equal outputs do not prove equivalence.
No counterexample (N) includes omitted counterexamples and declared equivalence; it does not mean proven equivalence.
no_counterexample means CE returned an empty SQL string, with the model's explanation retained.
generation_error means an API or response-format failure; error means SQL validation failed.
No automatic retries or SQL repairs were performed in this run.
Selected pairs: 709. Databases: dsb: 158, so: 66, tpcds: 382, tpch: 103. Original labels: EQ: 503, NEQ: 206.
Result CSVs include original columns, counterexample, counterexample_valid, status, errors, and raw replies.
Outputs store at most 100 preview rows; comparisons use up to 100,000 complete rows.

| Prompt | Model | Pairs | Generated | Valid | Valid / Pairs | Status Counts |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| CE | gemini-3.7-flash | 709 | 676 | 204 | 28.8% | {'valid': 204, 'same_output': 430, 'error': 42, 'generation_error': 31, 'no_counterexample': 2} |
| CE | gpt-5.6-luna | 709 | 263 | 184 | 26.0% | {'valid': 184, 'no_counterexample': 392, 'error': 18, 'same_output': 61, 'generation_error': 54} |
| CE | gpt-5.6-sol | 709 | 207 | 192 | 27.1% | {'valid': 192, 'no_counterexample': 334, 'error': 4, 'generation_error': 168, 'same_output': 11} |
| DF | gemini-3.7-flash | 709 | 190 | 176 | 24.8% | {'valid': 176, 'declared_equivalent': 518, 'error': 11, 'same_output': 3, 'generation_error': 1} |
| DF | gpt-5.6-luna | 709 | 201 | 179 | 25.2% | {'valid': 179, 'declared_equivalent': 492, 'error': 11, 'generation_error': 16, 'same_output': 11} |
| DF | gpt-5.6-sol | 709 | 201 | 193 | 27.2% | {'valid': 193, 'declared_equivalent': 485, 'error': 7, 'generation_error': 23, 'same_output': 1} |

## Label Conflicts

Counterexamples are valid under the shared-LIMIT removal policy despite an existing EQ label for indices: 1, 3, 5, 10, 11, 12, 13, 19, 20, 24, 25, 26, 27, 63, 71, 85, 92, 98, 103, 106, 109, 111, 132, 143, 146, 150, 160, 170, 176, 177, 203, 215, 222, 226, 234, 273, 297, 298, 302, 321, 333, 344, 379, 381, 394, 396, 398, 403, 407, 408, 420, 446, 454, 489, 503, 546, 548, 550, 553, 556, 557, 564, 569, 571, 572, 581, 582, 604, 611, 612, 613, 616, 639, 644, 651, 656, 663, 676, 695.
When a LIMIT is removed, validity describes the modified query pair, not necessarily the original limited pair.
Original ground_truth values are preserved. These are schema-only counterexamples; dataset-specific
assumptions such as calendar surrogate keys matching actual date offsets are not imposed.

## Retained Results

The six `postgres_*.csv` files retain all final records, including raw model replies,
counterexamples, validation outcomes, and execution SQL where validation was attempted.
`summary.csv` and `summary.json` contain the combined statistics; `run.json` preserves
the original configuration and input/prompt hashes. Input files and runner code remain in the parent directory.

Pilot outputs, checkpoints, logs, schema exports, database preparation metadata, and
duplicate per-group summaries were removed after the final integrity audit at the user's request.
Checkpoint-based resume and `--revalidate-only` are no longer available for this archive.
Use a new output directory and separate test databases for any new experiment; do not run the runner against this results directory.
