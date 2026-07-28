# Is the GPT set as good at the fraud analysis?

Blind, two judges: `global.anthropic.claude-opus-5`, `openai.gpt-5.5`. 4 agents x 6 applications x 5 models. Region `us-east-1` (mantle `us-east-1`). Generated 2026-07-28T20:05:14+00:00.

Measured spend: specialists $1.4584, judges $2.1401, total $3.5985.

## The verdict, per (agent, GPT model)

### bustout (incumbent `sonnet-5`)

- incumbent `sonnet-5`: band 1/3, anti-FP false positives 0/1, false negatives 0/2, grounded 0.571, contract violations 3, 25.313s, $0.0254/call, 2127 out tok
- Sample: 3 application(s) per cell; a band claim needs 5. Anti-false-positive fixtures in this sample: 1.

### identity (incumbent `sonnet-5`)

- incumbent `sonnet-5`: band 2/2, anti-FP false positives 0/1, false negatives 0/1, grounded 0.429, contract violations 0, 18.925s, $0.0218/call, 1545.0 out tok
- Sample: 2 application(s) per cell; a band claim needs 5. Anti-false-positive fixtures in this sample: 1.

### rings (incumbent `opus-5`)

- incumbent `opus-5`: band 4/5, anti-FP false positives 0/1, false negatives 0/3, grounded 1.0, contract violations 5, 29.43s, $0.0602/call, 2080 out tok
- **`luna`: n TOO SMALL - 3 usable application(s); a band claim needs 5** — band 3/3, anti-FP false positives 0/1, false negatives 0/2, grounded 0.5, contract violations 0, 3.68s, $0.0052/call, 425 out tok
- **`sol`: n TOO SMALL - 1 usable application(s); a band claim needs 5** — band 0/1, anti-FP false positives 0/0, false negatives 0/1, grounded 0.75, contract violations 0, 5.6s, $0.0226/call, 356 out tok
- **`terra`: n TOO SMALL - 0 usable application(s); a band claim needs 5** — band 0/0, anti-FP false positives 0/0, false negatives 0/0, grounded —, contract violations 0, —s, $—/call, — out tok
- Sample: 5 application(s) per cell; a band claim needs 5. Anti-false-positive fixtures in this sample: 1.

### synthetic (incumbent `opus-5`)

- incumbent `opus-5`: band 4/5, anti-FP false positives 0/1, false negatives 1/2, grounded 0.6, contract violations 2, 8.67s, $0.0293/call, 530 out tok
- **`luna`: n TOO SMALL - 3 usable application(s); a band claim needs 5** — band 3/3, anti-FP false positives 0/1, false negatives 0/1, grounded 0.2, contract violations 0, 2.34s, $0.0046/call, 210 out tok
- **`sol`: n TOO SMALL - 2 usable application(s); a band claim needs 5** — band 2/2, anti-FP false positives 0/1, false negatives 0/1, grounded 0.25, contract violations 0, 3.335s, $0.0237/call, 213.5 out tok
- **`terra`: n TOO SMALL - 3 usable application(s); a band claim needs 5** — band 3/3, anti-FP false positives 0/1, false negatives 0/1, grounded 0.4, contract violations 0, 7.2s, $0.0104/call, 166 out tok
- Sample: 5 application(s) per cell; a band claim needs 5. Anti-false-positive fixtures in this sample: 1.

## Per (agent, model): deterministic scores

| agent | set | model | n | band | escalations | dismissals | anti-FP false pos | contract viol | grounded frac p50 | signals cited p50 | wall p50 s | $/call p50 | out tok p50 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bustout | claude | opus-5 | 3 | 2/3 | 1 | 0 | 0/1 | 3 | 0.714 | 7 | 34.396 | 0.073 | 2536 |
| bustout | claude | sonnet-5 | 3 | 1/3 | 2 | 0 | 0/1 | 3 | 0.571 | 5 | 25.313 | 0.0254 | 2127 |
| identity | claude | opus-5 | 3 | 3/3 | 0 | 0 | 0/1 | 1 | 0.857 | 4 | 23.111 | 0.0549 | 1480 |
| identity | claude | sonnet-5 | 2 | 2/2 | 0 | 0 | 0/1 | 0 | 0.429 | 5.5 | 18.925 | 0.0218 | 1545.0 |
| rings | gpt | luna | 3 | 3/3 | 0 | 0 | 0/1 | 0 | 0.5 | 4 | 3.68 | 0.0052 | 425 |
| rings | claude | opus-5 | 5 | 4/5 | 1 | 0 | 0/1 | 5 | 1.0 | 4 | 29.43 | 0.0602 | 2080 |
| rings | gpt | sol | 1 | 0/1 | 1 | 0 | 0/0 | 0 | 0.75 | 4 | 5.6 | 0.0226 | 356 |
| rings | claude | sonnet-5 | 5 | 4/5 | 1 | 0 | 0/1 | 6 | 0.75 | 4 | 18.0 | 0.0207 | 1426 |
| rings | gpt | terra | 0 | 0/0 | 0 | 0 | 0/0 | 0 | — | — | — | — | — |
| synthetic | gpt | luna | 3 | 3/3 | 0 | 0 | 0/1 | 0 | 0.2 | 6 | 2.34 | 0.0046 | 210 |
| synthetic | claude | opus-5 | 5 | 4/5 | 0 | 1 | 0/1 | 2 | 0.6 | 2 | 8.67 | 0.0293 | 530 |
| synthetic | gpt | sol | 2 | 2/2 | 0 | 0 | 0/1 | 0 | 0.25 | 2.0 | 3.335 | 0.0237 | 213.5 |
| synthetic | claude | sonnet-5 | 5 | 5/5 | 0 | 0 | 0/1 | 3 | 0.5 | 6 | 17.03 | 0.0217 | 1350 |
| synthetic | gpt | terra | 3 | 3/3 | 0 | 0 | 0/1 | 0 | 0.4 | 5 | 7.2 | 0.0104 | 166 |

## Per (agent, model): what each judge thought

| agent | set | model | claude-opus-5 cal/sub | 5 cal/sub |
|---|---|---|---|---|
| bustout | claude | opus-5 | 3.67/4.0 (n=3) | —/— (n=0) |
| bustout | claude | sonnet-5 | 2.67/4.0 (n=3) | —/— (n=0) |
| identity | claude | opus-5 | 5.0/4.67 (n=3) | —/— (n=0) |
| identity | claude | sonnet-5 | 5.0/4.0 (n=2) | —/— (n=0) |
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

| axis | scope | claude-opus-5 | 5 | verdict |
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
- Deterministic cross-check, GPT minus Claude, no judge involved: band agreement 0.11, contract violations -0.742, grounded fraction -0.258

## The length gap: padding or lost evidence?

chars_ratio well above signals_cited_ratio and grounded_ratio means the longer (Claude) analysis is padding: same evidence, more words. chars_ratio at or below those ratios means the shorter (GPT) analysis really is dropping evidence, which outweighs any latency or cost win.

| scope | Claude | GPT | paired | chars ratio | evidence ratio | signals-cited ratio | grounded ratio | same band | finding |
|---|---|---|---|---|---|---|---|---|---|
| ALL AGENTS POOLED | opus-5 | luna | 6 | 2.41x | 7.75x | 1.0x | 2.25x | 6/6 | length and evidence scale together (2.41x chars, 1.0x signals) |
| rings | opus-5 | luna | 3 | 2.69x | 10.33x | 1.0x | 1.5x | 3/3 | PADDING: the Claude analysis is 2.69x longer but names only 1.0x the payload's signals |
| synthetic | opus-5 | luna | 3 | 2.05x | 4.83x | 0.83x | 2.5x | 3/3 | length and evidence scale together (2.05x chars, 0.83x signals) |
| ALL AGENTS POOLED | opus-5 | terra | 3 | 2.12x | 4.83x | 1.17x | 1.5x | 3/3 | PADDING: the Claude analysis is 2.12x longer but names only 1.17x the payload's signals |
| synthetic | opus-5 | terra | 3 | 2.12x | 4.83x | 1.17x | 1.5x | 3/3 | PADDING: the Claude analysis is 2.12x longer but names only 1.17x the payload's signals |
| ALL AGENTS POOLED | opus-5 | sol | 3 | 3.05x | 5.8x | 2.5x | 1.5x | 2/3 | length and evidence scale together (3.05x chars, 2.5x signals) |
| rings | opus-5 | sol | 1 | 5.25x | 15.5x | 1.0x | 1.33x | 0/1 | PADDING: the Claude analysis is 5.25x longer but names only 1.0x the payload's signals |
| synthetic | opus-5 | sol | 2 | 2.68x | 5.23x | 3.0x | 3.75x | 2/2 | EVIDENCE LOST: the shorter GPT analysis names 0.33x the signals for 0.37x the length |
| ALL AGENTS POOLED | sonnet-5 | luna | 6 | 1.98x | 5.17x | 1.0x | 1.58x | 6/6 | PADDING: the Claude analysis is 1.98x longer but names only 1.0x the payload's signals |
| rings | sonnet-5 | luna | 3 | 1.96x | 6.0x | 1.0x | 1.0x | 3/3 | PADDING: the Claude analysis is 1.96x longer but names only 1.0x the payload's signals |
| synthetic | sonnet-5 | luna | 3 | 1.99x | 3.67x | 0.83x | 1.67x | 3/3 | length and evidence scale together (1.99x chars, 0.83x signals) |
| ALL AGENTS POOLED | sonnet-5 | terra | 3 | 2.01x | 3.33x | 1.17x | 1.25x | 3/3 | PADDING: the Claude analysis is 2.01x longer but names only 1.17x the payload's signals |
| synthetic | sonnet-5 | terra | 3 | 2.01x | 3.33x | 1.17x | 1.25x | 3/3 | PADDING: the Claude analysis is 2.01x longer but names only 1.17x the payload's signals |
| ALL AGENTS POOLED | sonnet-5 | sol | 3 | 2.57x | 4.0x | 2.5x | 1.25x | 2/3 | length and evidence scale together (2.57x chars, 2.5x signals) |
| rings | sonnet-5 | sol | 1 | 3.7x | 10.0x | 1.0x | 1.0x | 0/1 | PADDING: the Claude analysis is 3.7x longer but names only 1.0x the payload's signals |
| synthetic | sonnet-5 | sol | 2 | 2.34x | 3.83x | 3.0x | 3.12x | 2/2 | EVIDENCE LOST: the shorter GPT analysis names 0.33x the signals for 0.43x the length |

## Do the two shared Claude calibration failures reproduce in GPT?

### APP-1014 / rings — pinned **LOW RISK**, every prior Claude tier said **POSSIBLE RISK** (RNG-020)

- GPT reproduced it 0/0; Claude 2/2. Correct band: GPT 0/0, Claude 0/2.
  - `opus-5` (claude): POSSIBLE RISK
  - `sonnet-5` (claude): POSSIBLE RISK

### APP-1004 / bustout — pinned **POSSIBLE RISK**, every prior Claude tier said **HIGH RISK** (BST-020)

- GPT reproduced it 0/0; Claude 2/2. Correct band: GPT 0/0, Claude 0/2.
  - `opus-5` (claude): HIGH RISK
  - `sonnet-5` (claude): HIGH RISK

## Caveats

- This comparison cannot be judged by one model without the judge's own identity deciding it. The prior tier study measured its Opus 5 judge scoring its own family +0.31 on calibration and +0.78 on substance while the deterministic band-agreement margin moved only -0.026 - i.e. the judge-scored margin was mostly preference. A GPT judge has the mirror problem. So both are used: global.anthropic.claude-opus-5 (which is ALSO a candidate here, the strongest possible form of the conflict, and is expected to favour Claude) and openai.gpt-5.5 (NOT a candidate - the candidates are the 5.6 variants - but same family and vendor, so expected to favour GPT). Where the two agree, the finding survives both biases and is reported as established. Where they disagree, the axis is reported as NOT ESTABLISHED and no recommendation rests on it. Neither margin is 'corrected' by subtracting a bias estimate: that would produce a number that looks adjusted and was never measured. The deterministic layers - band agreement, anti-false-positive escalation, grounding, output contract - involve no judge at all and are what the recommendation actually rests on.
- The recommendation rests on the DETERMINISTIC layers - band agreement against the pinned fixture, escalation on the anti-false-positive fixtures, _context grounding, and agents.output_contract.check. The judged calibration and substance scores are subjective and are reported separately, and an axis where the two judges disagree yields no finding at all.
- 6 applications per cell. A band-agreement difference of one case is 17 points, so a one-case gap between two models is inside this sample's resolution by construction.
- Only 2 of the 5 anti-false-positive fixtures (APP-1009, APP-1010) exercise the four agents swept here; APP-1006/1007/1008 target employment, dealer and income, which were not swept. The false-positive finding is therefore narrow and a wider anti-FP sweep is the obvious next measurement.
- Percentiles come from evals.bench.summarize_values: p50 needs 5 samples, p95 needs 20. Cells below those counts report the refusal, not a number.
- GPT specialists receive the verbatim prompt concatenated with the user turn (the mantle Responses shape agents.synthesize already uses), while Claude specialists receive it as a cached system prompt. That is a real difference in how the two families are invoked and it cannot be removed while GPT-5.x is off Converse.
- Latency and cost here are single serial calls, not the fan-out. The end-to-end consequence of a swap belongs to the pipeline benchmark.
