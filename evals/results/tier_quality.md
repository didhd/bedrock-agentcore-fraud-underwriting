# Tier quality: does the cheaper model analyse as well?

Judge `global.anthropic.claude-opus-5`, blind (model identifiers stripped, order shuffled under seed 20260728). Region `us-east-1`. Generated 2026-07-28T17:29:48+00:00.

Actual measured spend: specialists $1.3586, judge $3.5501, total $4.9088.

## Per (agent, model)

| agent | model | n | band agreement | contract violations | judge calibration (mean/min) | judge substance | escalates | padding | wall p50 s | $/call p50 | out tok p50 | evidence/1k chars |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bustout | haiku-4-5-20251001-v1:0 | 3 | 2/3 | 3 | 2.67/1 | 3.33 | 1 | 3/3 | 13.654 | 0.0087 | 1287 | 8.63 |
| bustout | opus-5 | 3 | 2/3 | 3 | 3.67/2 | 4.0 | 1 | 3/3 | 34.396 | 0.073 | 2536 | 7.42 |
| bustout | sonnet-5 | 3 | 1/3 | 3 | 2.67/2 | 4.0 | 2 | 1/3 | 25.313 | 0.0254 | 2127 | 9.52 |
| identity | haiku-4-5-20251001-v1:0 | 3 | 3/3 | 7 | 4.0/4 | 3.33 | 0 | 2/3 | 8.042 | 0.0051 | 564 | 4.73 |
| identity | opus-5 | 3 | 3/3 | 4 | 5.0/5 | 4.67 | 0 | 1/3 | 23.111 | 0.0549 | 1480 | 3.88 |
| identity | sonnet-5 | 3 | 2/2 | 2 | 5.0/5 | 4.0 | 0 | 2/3 | 13.65 | 0.0171 | 1034 | 4.83 |
| rings | haiku-4-5-20251001-v1:0 | 5 | 4/5 | 5 | 3.4/1 | 3.0 | 1 | 5/5 | 14.646 | 0.0081 | 1195 | 5.22 |
| rings | opus-5 | 5 | 4/5 | 5 | 4.2/2 | 4.0 | 1 | 4/5 | 32.81 | 0.0696 | 2448 | 5.66 |
| rings | sonnet-5 | 5 | 4/5 | 5 | 4.0/2 | 4.0 | 1 | 1/5 | 22.05 | 0.0241 | 2026 | 6.31 |
| synthetic | haiku-4-5-20251001-v1:0 | 5 | 5/5 | 8 | 4.4/3 | 3.4 | 0 | 4/5 | 9.56 | 0.0059 | 654 | 7.44 |
| synthetic | opus-5 | 5 | 4/5 | 5 | 4.2/1 | 4.8 | 0 | 0/5 | 8.505 | 0.0293 | 451 | 6.04 |
| synthetic | sonnet-5 | 5 | 5/5 | 5 | 4.8/4 | 3.8 | 0 | 4/5 | 13.54 | 0.0194 | 1123 | 3.48 |

## Band disagreements with the pinned expectation

- `bustout` / `haiku-4-5-20251001-v1:0` on APP-1004: declared **HIGH RISK**, pinned **POSSIBLE RISK** (BST-020)
- `bustout` / `opus-5` on APP-1004: declared **HIGH RISK**, pinned **POSSIBLE RISK** (BST-020)
- `bustout` / `sonnet-5` on APP-1004: declared **HIGH RISK**, pinned **POSSIBLE RISK** (BST-020)
- `bustout` / `sonnet-5` on APP-1012: declared **HIGH RISK**, pinned **POSSIBLE RISK** (BST-020)
- `rings` / `haiku-4-5-20251001-v1:0` on APP-1014: declared **POSSIBLE RISK**, pinned **LOW RISK** (RNG-020)
- `rings` / `opus-5` on APP-1014: declared **POSSIBLE RISK**, pinned **LOW RISK** (RNG-020)
- `rings` / `sonnet-5` on APP-1014: declared **POSSIBLE RISK**, pinned **LOW RISK** (RNG-020)
- `synthetic` / `opus-5` on APP-1014: declared **LOW RISK**, pinned **POSSIBLE RISK** (SYN-008)

## The judge grades its own family (declared conflict)

The judge (Opus 5) is also one of the three candidate models, so on the Opus rows it is scoring its own output. Self-preference is therefore possible and is quantified in judge_self_preference rather than assumed absent. Any conclusion that rests on Opus outscoring a cheaper tier by less than the measured self-preference margin is NOT supported.

- On its own 16 rows the judge scored substance +0.78 and calibration +0.31 versus the other 32, while band agreement moved -0.026. Treat any Opus-vs-cheaper margin below the substance margin as unsupported.
- Deterministic cross-check (no judge involved): band agreement margin -0.026, contract violations margin -0.12

## Is the extra length substance or padding? (paired by application)

| scope | longer | shorter | paired apps | chars ratio (median) | distinct-evidence ratio (median) | judge substance delta | pairs where only the longer one escalates |
|---|---|---|---|---|---|---|---|
| ALL AGENTS POOLED | opus-5 | sonnet-5 | 16 | 1.31x | 1.6x | +0.0 | 0 |
| bustout | opus-5 | sonnet-5 | 3 | 1.98x | 1.94x | +0 | 0 |
| identity | opus-5 | sonnet-5 | 3 | 1.31x | 1.08x | +1 | 0 |
| rings | opus-5 | sonnet-5 | 5 | 1.76x | 1.71x | +0 | 0 |
| synthetic | opus-5 | sonnet-5 | 5 | 0.89x | 1.3x | +1 | 0 |
| ALL AGENTS POOLED | opus-5 | haiku-4-5 | 16 | 1.0x | 1.17x | +1.0 | 0 |
| bustout | opus-5 | haiku-4-5 | 3 | 1.7x | 1.5x | +1 | 0 |
| identity | opus-5 | haiku-4-5 | 3 | 1.32x | 0.58x | +1 | 0 |
| rings | opus-5 | haiku-4-5 | 5 | 1.11x | 1.32x | +1 | 0 |
| synthetic | opus-5 | haiku-4-5 | 5 | 0.74x | 0.53x | +1 | 0 |
| ALL AGENTS POOLED | haiku-4-5 | sonnet-5 | 16 | 1.33x | 1.32x | -1.0 | 0 |
| bustout | haiku-4-5 | sonnet-5 | 3 | 1.3x | 1.18x | -1 | 0 |
| identity | haiku-4-5 | sonnet-5 | 3 | 0.88x | 1.0x | -1 | 0 |
| rings | haiku-4-5 | sonnet-5 | 5 | 1.5x | 1.29x | -1 | 0 |
| synthetic | haiku-4-5 | sonnet-5 | 5 | 1.36x | 2.6x | -1 | 0 |

A chars ratio above the distinct-evidence ratio means the extra length is not carrying proportionally more evidence.

## Is the cheaper model defensible, per agent?

### bustout (currently `sonnet-5`)

- **Verdict: n TOO SMALL TO TELL - 3 applications per cell. One case moves the band rate by 33 points; haiku-4-5-20251001-v1:0 did not lose on any axis here**
- incumbent `sonnet-5`: band 1/3, calibration mean 2.67, judge-confirmed false positives 2, $0.0254/call, 25.313s
- `haiku-4-5-20251001-v1:0`: band 2/3, calibration mean 2.67 (gap +0.00 vs incumbent), false positives 1, contract violations 3, $0.0087/call, 13.654s
- Sample: 3 application(s) per cell; a band claim needs 5

### identity (currently `sonnet-5`)

- **Verdict: n TOO SMALL TO TELL - 3 applications per cell. One case moves the band rate by 33 points; haiku-4-5-20251001-v1:0 did not lose on any axis here**
- incumbent `sonnet-5`: band 2/2, calibration mean 5.0, judge-confirmed false positives 0, $0.0171/call, 13.65s
- `haiku-4-5-20251001-v1:0`: band 3/3, calibration mean 4.0 (gap +1.00 vs incumbent), false positives 0, contract violations 7, $0.0051/call, 8.042s
- Sample: 3 application(s) per cell; a band claim needs 5

### rings (currently `opus-5`)

- **Verdict: DEFENSIBLE on this sample: haiku-4-5-20251001-v1:0, sonnet-5**
- incumbent `opus-5`: band 4/5, calibration mean 4.2, judge-confirmed false positives 1, $0.0696/call, 32.81s
- `haiku-4-5-20251001-v1:0`: band 4/5, calibration mean 3.4 (gap +0.80 vs incumbent), false positives 1, contract violations 5, $0.0081/call, 14.646s
- `sonnet-5`: band 4/5, calibration mean 4.0 (gap +0.20 vs incumbent), false positives 1, contract violations 5, $0.0241/call, 22.05s - NOTE: that +0.20 lead is smaller than the judge's measured +0.31 self-preference for its own family, so it is not distinguishable from bias
- Sample: 5 application(s) per cell; a band claim needs 5

### synthetic (currently `opus-5`)

- **Verdict: DEFENSIBLE on this sample: haiku-4-5-20251001-v1:0, sonnet-5**
- incumbent `opus-5`: band 4/5, calibration mean 4.2, judge-confirmed false positives 0, $0.0293/call, 8.505s
- `haiku-4-5-20251001-v1:0`: band 5/5, calibration mean 4.4 (gap -0.20 vs incumbent), false positives 0, contract violations 8, $0.0059/call, 9.56s
- `sonnet-5`: band 5/5, calibration mean 4.8 (gap -0.60 vs incumbent), false positives 0, contract violations 5, $0.0194/call, 13.54s
- Sample: 5 application(s) per cell; a band claim needs 5

## Which output rule applies to which agent (read from the prompts)

| rule | agents | n |
|---|---|---|
| `no_alert_numbers` | identity, dealer, straw, employment, income, synthetic | 6 |
| `no_emoji` | identity, dealer, straw, employment, income, synthetic | 6 |
| `no_risk_label_in_headers` | identity, dealer, straw, employment | 4 |
| `exact_title` | identity, dealer, straw, employment, income, synthetic | 6 |
| `max_4000_chars` | identity, dealer, straw, employment, income, synthetic | 6 |
| `low_risk_3_to_5_sentences` | identity, dealer, straw, employment, income, synthetic | 6 |
| `no_coborrower_2_to_3_sentences` | straw | 1 |

## Caveats

- Percentiles come from evals.bench.summarize_values: p50 needs 5 samples, p95 needs 20. Cells below those counts report the refusal, not a number.
- The band is read twice - by regex and by the blind judge. Rows where the two readers disagree are listed per cell and are not counted as agreement.
- Output-contract rules are enforced per domain only where the domain's own verbatim prompt states them; bustout and rings state none of them.
- Cost and latency are only ever the measured values from the call that produced the judged text. No cost is modelled at another model's tokens.
- The judge (Opus 5) is also one of the three candidate models, so on the Opus rows it is scoring its own output. Self-preference is therefore possible and is quantified in judge_self_preference rather than assumed absent. Any conclusion that rests on Opus outscoring a cheaper tier by less than the measured self-preference margin is NOT supported.
