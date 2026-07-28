# Is the GPT set as good at the fraud analysis?

Blind, two judges: `global.anthropic.claude-opus-5`, `openai.gpt-5.5`. 4 agents x 6 applications x 5 models. Region `us-east-1` (mantle `us-east-1`). Generated 2026-07-28T20:25:39+00:00.

Measured spend: specialists $2.7526, judges $2.1401, total $4.8927.

## The verdict, per (agent, GPT model)

### bustout (incumbent `sonnet-5`)

- incumbent `sonnet-5`: band 3/6, anti-FP false positives 0/2, false negatives 0/2, grounded 0.571, contract violations 6, 15.8995s, $0.0173/call, 1237.5 out tok
- **`luna`: AS GOOD ON THIS SAMPLE** — band 4/6, anti-FP false positives 0/2, false negatives 0/2, grounded 0.286, contract violations 0, 3.2095s, $0.0053/call, 421.0 out tok
- **`sol`: NOT AS GOOD: grounded fraction 0.2145 is less than half the incumbent's 0.571** — band 4/6, anti-FP false positives 0/2, false negatives 0/2, grounded 0.2145, contract violations 0, 6.0615s, $0.0231/call, 328.5 out tok
- **`terra`: AS GOOD ON THIS SAMPLE** — band 4/6, anti-FP false positives 0/2, false negatives 0/2, grounded 0.3575, contract violations 0, 7.9015s, $0.0103/call, 251.0 out tok
- Sample: 6 application(s) per cell; a band claim needs 5. Anti-false-positive fixtures in this sample: 2.

### identity (incumbent `sonnet-5`)

- incumbent `sonnet-5`: band 3/3, anti-FP false positives 0/1, false negatives 0/2, grounded 0.429, contract violations 0, 18.746s, $0.0201/call, 1375 out tok
- **`luna`: n TOO SMALL - 3 usable application(s); a band claim needs 5** — band 3/3, anti-FP false positives 0/0, false negatives 0/3, grounded 0.429, contract violations 0, 3.803s, $0.0052/call, 355 out tok
- **`sol`: NOT AS GOOD: 2 false positive(s) on the anti-FP fixtures against the incumbent's 0 (APP-1009, APP-1010); 1 dismissal(s) of a corroborated application against the incumbent's 0 (APP-1014); band agreement 2/6 below the incumbent's 3/3; grounded fraction 0.143 is less than half the incumbent's 0.429** — band 2/6, anti-FP false positives 2/2, false negatives 1/3, grounded 0.143, contract violations 0, 5.5045s, $0.0269/call, 369.5 out tok
- **`terra`: n TOO SMALL - 3 usable application(s); a band claim needs 5** — band 2/3, anti-FP false positives 0/0, false negatives 0/2, grounded 0.571, contract violations 0, 9.429s, $0.0127/call, 399 out tok
- Sample: 3 application(s) per cell; a band claim needs 5. Anti-false-positive fixtures in this sample: 1.

### rings (incumbent `opus-5`)

- incumbent `opus-5`: band 5/6, anti-FP false positives 0/2, false negatives 0/3, grounded 0.875, contract violations 6, 26.2175s, $0.0583/call, 1813.0 out tok
- **`luna`: AS GOOD ON THIS SAMPLE** — band 5/6, anti-FP false positives 0/2, false negatives 0/3, grounded 0.5, contract violations 0, 3.201s, $0.005/call, 391.0 out tok
- **`sol`: NOT AS GOOD: band agreement 4/6 below the incumbent's 5/6** — band 4/6, anti-FP false positives 0/2, false negatives 0/3, grounded 0.5, contract violations 0, 4.855s, $0.0206/call, 261.5 out tok
- **`terra`: AS GOOD ON THIS SAMPLE** — band 5/6, anti-FP false positives 0/2, false negatives 0/3, grounded 0.625, contract violations 0, 8.0395s, $0.0105/call, 277.0 out tok
- Sample: 6 application(s) per cell; a band claim needs 5. Anti-false-positive fixtures in this sample: 2.

### synthetic (incumbent `opus-5`)

- incumbent `opus-5`: band 5/6, anti-FP false positives 0/2, false negatives 1/2, grounded 0.55, contract violations 2, 8.5875s, $0.0318/call, 507.0 out tok
- **`luna`: NOT AS GOOD: grounded fraction 0.15 is less than half the incumbent's 0.55** — band 5/6, anti-FP false positives 0/2, false negatives 1/2, grounded 0.15, contract violations 0, 2.2775s, $0.0047/call, 206.0 out tok
- **`sol`: NOT AS GOOD: grounded fraction 0.1 is less than half the incumbent's 0.55** — band 5/6, anti-FP false positives 0/2, false negatives 1/2, grounded 0.1, contract violations 0, 2.8185s, $0.021/call, 126.0 out tok
- **`terra`: AS GOOD ON THIS SAMPLE** — band 5/6, anti-FP false positives 0/2, false negatives 1/2, grounded 0.35, contract violations 0, 7.046s, $0.0106/call, 158.0 out tok
- Sample: 6 application(s) per cell; a band claim needs 5. Anti-false-positive fixtures in this sample: 2.

## Per (agent, model): deterministic scores

| agent | set | model | n | band | escalations | dismissals | anti-FP false pos | contract viol | grounded frac p50 | signals cited p50 | wall p50 s | $/call p50 | out tok p50 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bustout | gpt | luna | 6 | 4/6 | 2 | 0 | 0/2 | 0 | 0.286 | 1.0 | 3.2095 | 0.0053 | 421.0 |
| bustout | claude | opus-5 | 6 | 4/6 | 2 | 0 | 0/2 | 6 | 0.7855 | 7.0 | 31.4235 | 0.0685 | 2357.5 |
| bustout | gpt | sol | 6 | 4/6 | 2 | 0 | 0/2 | 0 | 0.2145 | 1.0 | 6.0615 | 0.0231 | 328.5 |
| bustout | claude | sonnet-5 | 6 | 3/6 | 3 | 0 | 0/2 | 6 | 0.571 | 4.5 | 15.8995 | 0.0173 | 1237.5 |
| bustout | gpt | terra | 6 | 4/6 | 2 | 0 | 0/2 | 0 | 0.3575 | 1.5 | 7.9015 | 0.0103 | 251.0 |
| identity | gpt | luna | 3 | 3/3 | 0 | 0 | 0/0 | 0 | 0.429 | 4 | 3.803 | 0.0052 | 355 |
| identity | claude | opus-5 | 6 | 5/6 | 1 | 0 | 0/2 | 4 | 0.7855 | 4.5 | 24.8615 | 0.0578 | 1723.5 |
| identity | gpt | sol | 6 | 2/6 | 3 | 1 | 2/2 | 0 | 0.143 | 3.0 | 5.5045 | 0.0269 | 369.5 |
| identity | claude | sonnet-5 | 3 | 3/3 | 0 | 0 | 0/1 | 0 | 0.429 | 5 | 18.746 | 0.0201 | 1375 |
| identity | gpt | terra | 3 | 2/3 | 1 | 0 | 0/0 | 0 | 0.571 | 4 | 9.429 | 0.0127 | 399 |
| rings | gpt | luna | 6 | 5/6 | 1 | 0 | 0/2 | 0 | 0.5 | 4.0 | 3.201 | 0.005 | 391.0 |
| rings | claude | opus-5 | 6 | 5/6 | 1 | 0 | 0/2 | 6 | 0.875 | 4.0 | 26.2175 | 0.0583 | 1813.0 |
| rings | gpt | sol | 6 | 4/6 | 2 | 0 | 0/2 | 0 | 0.5 | 4.0 | 4.855 | 0.0206 | 261.5 |
| rings | claude | sonnet-5 | 6 | 5/6 | 1 | 0 | 0/2 | 7 | 0.75 | 4.0 | 17.95 | 0.0191 | 1422.5 |
| rings | gpt | terra | 6 | 5/6 | 1 | 0 | 0/2 | 0 | 0.625 | 4.0 | 8.0395 | 0.0105 | 277.0 |
| synthetic | gpt | luna | 6 | 5/6 | 0 | 1 | 0/2 | 0 | 0.15 | 4.5 | 2.2775 | 0.0047 | 206.0 |
| synthetic | claude | opus-5 | 6 | 5/6 | 0 | 1 | 0/2 | 2 | 0.55 | 3.5 | 8.5875 | 0.0318 | 507.0 |
| synthetic | gpt | sol | 6 | 5/6 | 0 | 1 | 0/2 | 0 | 0.1 | 2.5 | 2.8185 | 0.021 | 126.0 |
| synthetic | claude | sonnet-5 | 6 | 6/6 | 0 | 0 | 0/2 | 3 | 0.4 | 5.5 | 16.235 | 0.0204 | 1269.5 |
| synthetic | gpt | terra | 6 | 5/6 | 0 | 1 | 0/2 | 0 | 0.35 | 3.0 | 7.046 | 0.0106 | 158.0 |

## Per (agent, model): what each judge thought

| agent | set | model | anthropic.claude-opus-5 (claude) cal/sub | gpt-5.5 (gpt) cal/sub |
|---|---|---|---|---|
| bustout | gpt | luna | —/— (n=0) | —/— (n=0) |
| bustout | claude | opus-5 | 3.67/4.0 (n=3) | —/— (n=0) |
| bustout | gpt | sol | —/— (n=0) | —/— (n=0) |
| bustout | claude | sonnet-5 | 2.67/4.0 (n=3) | —/— (n=0) |
| bustout | gpt | terra | —/— (n=0) | —/— (n=0) |
| identity | gpt | luna | —/— (n=0) | —/— (n=0) |
| identity | claude | opus-5 | 5.0/4.67 (n=3) | —/— (n=0) |
| identity | gpt | sol | —/— (n=0) | —/— (n=0) |
| identity | claude | sonnet-5 | 5.0/4.0 (n=2) | —/— (n=0) |
| identity | gpt | terra | —/— (n=0) | —/— (n=0) |
| rings | gpt | luna | —/— (n=0) | —/— (n=0) |
| rings | claude | opus-5 | 4.2/4.0 (n=5) | —/— (n=0) |
| rings | gpt | sol | —/— (n=0) | —/— (n=0) |
| rings | claude | sonnet-5 | 4.0/4.0 (n=5) | —/— (n=0) |
| rings | gpt | terra | —/— (n=0) | —/— (n=0) |
| synthetic | gpt | luna | —/— (n=0) | —/— (n=0) |
| synthetic | claude | opus-5 | 4.2/4.8 (n=5) | —/— (n=0) |
| synthetic | gpt | sol | —/— (n=0) | —/— (n=0) |
| synthetic | claude | sonnet-5 | 4.8/3.8 (n=5) | —/— (n=0) |
| synthetic | gpt | terra | —/— (n=0) | —/— (n=0) |

## Do the two oppositely-biased judges agree?

Agreement between two judges biased in OPPOSITE directions is the signal; disagreement is 'not established'. Margins within +/-0.31 (the prior study's measured self-preference on calibration) are read as no difference rather than as a small effect.

| axis | scope | anthropic.claude-opus-5 (claude) | gpt-5.5 (gpt) | verdict |
|---|---|---|---|---|
| calibration_score | ALL AGENTS POOLED | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |
| calibration_score | bustout | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |
| calibration_score | identity | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |
| calibration_score | rings | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |
| calibration_score | synthetic | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |
| substance_score | ALL AGENTS POOLED | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |
| substance_score | bustout | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |
| substance_score | identity | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |
| substance_score | rings | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |
| substance_score | synthetic | — | — | NOT ESTABLISHED - at least one judge produced no verdict here |

Margins are GPT minus Claude: positive favours GPT.

## Each judge's measured preference for its own family

This comparison cannot be judged by one model without the judge's own identity deciding it. The prior tier study measured its Opus 5 judge scoring its own family +0.31 on calibration and +0.78 on substance while the deterministic band-agreement margin moved only -0.026 - i.e. the judge-scored margin was mostly preference. A GPT judge has the mirror problem. So both are used: global.anthropic.claude-opus-5 (which is ALSO a candidate here, the strongest possible form of the conflict, and is expected to favour Claude) and openai.gpt-5.5 (NOT a candidate - the candidates are the 5.6 variants - but same family and vendor, so expected to favour GPT). Where the two agree, the finding survives both biases and is reported as established. Where they disagree, the axis is reported as NOT ESTABLISHED and no recommendation rests on it. Neither margin is 'corrected' by subtracting a bias estimate: that would produce a number that looks adjusted and was never measured. The deterministic layers - band agreement, anti-false-positive escalation, grounding, output contract - involve no judge at all and are what the recommendation actually rests on.

- `global.anthropic.claude-opus-5` (claude family): not assessable on this sample
- `openai.gpt-5.5` (gpt family): not assessable on this sample
- Deterministic cross-check, GPT minus Claude, no judge involved: band agreement -0.073, contract violations -0.756, grounded fraction -0.276

## The length gap: padding or lost evidence?

chars_ratio well above signals_cited_ratio and grounded_ratio means the longer (Claude) analysis is padding: same evidence, more words. chars_ratio at or below those ratios means the shorter (GPT) analysis really is dropping evidence, which outweighs any latency or cost win.

| scope | Claude | GPT | paired | chars ratio | evidence ratio | signals-cited ratio | grounded ratio | same band | finding |
|---|---|---|---|---|---|---|---|---|---|
| ALL AGENTS POOLED | opus-5 | luna | 21 | 2.97x | 5.22x | 1.0x | 2.5x | 21/21 | length and evidence scale together (2.97x chars, 1.0x signals) |
| bustout | opus-5 | luna | 6 | 4.62x | 4.95x | 7.0x | 3.0x | 6/6 | EVIDENCE LOST: the shorter GPT analysis names 0.14x the signals for 0.22x the length |
| identity | opus-5 | luna | 3 | 3.95x | 3.0x | 1.0x | 2.0x | 3/3 | PADDING: the Claude analysis is 3.95x longer but names only 1.0x the payload's signals |
| rings | opus-5 | luna | 6 | 2.83x | 7.75x | 1.0x | 1.5x | 6/6 | PADDING: the Claude analysis is 2.83x longer but names only 1.0x the payload's signals |
| synthetic | opus-5 | luna | 6 | 2.02x | 10.0x | 0.67x | 2.75x | 6/6 | length and evidence scale together (2.02x chars, 0.67x signals) |
| ALL AGENTS POOLED | opus-5 | terra | 21 | 3.45x | 5.0x | 1.33x | 1.5x | 21/21 | PADDING: the Claude analysis is 3.45x longer but names only 1.33x the payload's signals |
| bustout | opus-5 | terra | 6 | 4.57x | 4.95x | 5.25x | 2.25x | 6/6 | EVIDENCE LOST: the shorter GPT analysis names 0.19x the signals for 0.22x the length |
| identity | opus-5 | terra | 3 | 3.86x | 4.5x | 1.5x | 1.5x | 3/3 | PADDING: the Claude analysis is 3.86x longer but names only 1.5x the payload's signals |
| rings | opus-5 | terra | 6 | 3.4x | 6.88x | 1.0x | 1.42x | 6/6 | PADDING: the Claude analysis is 3.4x longer but names only 1.0x the payload's signals |
| synthetic | opus-5 | terra | 6 | 1.78x | 5.0x | 0.92x | 1.45x | 6/6 | length and evidence scale together (1.78x chars, 0.92x signals) |
| ALL AGENTS POOLED | opus-5 | sol | 24 | 3.75x | 5.15x | 1.67x | 2.67x | 18/24 | PADDING: the Claude analysis is 3.75x longer but names only 1.67x the payload's signals |
| bustout | opus-5 | sol | 6 | 5.38x | 4.23x | 7.0x | 4.25x | 6/6 | EVIDENCE LOST: the shorter GPT analysis names 0.14x the signals for 0.19x the length |
| identity | opus-5 | sol | 6 | 3.48x | 3.93x | 2.0x | 3.5x | 1/6 | length and evidence scale together (3.48x chars, 2.0x signals) |
| rings | opus-5 | sol | 6 | 3.75x | 5.2x | 1.0x | 1.5x | 5/6 | PADDING: the Claude analysis is 3.75x longer but names only 1.0x the payload's signals |
| synthetic | opus-5 | sol | 6 | 2.3x | 5.4x | 1.12x | 3.5x | 6/6 | length and evidence scale together (2.3x chars, 1.12x signals) |
| ALL AGENTS POOLED | sonnet-5 | luna | 20 | 2.21x | 3.17x | 1.29x | 1.83x | 18/20 | length and evidence scale together (2.21x chars, 1.29x signals) |
| bustout | sonnet-5 | luna | 6 | 2.29x | 2.55x | 4.5x | 2.0x | 5/6 | EVIDENCE LOST: the shorter GPT analysis names 0.22x the signals for 0.44x the length |
| identity | sonnet-5 | luna | 2 | 1.98x | 2.04x | 1.29x | 0.62x | 2/2 | PADDING: the Claude analysis is 1.98x longer but names only 1.29x the payload's signals |
| rings | sonnet-5 | luna | 6 | 2.06x | 5.17x | 1.0x | 1.25x | 6/6 | PADDING: the Claude analysis is 2.06x longer but names only 1.0x the payload's signals |
| synthetic | sonnet-5 | luna | 6 | 2.33x | 4.33x | 1.0x | 2.25x | 5/6 | length and evidence scale together (2.33x chars, 1.0x signals) |
| ALL AGENTS POOLED | sonnet-5 | terra | 19 | 2.34x | 3.09x | 1.5x | 1.33x | 17/19 | PADDING: the Claude analysis is 2.34x longer but names only 1.5x the payload's signals |
| bustout | sonnet-5 | terra | 6 | 2.26x | 2.77x | 2.75x | 1.33x | 5/6 | EVIDENCE LOST: the shorter GPT analysis names 0.36x the signals for 0.44x the length |
| identity | sonnet-5 | terra | 1 | 3.35x | 14.0x | 5.0x | 1.5x | 1/1 | EVIDENCE LOST: the shorter GPT analysis names 0.2x the signals for 0.3x the length |
| rings | sonnet-5 | terra | 6 | 2.39x | 5.25x | 1.0x | 1.17x | 6/6 | PADDING: the Claude analysis is 2.39x longer but names only 1.0x the payload's signals |
| synthetic | sonnet-5 | terra | 6 | 2.06x | 3.17x | 1.42x | 1.38x | 5/6 | PADDING: the Claude analysis is 2.06x longer but names only 1.42x the payload's signals |
| ALL AGENTS POOLED | sonnet-5 | sol | 21 | 2.83x | 3.4x | 2.5x | 1.67x | 15/21 | length and evidence scale together (2.83x chars, 2.5x signals) |
| bustout | sonnet-5 | sol | 6 | 2.96x | 2.62x | 3.5x | 2.0x | 5/6 | EVIDENCE LOST: the shorter GPT analysis names 0.29x the signals for 0.34x the length |
| identity | sonnet-5 | sol | 3 | 2.62x | 2.8x | 5.0x | 1.0x | 0/3 | EVIDENCE LOST: the shorter GPT analysis names 0.2x the signals for 0.38x the length |
| rings | sonnet-5 | sol | 6 | 2.79x | 3.95x | 1.0x | 1.42x | 5/6 | PADDING: the Claude analysis is 2.79x longer but names only 1.0x the payload's signals |
| synthetic | sonnet-5 | sol | 6 | 2.71x | 3.83x | 2.42x | 3.0x | 5/6 | length and evidence scale together (2.71x chars, 2.42x signals) |

## Do the two shared Claude calibration failures reproduce in GPT?

### APP-1014 / rings — pinned **LOW RISK**, every prior Claude tier said **POSSIBLE RISK** (RNG-020)

- GPT reproduced it 3/3; Claude 2/2. Correct band: GPT 0/3, Claude 0/2.
  - `opus-5` (claude): POSSIBLE RISK
  - `sonnet-5` (claude): POSSIBLE RISK
  - `luna` (gpt): POSSIBLE RISK
  - `sol` (gpt): POSSIBLE RISK
  - `terra` (gpt): POSSIBLE RISK

### APP-1004 / bustout — pinned **POSSIBLE RISK**, every prior Claude tier said **HIGH RISK** (BST-020)

- GPT reproduced it 3/3; Claude 2/2. Correct band: GPT 0/3, Claude 0/2.
  - `opus-5` (claude): HIGH RISK
  - `sonnet-5` (claude): HIGH RISK
  - `luna` (gpt): HIGH RISK
  - `sol` (gpt): HIGH RISK
  - `terra` (gpt): HIGH RISK

## Caveats

- This comparison cannot be judged by one model without the judge's own identity deciding it. The prior tier study measured its Opus 5 judge scoring its own family +0.31 on calibration and +0.78 on substance while the deterministic band-agreement margin moved only -0.026 - i.e. the judge-scored margin was mostly preference. A GPT judge has the mirror problem. So both are used: global.anthropic.claude-opus-5 (which is ALSO a candidate here, the strongest possible form of the conflict, and is expected to favour Claude) and openai.gpt-5.5 (NOT a candidate - the candidates are the 5.6 variants - but same family and vendor, so expected to favour GPT). Where the two agree, the finding survives both biases and is reported as established. Where they disagree, the axis is reported as NOT ESTABLISHED and no recommendation rests on it. Neither margin is 'corrected' by subtracting a bias estimate: that would produce a number that looks adjusted and was never measured. The deterministic layers - band agreement, anti-false-positive escalation, grounding, output contract - involve no judge at all and are what the recommendation actually rests on.
- The recommendation rests on the DETERMINISTIC layers - band agreement against the pinned fixture, escalation on the anti-false-positive fixtures, _context grounding, and agents.output_contract.check. The judged calibration and substance scores are subjective and are reported separately, and an axis where the two judges disagree yields no finding at all.
- 6 applications per cell. A band-agreement difference of one case is 17 points, so a one-case gap between two models is inside this sample's resolution by construction.
- Only 2 of the 5 anti-false-positive fixtures (APP-1009, APP-1010) exercise the four agents swept here; APP-1006/1007/1008 target employment, dealer and income, which were not swept. The false-positive finding is therefore narrow and a wider anti-FP sweep is the obvious next measurement.
- Percentiles come from evals.bench.summarize_values: p50 needs 5 samples, p95 needs 20. Cells below those counts report the refusal, not a number.
- GPT specialists receive the verbatim prompt concatenated with the user turn (the mantle Responses shape agents.synthesize already uses), while Claude specialists receive it as a cached system prompt. That is a real difference in how the two families are invoked and it cannot be removed while GPT-5.x is off Converse.
- Latency and cost here are single serial calls, not the fan-out. The end-to-end consequence of a swap belongs to the pipeline benchmark.
