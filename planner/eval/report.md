# Gate 3 report

Backend: Claude Code CLI on Pedro's subscription (quality run, no per-token charges).

**Latency, tokens and cost on this backend are not representative of the API.** Each call starts a CLI process, adds Claude Code's own overhead and tool scaffolding, and has no prompt-cache control or server-side refusal fallback. Only the quality columns (outcomes, first-try validity, errors) carry over. `notional` is the CLI's own list-price figure and was not billed.

Thresholds: first-try validity >= 70%, `llm_failed_fallback` <= 10% (of requests that have a valid plan at all), p95 total latency <= 30 s (API run only), and Pedro rates at least 8 of 10 sampled plans acceptable (see `sample-for-review.md`).

| model | effort | requests | llm_valid | llm_failed_fallback | no_plan | first-try valid | fallback share | mean attempts | p95 latency | tokens in / out | notional or cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5-5 | medium | 50 | 47 | 3 | 0 | 70% | 6% | 1.44 | 60.6 s | 991886 / 105811 | notional $4.54 |
| claude-sonnet-5-5 | low | 50 | 46 | 3 | 1 | 65% | 6% | 1.48 | 34.4 s | 989792 / 80305 | notional $2.46 |
| claude-opus-5-5 | medium | 50 | 45 | 4 | 1 | 45% | 8% | 1.68 | 106.9 s | 1206995 / 131098 | notional $10.76 |

Errors by code (failed drafts):

- claude-sonnet-5-5 medium: {'visit_overruns': 9, 'meal_missing': 8, 'wait_too_long': 7, 'too_many_stops': 3, 'meal_outside_window': 3, 'unknown_place': 2, 'too_few_stops': 1, 'schema': 1, 'home_too_late': 1}
- claude-sonnet-5-5 low: {'meal_missing': 10, 'too_many_stops': 8, 'wait_too_long': 8, 'visit_overruns': 8, 'meal_outside_window': 3, 'home_too_late': 1, 'schema': 1}
- claude-opus-5-5 medium: {'schema': 23, 'wait_too_long': 8, 'visit_overruns': 4, 'meal_missing': 4, 'meal_outside_window': 2, 'home_too_late': 2, 'too_many_stops': 1}
