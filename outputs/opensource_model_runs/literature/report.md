# Counterexamples: All 56 Query Pairs

Counterexamples were executed in isolated schema-only test databases.
Only INSERT statements are accepted; constraints are enabled.
Q1 and Q2 are compared as complete result bags with output order ignored.
Errors are not evidence of inequivalence. Equal outputs do not prove equivalence.
Counterexample arrays containing only nonempty SQL strings are normalized before validation.
No automatic SQL repairs were performed.
Selected pairs: 56. Databases: 0: 1, 1: 1, 10: 1, 11: 1, 12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 3, 19: 1, 2: 2, 20: 1, 21: 1, 22: 1, 23: 1, 24: 1, 26: 1, 28: 1, 29: 8, 3: 1, 30: 2, 31: 2, 32: 14, 4: 1, 5: 1, 6: 2, 7: 1, 8: 1. Original labels: EQ: 28, NEQ: 28.
Outputs store at most 100 preview rows; comparisons use up to 100,000 complete rows.

| Prompt | Model | Pairs | Generated | Declared EQ | Valid | Valid / Pairs | Status Counts |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| CE | Kwai-AutoSQL-14B | 56 | 56 | 0 | 26 | 46.4% | {'same_output': 29, 'error': 1, 'valid': 26} |
| CE | Qwen3-14B | 56 | 56 | 0 | 27 | 48.2% | {'error': 3, 'same_output': 26, 'valid': 27} |
| CE | XiYanSQL-QwenCoder-7B-2504 | 56 | 54 | 0 | 11 | 19.6% | {'error': 7, 'same_output': 36, 'valid': 11, 'no_counterexample': 1, 'generation_error': 1} |
| DF | Kwai-AutoSQL-14B | 56 | 41 | 14 | 19 | 33.9% | {'declared_equivalent': 14, 'same_output': 21, 'valid': 19, 'error': 1, 'generation_error': 1} |
| DF | Qwen3-14B | 56 | 29 | 27 | 24 | 42.9% | {'declared_equivalent': 27, 'valid': 24, 'same_output': 4, 'error': 1} |
| DF | XiYanSQL-QwenCoder-7B-2504 | 56 | 46 | 9 | 8 | 14.3% | {'error': 3, 'same_output': 35, 'valid': 8, 'declared_equivalent': 9, 'generation_error': 1} |

## Reproduce

```sh
python evebench_2/revalidate_generated_runs.py ../evebench_runs/literature
```
