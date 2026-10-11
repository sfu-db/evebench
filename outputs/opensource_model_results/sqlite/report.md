# Counterexamples: All 878 Query Pairs

Counterexamples were executed in isolated schema-only test databases.
Only INSERT statements are accepted; constraints are enabled.
Q1 and Q2 are compared as complete result bags with output order ignored.
Errors are not evidence of inequivalence. Equal outputs do not prove equivalence.
Counterexample arrays containing only nonempty SQL strings are normalized before validation.
No automatic SQL repairs were performed.
Selected pairs: 878. Databases: california_schools: 67, card_games: 94, codebase_community: 69, debit_card_specializing: 45, european_football_2: 76, financial: 73, formula_1: 125, student_club: 68, superhero: 58, thrombosis_prediction: 118, toxicology: 85. Original labels: EQ: 170, NEQ: 708.
Outputs store at most 100 preview rows; comparisons use up to 100,000 complete rows.

| Prompt | Model | Pairs | Generated | Declared EQ | Valid | Valid / Pairs | Status Counts |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| CE | Kwai-AutoSQL-14B | 878 | 873 | 0 | 330 | 37.6% | {'error': 399, 'valid': 330, 'same_output': 144, 'generation_error': 5} |
| CE | Qwen3-14B | 878 | 874 | 0 | 358 | 40.8% | {'error': 363, 'valid': 358, 'same_output': 153, 'generation_error': 4} |
| CE | XiYanSQL-QwenCoder-7B-2504 | 878 | 866 | 0 | 148 | 16.9% | {'error': 493, 'no_counterexample': 5, 'valid': 148, 'same_output': 225, 'generation_error': 7} |
| DF | Kwai-AutoSQL-14B | 878 | 627 | 247 | 233 | 26.5% | {'declared_equivalent': 247, 'error': 322, 'valid': 233, 'same_output': 72, 'generation_error': 4} |
| DF | Qwen3-14B | 878 | 632 | 243 | 285 | 32.5% | {'error': 315, 'declared_equivalent': 243, 'valid': 285, 'same_output': 32, 'generation_error': 3} |
| DF | XiYanSQL-QwenCoder-7B-2504 | 878 | 722 | 149 | 131 | 14.9% | {'declared_equivalent': 149, 'error': 432, 'same_output': 159, 'valid': 131, 'generation_error': 7} |

## Reproduce

```sh
python evebench_2/revalidate_generated_runs.py ../evebench_runs/sqlite
```
