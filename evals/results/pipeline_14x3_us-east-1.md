### Measured pipeline benchmark — 14 applications x 3 repetitions, n=42 adjudications

`pipeline-live`, region `us-east-1` (synthesizer `us-east-1`), git `9bb569bac801`, 2026-07-28T07:42:42+00:00.

| stage | n | min | p50 | p95 | max |
|---|---:|---:|---:|---:|---:|
| end to end | 42 | 18.93s | 31.45s | 46.55s | 74.97s |
| fan-out (8 specialists) | 42 | 15.37s | 24.93s | 41.05s | 70.62s |
| synthesis | 42 | 3.08s | 4.20s | 5.59s | 15.21s |
| sum-of-eight (sequential counterfactual) | 42 | 65.52s | 85.62s | 157.14s | 173.00s |

**Under the 60s target:** 40 of 42 runs; 12 of 14 applications on every repetition, 14 on at least one.

**Parallel speed-up** (fan-out vs sum-of-eight, n=42): min 1.85x, p50 3.64x, p95 4.99x, max 5.11x. One ratio per run — the fan-out is bounded by its slowest dimension, so the spread moves with which specialist ran long.

**Cost per adjudication** (n=41 fully priced runs, 1 excluded): min $0.1177, p50 $0.1440, max $0.2536.

| specialist | model | n ok | n failed | p50 | p95 | max | tail in N runs |
|---|---|---:|---:|---:|---:|---:|---:|
| `identity` | `global.anthropic.claude-sonnet-5` | 42 | 0 | 12.56s | 33.40s | 41.04s | 4 |
| `dealer` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | 42 | 0 | 6.56s | 14.56s | 18.85s | 0 |
| `straw` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | 42 | 0 | 4.40s | 8.76s | 12.52s | 0 |
| `employment` | `global.anthropic.claude-sonnet-5` | 42 | 0 | 7.48s | 17.64s | 20.00s | 0 |
| `income` | `global.anthropic.claude-sonnet-5` | 42 | 0 | 8.09s | 31.44s | 66.01s | 5 |
| `synthetic` | `global.anthropic.claude-opus-5` | 41 | 1 | 8.52s | 20.82s | 26.70s | 1 |
| `bustout` | `global.anthropic.claude-sonnet-5` | 42 | 0 | 14.46s | 35.71s | 69.78s | 7 |
| `rings` | `global.anthropic.claude-opus-5` | 41 | 1 | 20.96s | 32.25s | 36.17s | 25 |

**Verdict agreement:** 12 of 14 applications returned the same `overall_risk_decision` on every repetition.

- `APP-1005` was unstable: HIGH RISK / MEDIUM RISK across 3 runs (fixture pins MEDIUM RISK).
- `APP-1012` was unstable: HIGH RISK / MEDIUM RISK across 3 runs (fixture pins HIGH RISK).

- `APP-1003` matched its pinned MEDIUM RISK in 0 of 3 runs (observed LOW RISK).
- `APP-1005` matched its pinned MEDIUM RISK in 2 of 3 runs (observed HIGH RISK / MEDIUM RISK).
- `APP-1012` matched its pinned HIGH RISK in 1 of 3 runs (observed HIGH RISK / MEDIUM RISK).

This is reported, not adjudicated. Whether the pin or the model is wrong is the customer's call; run the calibration judge in `evals/evaluators/` before drawing an accuracy conclusion.

**Failures:** 1 degraded run(s) — server_5xx x2. Degraded runs stay in the latency distribution and are excluded from the cost distribution.

Not measured: AgentCore runtime overhead (these calls leave this host directly), signal generation (precomputed, outside the timed window), and throughput (one application in flight at a time).
