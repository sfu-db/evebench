# PostgreSQL LLM Solver Analysis

Dataset: `/workspaces/evebench/datasets/postgres/evebench/postgres.csv` (709 cases).
Results: `/workspaces/evebench/outputs/Sep2026/full709`.

DF reports EQ for an explicit Y verdict and NEQ only for an N verdict with a
validated counterexample. CE likewise reports NEQ only for a validated
counterexample. Invalid, failed, and missing counterexamples are UNKNOWN.
NEQ is the positive class: FP means
ground-truth EQ reported as NEQ, and FN means ground-truth NEQ reported as EQ.
Standard FPR/FNR exclude UNKNOWN outcomes; the two `all actual` columns retain them
in the relevant ground-truth-class denominator.

## Performance

| Prompt | Model | EQ | NEQ | UNKNOWN | Coverage | FP | FN | FPR | FNR | FP / all EQ | FN / all NEQ |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CE | gemini-3.7-flash | 0 | 204 | 505 | 28.77% | 72 | 0 | 100.00% | 0.00% | 14.31% | 0.00% |
| CE | gpt-5.6-luna | 0 | 184 | 525 | 25.95% | 67 | 0 | 100.00% | 0.00% | 13.32% | 0.00% |
| CE | gpt-5.6-sol | 0 | 192 | 517 | 27.08% | 71 | 0 | 100.00% | 0.00% | 14.12% | 0.00% |
| DF | gemini-3.7-flash | 518 | 176 | 15 | 97.88% | 62 | 85 | 12.53% | 42.71% | 12.33% | 41.26% |
| DF | gpt-5.6-luna | 492 | 179 | 38 | 94.64% | 68 | 74 | 13.99% | 40.00% | 13.52% | 35.92% |
| DF | gpt-5.6-sol | 485 | 193 | 31 | 95.63% | 77 | 68 | 15.59% | 36.96% | 15.31% | 33.01% |

## Model disagreements

A disagreement means at least two models under the same prompt have different
EQ/NEQ/UNKNOWN conclusions. Full case details are in `disagreements.csv`.

- DF disagreements: 93
- CE disagreements: 53

## Ground-truth EQ conflicts

At least one model generated a validated counterexample for 79 cases
currently labeled EQ. `label_conflict_cases.csv` contains one row per case, and
`label_conflict_evidence.csv` retains every model counterexample supporting the audit.

- 33 cases have a valid counterexample against the original queries; these are candidates to relabel as NEQ.
- 46 cases conflict only after a shared outer LIMIT was removed; do not relabel these without reviewing that policy.

The source dataset is not modified automatically. The evidence should be reviewed before
changing labels, especially where the intended benchmark semantics include assumptions
that are not expressed as schema constraints.
