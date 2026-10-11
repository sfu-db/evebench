# Counterexamples: All 254 Query Pairs

Counterexamples were executed in isolated schema-only test databases.
Only INSERT statements are accepted; constraints are enabled.
Q1 and Q2 are compared as complete result bags with output order ignored.
Errors are not evidence of inequivalence. Equal outputs do not prove equivalence.
Counterexample arrays containing only nonempty SQL strings are normalized before validation.
No automatic SQL repairs were performed.
Selected pairs: 254. Databases: beers: 254. Original labels: NEQ: 254.
Outputs store at most 100 preview rows; comparisons use up to 100,000 complete rows.

| Prompt | Model | Pairs | Generated | Declared EQ | Valid | Valid / Pairs | Status Counts |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| CE | Kwai-AutoSQL-14B | 254 | 253 | 0 | 164 | 64.6% | {'valid': 164, 'error': 85, 'generation_error': 1, 'same_output': 4} |
| CE | Qwen3-14B | 254 | 254 | 0 | 162 | 63.8% | {'error': 85, 'valid': 162, 'same_output': 7} |
| CE | XiYanSQL-QwenCoder-7B-2504 | 254 | 254 | 0 | 99 | 39.0% | {'error': 115, 'same_output': 40, 'valid': 99} |
| DF | Kwai-AutoSQL-14B | 254 | 237 | 17 | 100 | 39.4% | {'declared_equivalent': 17, 'error': 133, 'same_output': 4, 'valid': 100} |
| DF | Qwen3-14B | 254 | 241 | 13 | 114 | 44.9% | {'valid': 114, 'declared_equivalent': 13, 'error': 122, 'same_output': 5} |
| DF | XiYanSQL-QwenCoder-7B-2504 | 254 | 251 | 3 | 89 | 35.0% | {'declared_equivalent': 3, 'error': 124, 'valid': 89, 'same_output': 38} |

## Reproduce

```sh
python evebench_2/revalidate_generated_runs.py ../evebench_runs/beers
```
