# Counterexamples: All 595 Query Pairs

Counterexamples were executed in isolated schema-only test databases.
Only INSERT statements are accepted; constraints are enabled.
Q1 and Q2 are compared as complete result bags with output order ignored.
Errors are not evidence of inequivalence. Equal outputs do not prove equivalence.
Counterexample arrays containing only nonempty SQL strings are normalized before validation.
No automatic SQL repairs were performed.
Selected pairs: 595. Databases: 1045: 31, 1050: 11, 1075: 22, 1083: 61, 1084: 73, 1097: 36, 1113: 20, 1127: 11, 1141: 27, 1148: 12, 1149: 16, 175: 11, 182: 24, 183: 15, 511: 7, 512: 10, 550: 37, 577: 12, 585: 13, 596: 13, 603: 19, 607: 51, 610: 1, 614: 47, 619: 15. Original labels: EQ: 293, NEQ: 302.
Outputs store at most 100 preview rows; comparisons use up to 100,000 complete rows.

| Prompt | Model | Pairs | Generated | Declared EQ | Valid | Valid / Pairs | Status Counts |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| CE | Kwai-AutoSQL-14B | 595 | 593 | 0 | 86 | 14.5% | {'error': 75, 'same_output': 432, 'valid': 86, 'generation_error': 2} |
| CE | Qwen3-14B | 595 | 590 | 0 | 95 | 16.0% | {'error': 102, 'same_output': 393, 'valid': 95, 'generation_error': 5} |
| CE | XiYanSQL-QwenCoder-7B-2504 | 595 | 590 | 0 | 20 | 3.4% | {'same_output': 475, 'error': 95, 'no_counterexample': 4, 'valid': 20, 'generation_error': 1} |
| DF | Kwai-AutoSQL-14B | 595 | 274 | 318 | 42 | 7.1% | {'declared_equivalent': 318, 'error': 72, 'same_output': 160, 'valid': 42, 'generation_error': 3} |
| DF | Qwen3-14B | 595 | 131 | 454 | 57 | 9.6% | {'declared_equivalent': 454, 'error': 46, 'valid': 57, 'generation_error': 10, 'same_output': 28} |
| DF | XiYanSQL-QwenCoder-7B-2504 | 595 | 444 | 149 | 12 | 2.0% | {'declared_equivalent': 149, 'same_output': 378, 'error': 54, 'valid': 12, 'generation_error': 2} |

## Reproduce

```sh
python evebench_2/revalidate_generated_runs.py ../evebench_runs/leetcode
```
