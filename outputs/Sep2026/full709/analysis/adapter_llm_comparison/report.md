# Adapter–LLM Complementarity

Adapter benchmark run: `1`. Cases: 709.
Adapters: parseval, qed, spes, sqlsolver, verieql.
LLM runs: CE_gemini-3.7-flash, CE_gpt-5.6-luna, CE_gpt-5.6-sol, DF_gemini-3.7-flash, DF_gpt-5.6-luna, DF_gpt-5.6-sol.

The directional sets are label-aware: a side solves a case when at least one of its
selected systems returns the current ground-truth verdict. A conclusive but opposing
verdict does not count as solving the case. Raw EQ/NEQ coverage is reported separately.
Because some current labels are under audit, these are empirical label agreements, not
independent certifications of semantic correctness.

## Ensemble comparison

| Relation | Correctly solved | Raw decisive coverage |
| --- | ---: | ---: |
| adapter_only | 53 | 0 |
| llm_only | 169 | 84 |
| both | 402 | 625 |
| neither | 85 | 0 |

Cases with at least one cross-method EQ/NEQ conflict: 172.

Directional failure modes:

- Of the 53 adapter-only cases, the LLM ensemble was wrong on 53 and abstained on 0.
- Of the 169 LLM-only cases, the adapter ensemble was wrong on 100 and abstained on 69.

- `adapter_only.csv`: adapters solve the current label and no LLM run does.
- `llm_only.csv`: an LLM run solves the current label and no adapter does.
- `adapter_only_decisive.csv` and `llm_only_decisive.csv`: the literal raw-verdict
  comparison, regardless of the current label.
- `cases.csv`: all cases, individual outcomes, failure statuses, and conflict flags.
- `pairwise_summary.csv`: every adapter versus every LLM run.

## Pairwise comparison

| Adapter | LLM run | Adapter only | LLM only | Both | Neither |
| --- | --- | ---: | ---: | ---: | ---: |
| parseval | CE_gemini-3.7-flash | 430 | 115 | 17 | 147 |
| parseval | CE_gpt-5.6-luna | 429 | 99 | 18 | 163 |
| parseval | CE_gpt-5.6-sol | 430 | 104 | 17 | 158 |
| parseval | DF_gemini-3.7-flash | 59 | 159 | 388 | 103 |
| parseval | DF_gpt-5.6-luna | 72 | 154 | 375 | 108 |
| parseval | DF_gpt-5.6-sol | 73 | 159 | 374 | 103 |
| qed | CE_gemini-3.7-flash | 28 | 132 | 0 | 549 |
| qed | CE_gpt-5.6-luna | 28 | 117 | 0 | 564 |
| qed | CE_gpt-5.6-sol | 28 | 121 | 0 | 560 |
| qed | DF_gemini-3.7-flash | 0 | 519 | 28 | 162 |
| qed | DF_gpt-5.6-luna | 0 | 501 | 28 | 180 |
| qed | DF_gpt-5.6-sol | 0 | 505 | 28 | 176 |
| spes | CE_gemini-3.7-flash | 16 | 132 | 0 | 561 |
| spes | CE_gpt-5.6-luna | 16 | 117 | 0 | 576 |
| spes | CE_gpt-5.6-sol | 16 | 121 | 0 | 572 |
| spes | DF_gemini-3.7-flash | 0 | 531 | 16 | 162 |
| spes | DF_gpt-5.6-luna | 0 | 513 | 16 | 180 |
| spes | DF_gpt-5.6-sol | 0 | 517 | 16 | 176 |
| sqlsolver | CE_gemini-3.7-flash | 36 | 132 | 0 | 541 |
| sqlsolver | CE_gpt-5.6-luna | 36 | 117 | 0 | 556 |
| sqlsolver | CE_gpt-5.6-sol | 36 | 121 | 0 | 552 |
| sqlsolver | DF_gemini-3.7-flash | 1 | 512 | 35 | 161 |
| sqlsolver | DF_gpt-5.6-luna | 1 | 494 | 35 | 179 |
| sqlsolver | DF_gpt-5.6-sol | 1 | 498 | 35 | 175 |
| verieql | CE_gemini-3.7-flash | 26 | 128 | 4 | 551 |
| verieql | CE_gpt-5.6-luna | 27 | 114 | 3 | 565 |
| verieql | CE_gpt-5.6-sol | 26 | 117 | 4 | 562 |
| verieql | DF_gemini-3.7-flash | 1 | 518 | 29 | 161 |
| verieql | DF_gpt-5.6-luna | 0 | 499 | 30 | 180 |
| verieql | DF_gpt-5.6-sol | 0 | 503 | 30 | 176 |
