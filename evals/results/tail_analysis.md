# Where the 75-second run comes from

Source: `evals/results/pipeline_14x3_us-east-1.json` -- 42 live adjudications, 336 specialist calls, us-east-1, 2026-07-28T07:42:42+00:00.

End-to-end over n=42: p50 31.45s, p95 46.55s, min 18.93s, max 74.97s.

## 1. The tail is not the agent that sets the median

Critical path (slowest agent) across all 42 runs: rings 25, bustout 7, income 5, identity 4, synthetic 1.

In the 10 SLOWEST runs: bustout 3, identity 3, income 2, rings 2. In the 10 FASTEST runs: rings 7, bustout 3.

| application | rep | e2e | fan-out | synthesis | critical path | its share of fan-out |
|---|---|---|---|---|---|---|
| APP-1007 | 2 | 75.0s | 70.6s | 4.3s | bustout 69.8s | 98.8% |
| APP-1001 | 2 | 70.2s | 66.5s | 3.7s | income 66.0s | 99.3% |
| APP-1005 | 1 | 46.5s | 41.0s | 5.5s | identity 41.0s | 100.0% |
| APP-1005 | 3 | 43.9s | 39.7s | 4.2s | identity 39.7s | 100.0% |
| APP-1012 | 1 | 42.7s | 37.5s | 5.3s | bustout 36.7s | 98.0% |
| APP-1005 | 2 | 42.0s | 36.4s | 5.6s | bustout 35.7s | 98.1% |

Variance split over n=42: 39.0% between applications, 61.0% WITHIN an application across repetitions of byte-identical input. Most of the spread is not the workload.

| application | reps (s) | spread |
|---|---|---|
| APP-1007 | 23.5, 34.6, 75.0 | 51.5s (3.19x) |
| APP-1001 | 18.9, 24.2, 70.2 | 51.3s (3.71x) |
| APP-1014 | 24.8, 31.6, 41.9 | 17.1s (1.69x) |
| APP-1006 | 22.3, 23.8, 33.7 | 11.3s (1.51x) |
| APP-1012 | 36.6, 38.2, 42.7 | 6.1s (1.17x) |
| APP-1008 | 25.5, 28.5, 31.4 | 5.9s (1.23x) |

Tightest three repetitions (demo candidates):

- **APP-1010**: 28.0s, 28.4s, 28.6s -- spread 0.6s
- **APP-1002**: 22.3s, 22.9s, 23.2s -- spread 0.9s
- **APP-1011**: 21.2s, 22.4s, 22.8s -- spread 1.6s

## 2. Latency IS linear in output tokens -- except for two calls that are not

Pooled fit, n=334: r=0.8514, r2=0.7249. Excluding the 2 stalls, n=332: r=0.9763, r2=0.9531 -- `wall_ms = 2980 + 11.34 x output_tokens`, i.e. 88.2 output tok/s marginal on a 2.98s fixed overhead.

So 2 of 336 calls (0.6%) are a different mechanism:

| application | rep | agent | wall | model latency | unaccounted | out tok | tok/s | stop |
|---|---|---|---|---|---|---|---|---|
| APP-1007 | 2 | bustout | 69.8s | 69.45s | 0.33s | 1120 | 16.1 | end_turn |
| APP-1001 | 2 | income | 66.0s | 65.01s | 1.0s | 306 | 4.7 | end_turn |

Every other call in the sample generates at 66 output tok/s median (n=332, min 21, max 96); that per-call median is lower than the 88.2 tok/s MARGINAL rate above because every call also pays the fixed overhead in the fit's intercept. The 2 stalls ran at 16.1 and 4.7 tok/s with near-zero unaccounted wall clock and `stop_reason=end_turn`. They were slow **inside the model**, on a normal amount of output.

### The verbosity half is real and it is structural

| | domains | n calls | out tok p50 | out tok max | analysis chars p50 | chars > 4000 |
|---|---|---|---|---|---|---|
| prompt caps at 4000 chars | identity, dealer, straw, employment, income, synthetic | 251 | 381 | 3582 | 1224 | 9 (3.6%) |
| NO cap in prompt | bustout, rings | 83 | 1331 | 3257 | 3519 | 25 (30.1%) |

The two prompts with no character cap (bustout, rings) write materially more than the six capped at 'max 4000 characters', and latency is linear in what is written, so they are the structural owner of the fan-out floor.

Per-domain fit (all calls / stalls excluded):

| agent | prompt cap | n | r2 all | r2 excl. stalls | ms per output token | fixed overhead |
|---|---|---|---|---|---|---|
| identity | 4000 | 42 | 0.9834 | 0.9834 | 10.3427 | 4.2s |
| dealer | 4000 | 42 | 0.9107 | 0.9107 | 10.3704 | 3.7s |
| straw | 4000 | 42 | 0.8439 | 0.8439 | 10.0784 | 2.48s |
| employment | 4000 | 42 | 0.9669 | 0.9669 | 10.7065 | 3.17s |
| income | 4000 | 42 | 0.3855 | 0.9619 | 10.6294 | 3.18s |
| synthetic | 4000 | 41 | 0.8931 | 0.8931 | 12.9012 | 2.48s |
| bustout | NONE | 42 | 0.3077 | 0.981 | 10.8263 | 2.16s |
| rings | NONE | 41 | 0.8437 | 0.8437 | 13.802 | 1.1s |

### A character cap and a token cap are not the same constraint

| agent | chars per output token (median) | min |
|---|---|---|
| identity | 1.55 | 0.33 |
| dealer | 4.51 | 3.95 |
| straw | 4.22 | 3.73 |
| employment | 3.03 | 1.81 |
| income | 2.72 | 0.85 |
| synthetic | 3.1 | 1.18 |
| bustout | 2.67 | 1.06 |
| rings | 2.81 | 1.97 |

A prompt's 'max 4000 characters' rule and a max_tokens cap are not the same constraint. Where this ratio collapses, the model is billed for output the analysis text does not contain, so a cap chosen from the character contract truncates earlier than the contract implies.

## 3. It is not retries and not throttling

Retry ladder in force: [1, 2, 4]s over 4 attempts (7s worst case). Wall clock minus the model's own `latencyMs`, n=334: p50 0.73s, p95 1.69s, max 7.66s.

That strategy retries on `ModelThrottledException` and nothing else, and **0** were recorded across 336 calls. Synthesis repair attempts: 0.

Unaccounted wall clock on the two stalled calls: 1.00s, 0.33s. Attributing every millisecond of it to backoff explains at most **1.8%** of their excess over the median call.

Verdict: **RULED OUT as the cause of the tail. No ThrottlingException was recorded on any of 336 calls, and ModelThrottledException is the only exception this strategy retries. The two stalled calls carry 1.00s, 0.33s of unaccounted wall clock; even attributing ALL of it to backoff explains at most 1.8% of their excess over the median call. The tail is inside the model's own reported latency, not in the client.**

The stalled calls were slow INSIDE the model's own reported latency, not waiting in the client, so neither retries nor connection-pool queueing produced the tail. A server_5xx is the opposite shape -- it FAILS FAST, so the degraded run is one of the quickest in the sample and it costs a fraud dimension rather than time.

The one degraded run, APP-1011 rep 3: synthetic: server_5xx, rings: server_5xx, and it finished in **21.2s** -- a 5xx fails fast, so it costs a fraud dimension, not time. It still validated 21 keys: True.

## 4. The floor: fan-out cannot beat its slowest agent

| layer | n | p50 | p95 | max |
|---|---|---|---|---|
| fan-out floor (slowest agent alone) | 42 | 24.03s | 41.04s | 69.78s |
| orchestration above the floor | 42 | 0.83s | 1.87s | 2.71s |
| synthesis + validation | 42 | 4.20s | 5.59s | 15.21s |

The floor is 96.3% of fan-out (median) and fan-out is 85.6% of end-to-end. Orchestration costs 0.83s at p50 and never more than 2.71s -- there is nothing to win there.

Ceiling on any single-agent fix: slowest minus second-slowest is p50 6.34s, p95 20.56s (n=42). Even making the critical-path agent instantaneous only recovers that much before the next agent becomes the floor.

## What a lowered `max_tokens` could bind on

Shipped caps: {'light': 3072, 'mid': 4096, 'heavy': 4096, 'custom': 4096}.

| max_tokens | calls already over it | % | domains |
|---|---|---|---|
| 1024 | 105/334 | 31.4% | rings:41, bustout:24, identity:14, income:13, employment:5, synthetic:5, dealer:3 |
| 1536 | 45/334 | 13.5% | rings:18, income:9, bustout:8, identity:7, employment:2, synthetic:1 |
| 2048 | 21/334 | 6.3% | identity:7, income:5, bustout:5, rings:4 |
| 3072 | 3/334 | 0.9% | identity:2, bustout:1 |
| 4096 | 0/334 | 0.0% | - |

MEASURED: which observed calls already wrote more than the cap, i.e. which would have raised MaxTokensReachedException. NOT MEASURED here: the latency or cost of running with the cap, because a cap changes what the model writes. That is what --live measures.

## Live cap sweep: does constraining output shorten the tail? **No.**

30 live calls in us-east-1, $0.6278 measured on the 16 that returned an analysis, plus a bounded ~$0.4378 on the 14 that truncated. Control cap 4096.

cap=4096 is the shipped MAX_TOKENS_BY_TIER value for these tiers and is re-run in the same session as the constrained cells, so a cap delta is measured against a same-session control rather than against a run from another day.

### Every cell, measured

| application | agent | max_tokens | wall | out tok | chars | band | outcome |
|---|---|---|---|---|---|---|---|
| APP-1004 | bustout | 1024 | 13.06s | - | - | - | **TRUNCATED** |
| APP-1004 | bustout | 1536 | 17.70s | - | - | - | **TRUNCATED** |
| APP-1004 | bustout | 2048 | 22.90s | - | - | - | **TRUNCATED** |
| APP-1004 | bustout | 3072 | 24.69s | 2151 | 3561 | HIGH RISK | ok |
| APP-1004 | bustout | 4096 | 22.69s | 1916 | 3420 | HIGH RISK | ok |
| APP-1004 | rings | 1024 | 15.48s | - | - | - | **TRUNCATED** |
| APP-1004 | rings | 1536 | 20.75s | - | - | - | **TRUNCATED** |
| APP-1004 | rings | 2048 | 28.50s | 1980 | 5636 | HIGH RISK | ok |
| APP-1004 | rings | 3072 | 30.45s | 2248 | 6228 | HIGH RISK | ok |
| APP-1004 | rings | 4096 | 38.90s | 2876 | 7785 | HIGH RISK | ok |
| APP-1012 | bustout | 1024 | 12.76s | - | - | - | **TRUNCATED** |
| APP-1012 | bustout | 1536 | 17.41s | - | - | - | **TRUNCATED** |
| APP-1012 | bustout | 2048 | 22.73s | - | - | - | **TRUNCATED** |
| APP-1012 | bustout | 3072 | 33.70s | 3034 | 3960 | POSSIBLE RISK | ok |
| APP-1012 | bustout | 4096 | 15.87s | 1204 | 3445 | HIGH RISK | ok |
| APP-1012 | rings | 1024 | 14.88s | - | - | - | **TRUNCATED** |
| APP-1012 | rings | 1536 | 23.14s | 1504 | 4316 | HIGH RISK | ok |
| APP-1012 | rings | 2048 | 27.46s | - | - | - | **TRUNCATED** |
| APP-1012 | rings | 3072 | 28.22s | 2043 | 5766 | POSSIBLE RISK | ok |
| APP-1012 | rings | 4096 | 21.73s | 1553 | 4343 | POSSIBLE RISK | ok |
| APP-1013 | bustout | 1024 | 12.27s | - | - | - | **TRUNCATED** |
| APP-1013 | bustout | 1536 | 14.53s | 1097 | 3022 | POSSIBLE RISK | ok |
| APP-1013 | bustout | 2048 | 16.15s | 1247 | 3617 | POSSIBLE RISK | ok |
| APP-1013 | bustout | 3072 | 21.34s | 1838 | 3268 | POSSIBLE RISK | ok |
| APP-1013 | bustout | 4096 | 22.87s | 2026 | 3422 | POSSIBLE RISK | ok |
| APP-1013 | rings | 1024 | 13.47s | - | - | - | **TRUNCATED** |
| APP-1013 | rings | 1536 | 23.29s | - | - | - | **TRUNCATED** |
| APP-1013 | rings | 2048 | 29.04s | - | - | - | **TRUNCATED** |
| APP-1013 | rings | 3072 | 25.51s | 1753 | 5059 | POSSIBLE RISK | ok |
| APP-1013 | rings | 4096 | 20.13s | 1400 | 3799 | HIGH RISK | ok |

### 1. The cap did not shorten the calls that survived it

Of 10 cells that returned an analysis at BOTH their cap and the control, only **5** were faster than the control and 5 were slower. Delta against control (n=10): min -10.39s, mean -0.23s, max +17.84s.

14 further cells finished sooner and are EXCLUDED: Only cells that RETURNED AN ANALYSIS at both caps can support a latency claim. A cell that truncated finished early because it stopped, not because it was faster, and counting it would turn a lost fraud dimension into a latency win.

The mechanism is visible in the table: at a given cap the model writes what it wants to write and the cap either does not bind or kills the call. It never produces a shorter analysis.

### 2. A truncated call costs full time and full money for zero analysis

14 of 30 cells truncated, burning **263.2s** of wall clock (mean 18.8s each, max 29.0s) and a bounded ~$0.4378, and returning nothing.

A truncated call is the worst of both: it spends the wall clock, it is billed, and it returns no analysis, so the fraud dimension is lost. A cap low enough to shorten these agents is a cap low enough to delete them.

### 3. The failure boundary is not even monotonic

- `bustout`: caps tested [1024, 1536, 2048, 3072, 4096]; truncated at **[1024, 1536, 2048]**; completed at least once at [1536, 2048, 3072, 4096]; lowest cap where EVERY cell completed: **3072**
- `rings`: caps tested [1024, 1536, 2048, 3072, 4096]; truncated at **[1024, 1536, 2048]**; completed at least once at [1536, 2048, 3072, 4096]; lowest cap where EVERY cell completed: **3072**

Note the overlap: both agents both truncated AND completed at 1536 and 2048 depending on the application and the draw. There is no cap below the shipped 4096 that is safe for these two agents on these three applications, and 3072 is the lowest that completed every cell -- one draw each, which is not enough to call it safe.

### 4. Quality gate

**Band agreement: 13/30** cells agree with the same-session control. 17 disagree, of which 14 lost the band entirely because the call truncated.

The disagreements that were not truncations still moved a fraud band at one draw each:

- APP-1012 bustout at cap 3072: control `HIGH RISK` -> `POSSIBLE RISK`
- APP-1012 rings at cap 1536: control `POSSIBLE RISK` -> `HIGH RISK`
- APP-1013 rings at cap 3072: control `HIGH RISK` -> `POSSIBLE RISK`

Compared against the same-session control cap, which is the only comparison that isolates the cap. Specialist bands are not deterministic, so a single disagreement is evidence of instability at that cap, not proof of causation; the cell count is stated so nobody reads it as more.

NOT ESTABLISHED: whether those band moves are caused by the cap or are the same run-to-run instability the 42-run benchmark already records on APP-1005 and APP-1012. One draw per cell cannot separate the two, and this sweep does not claim to.

A cap is only a fix if it (a) lowers wall clock, (b) truncates nothing, and (c) leaves every specialist's risk band unchanged. Failing (b) or (c) makes a latency win a lost fraud dimension.

## What actually removes the tail

The tail has two mechanisms, and only one of them is ours.

**1. Verbosity sets the FLOOR (fixable, but not by a cap).** Wall clock is linear in output tokens at r2=0.9531 over n=332 calls, so the floor is bought one token at a time. The two agents with no character cap in their prompt write 1331 output tokens at p50 against 381 for the six that have one, and rings alone was the critical path in 25 of 42 runs. But the live sweep above shows `max_tokens` is the WRONG instrument: it does not shorten the analysis, it deletes it. The instrument that matches the mechanism is the customer's own prompt text -- add the 'max 4000 characters' sentence bustout and rings are missing, which is a one-line change to two verbatim prompts and is a customer decision, not ours. NOT MEASURED: whether adding that sentence shortens the output, because this task did not modify the customer's verbatim prompts.

**2. Server-side generation speed sets the TAIL (not fixable at all).** The two runs over 60s were caused by 2 of 336 calls (0.6%) generating at 16.1 and 4.7 output tok/s against a 66 tok/s median, inside the model's own reported latency, with no throttle, no retry and `stop_reason=end_turn`. 61.0% of all end-to-end variance is within-application, i.e. across repetitions of byte-identical input. **This is irreducible run-to-run Bedrock variance and no client-side change removes it.**

### Therefore, for Friday

The honest mitigation is not a code change, it is run selection plus a floor reduction:

1. **Demo on an application with a measured-stable profile.** Three repetitions of identical input, so this is the only defensible basis for predicting one live run: **APP-1010** 28.0s, 28.4s, 28.6s (spread 0.6s); **APP-1002** 22.3s, 22.9s, 23.2s (spread 0.9s); **APP-1011** 21.2s, 22.4s, 22.8s (spread 1.6s). Against APP-1007 (23.5s, 34.6s, 75.0s) and APP-1001, whose spreads exceed 51s. Caveat stated plainly: n=3 per application makes this a low-variance OBSERVATION, not a guarantee -- the stall rate is 0.6% per CALL and a run makes eight of them.

2. **Move rings off Opus 5.** The separately measured tier sweep (n=3 applications, one draw each, `/tmp/bench/tiering.json`) has rings at 16.22s p50 on Sonnet 5 against 32.81s on Opus 5, emitting 1314 against 2454 output tokens -- and all three applications reached the SAME band on all three models, so the latency is bought without a band change in that sample. rings owns the critical path in 25 of 42 runs, and the floor is 96.3% of fan-out, so this is the one change that moves the median. Ceiling on it, measured: slowest minus second-slowest is p50 6.34s, so even a perfect rings fix leaves the next agent as the floor.

3. **Do not lower `max_tokens`.** Measured above: 14 of 30 cells truncated, non-monotonically, and a truncated call pays full time and full money for no analysis.

### What this analysis did NOT establish

- **Why** a Bedrock call generates at 4.7 tok/s. Only that it does, that it did so on 0.6% of 336 calls, and that it is not throttling, not retries and not output length.
- Whether the stall rate is stable. Two events over 336 calls cannot support a rate; the 0.6% figure is a count divided by a denominator, not a predicted frequency.
- Whether adding the missing character cap to the bustout and rings PROMPTS shortens their output. That is the recommended fix and it is unmeasured; it needs a live run against modified prompt text, which is the customer's artifact.
- Whether the band moves observed in the sweep were caused by the cap. One draw per cell, and the source benchmark already records band instability on two applications with no cap change at all.
- Any p99. n=42 runs and n=42 fan-outs; the p99 stays suppressed, which matters here precisely because the tail is the question -- a 0.6%-per-call event is exactly what a sample this size cannot pin down.
- AgentCore runtime overhead, signal generation and concurrent throughput, all of which the source benchmark also excludes.

