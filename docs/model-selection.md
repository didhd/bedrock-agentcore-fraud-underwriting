# Model and region selection

What runs on what, and the measurement behind each choice. Every number below was
measured by this repo unless it is explicitly marked THIRD-PARTY or PROJECTION. Every
latency and cost carries its sample count. Nothing here is nominal or extrapolated.

**If you read one section, read
[Should the specialists be GPT too?](#should-the-specialists-be-gpt-too-the-answer-is-not-yet).**
GPT-5.6 measured 4–8x faster and 3–12x cheaper than the Claude specialists on the real
fraud prompts, and we are **not** taking it yet. That section says why, what the hold
costs (a projected 47.8%), and the single experiment that would change the answer.

Encoded in code at `agents/mantle.py` (`MEASURED_SYNTHESIS_LATENCY`) and
`agents/models.py` (`SPECIALIST_MODELS`, `TIER_RATIONALE`, `MAX_TOKENS_BY_AGENT`,
`DEFAULT_SYNTHESIZER_MODEL`), and asserted by `tests/test_models.py`,
`tests/test_pricing.py` and `tests/test_mantle.py` so the page cannot drift from the code
silently.

## The assignment

| Role | Model | max_tokens | Endpoint | Evidence grade |
|---|---|---|---|---|
| dealer, straw | Claude Haiku 4.5 | 3072 | Bedrock Converse | MEASUREMENT (latency/cost only) |
| identity, employment, income, bustout, **synthetic**, **rings** | Claude Sonnet 5 | 4096 | Bedrock Converse | mixed — see the table below |
| master synthesizer | **GPT-5.6 Luna** | 4096 | `bedrock-mantle` | MEASUREMENT (`us-east-1`) |

**All eight specialists stay on Claude — and that is now a decision, not a default.**
A cross-family comparison *was* run (2026-07-28, 120 blind-judged analyses, two judges,
$16.89 measured spend), it found GPT-5.6 **4–8x faster and 3–12x cheaper**, and the
specialists are held anyway. The reason is one deterministic measurement, and the price of
holding is stated below rather than left implied. See
[Should the specialists be GPT too?](#should-the-specialists-be-gpt-too-the-answer-is-not-yet).

**There is no Opus 5 agent.** `synthetic` and `rings` were on Opus 5 until 2026-07-28
because a reading of the customer's prompts said fraud rings needs heavy multi-signal
reasoning. Measurement contradicted that on both. This page records the correction,
because the reasoning that produced the original assignment is exactly the reasoning a
customer will apply to the next agent.

### Why each agent sits where it sits

Three evidence grades, and the difference between them is the point:

- **MEASUREMENT** — a measurement on *this agent* decided the assignment.
- **HELD** — a comparison was run and **deliberately not acted on**, because n was too
  small to move a fraud dimension.
- **UNPROVEN** — no comparison exists for this agent. Nothing is inferred from its
  neighbours.

| Agent | Model | Grade | Claude-tier swept? | GPT-swept? | p50 | p95 | $/call | Slowest agent in N of 42 |
|---|---|---|---|---|---|---|---|---|
| rings | Sonnet 5 | MEASUREMENT | yes, n=5 apps | yes, n=6 | 22.05 s (n=5) | — | $0.0241 | **25** |
| synthetic | Sonnet 5 | MEASUREMENT | yes, n=5 apps | yes, n=6 | 13.54 s (n=5) | — | $0.0194 | 1 |
| bustout | Sonnet 5 | **HELD** | yes, n=3 apps | yes, n=6 | 14.46 s (n=42) | 35.71 s | $0.0170 | 7 |
| identity | Sonnet 5 | **HELD** | yes, n=3 apps | yes, n=6 | 12.56 s (n=42) | 33.40 s | $0.0173 | 4 |
| income | Sonnet 5 | **UNPROVEN** | **no** | **no** | 8.09 s (n=42) | 31.44 s | $0.0154 | 5 |
| employment | Sonnet 5 | **UNPROVEN** | **no** | **no** | 7.48 s (n=42) | 17.64 s | $0.0102 | 0 |
| dealer | Haiku 4.5 | MEASUREMENT | **no** | **no** | 6.56 s (n=42) | 14.56 s | $0.0054 | 0 |
| straw | Haiku 4.5 | MEASUREMENT | **no** | **no** | 4.40 s (n=42) | 8.76 s | $0.0035 | 0 |
| synthesizer | GPT-5.6 Luna | MEASUREMENT | **no** | n/a — it *is* GPT | 4.20 s (n=42) | 5.59 s | $0.0106 | n/a |

The two sweep columns are deliberately separate. An agent can be compared *within* Claude
and never compared against another family, and **four of the eight are in exactly that
state.** Conflating the two is how a within-family result gets quoted as coverage of a
cross-family question.

n=42 rows come from the 42-run pipeline benchmark
(`evals/results/pipeline_14x3_us-east-1.json`). The two n=5 rows come from the tier sweep
(`evals/results/tier_quality.json`) and are **medians of five, on five applications** — no
p95 is offered for them, because five samples do not support one.

Note `dealer` and `straw` are graded MEASUREMENT but were **never quality-swept**: their
case is latency and cost at n=42, plus the scope their own prompts define. No blind judge
has compared their analyses against a larger model. That is a real gap, and it is stated
rather than papered over with the word "right-sized".

## Should the specialists be GPT too? The answer is NOT YET

The synthesizer already runs on GPT-5.6 Luna, on a measurement. The obvious next question
is whether the eight specialists should follow it. **This was measured, and the answer is
no — for now, and for two specific reasons.** It is the section to read first, because it
is the one where the evidence and the deadline pull in opposite directions.

**What was measured.** `evals/results/set_comparison.json`, 2026-07-28, `us-east-1`:
4 agents (rings, synthetic, bustout, identity) x **6 applications** x 5 models
(Claude Opus 5, Sonnet 5; GPT-5.6 Luna, Terra, Sol) = **120 analyses**. Model identifiers
stripped before judging, order shuffled under seed 20260728. **$16.8869 measured spend**,
$14.1343 of it on judging.

**Two judges, biased in opposite directions.** This comparison cannot be judged by one
model without the judge's identity deciding it — the prior tier study measured its Opus 5
judge preferring its own family by **+0.31 calibration / +0.78 substance** while the
band-agreement margin it could not influence moved only **−0.026**. So both were used:
Claude Opus 5 (*also a candidate* — the strongest form of the conflict) and GPT-5.5 (same
family as the candidates, not itself one). An axis where both agree has survived opposite
biases. Nothing is "bias-corrected"; where they disagree, the axis is reported
**NOT ESTABLISHED** and no recommendation rests on it.

### The case for GPT is strong, and none of it is disputed

| Axis | Claude | GPT | Notes |
|---|---|---|---|
| Wall clock p50, pooled | 18.88 s | **4.73 s** | rings: Luna 3.20 s vs Opus 5's 26.22 s — **8.2x** |
| $/call p50, pooled | $0.03125 | **$0.01066** | rings: $0.0050 vs $0.0583 — **11.7x** |
| Output tokens p50 | 1422 | 288 | GPT writes a fifth of the words |
| Output-contract clean rows | 17/48 | **72/72** | deterministic, `agents.output_contract.check` |
| Judged substance, pooled | — | **+0.398 / +0.684** | **better on BOTH judges** |

Two of those deserve emphasis because they are the opposite of what "cheaper and shorter"
predicts:

- **The Claude judge scored GPT higher on substance** — −0.398 *against its own family*,
  i.e. against its own measured preference. That is much harder to dismiss than a
  single-judge win.
- **The length gap is padding, not lost evidence.** Paired on identical payloads, Opus
  writes **2.83x** Luna's characters on rings while naming **1.00x** the payload's signals,
  and **3.44x** on identity for **1.17x**. Same band in **23 of 24** paired comparisons.
  This is the same finding the Claude-only tier work reached (1.76x characters for 1.71x
  evidence). **Brevity is not the defect here** — which matters, because "GPT writes a
  fifth of the words on a fraud product" was the intuitive objection, and it does not
  survive pairing.

### What decided it: `_context` grounding

Every specialist prompt requires signals to be validated against the paired `*_context`
columns — `rings_agent_prompt.txt` says *"You MUST validate signals using relevant context
(columns that end in `_context`)"*. This measures the fraction of the signals in view that
the analysis actually validates that way. **No judge is involved.**

| Agent | Opus 5 | Sonnet 5 | Luna | Terra | Sol |
|---|---|---|---|---|---|
| rings | 0.875 | 0.792 | 0.625 | 0.625 | 0.583 |
| identity | 0.738 | 0.476 | 0.357 | 0.381 | 0.238 |
| bustout | 0.833 | 0.524 | 0.286 | 0.310 | 0.215 |
| synthetic | 0.517 | 0.383 | **0.150** | 0.350 | 0.167 |
| **Pooled** | **Claude 0.642** | | **GPT 0.357** | | |

**GPT is lower on 4 of 4 agents against both Claude incumbents.** That is not one bad cell
dragging an average, and it is the axis where the customer's prompt states a requirement
and GPT measurably meets it less often. It is invisible in the band, the latency and the
cost — which is exactly why it decides. On `synthetic` it collapses to 0.150 against Opus's
0.517, which is below half the incumbent and disqualifying on the study's own rule.

### And the coverage is half the fan-out

**4 of 8 agents were swept.** `dealer`, `straw`, `employment` and `income` were never run
on GPT at all. Worse for the false-positive question: only **2 of the customer's 5
anti-false-positive fixtures** exercise the swept four — **APP-1006, APP-1007 and APP-1008
target employment, dealer and income** and were never run on GPT.

That matters because the study contains a live demonstration of what an unswept agent
could hide. **GPT-5.6 Sol on identity escalated BOTH anti-false-positive fixtures**
(APP-1009, APP-1010) against the incumbent's zero, **dismissed** a corroborated application
(APP-1014), and banded 2/6 against Sonnet's 3/3 — at $0.0269/call, **4x Luna's price**.
Paying more did not buy calibration.

### Per-agent verdicts at n=6

n=6 clears the study's own bar for a band claim (5), so these verdicts *are* claimable.
"As good" means the candidate lost on none of: band agreement, escalation on an
anti-false-positive fixture, dismissal of a corroborated application, or grounded fraction
below half the incumbent's.

| Agent | Luna | Terra | Sol |
|---|---|---|---|
| rings | as good | as good | **not** — band 4/6 vs 5/6 |
| bustout | as good | as good | **not** — grounding |
| identity | as good | **not** — band 4/5, one analysis declared no band | **not** — 2 anti-FP false positives, 1 false negative, band 2/6 |
| synthetic | **not** — grounded 0.150 vs 0.517 | as good | **not** — grounded 0.100 vs 0.517 |

**Luna is "as good" on 3 of 4 and is still not taken.** That is the honest shape of this
recommendation: the per-agent evidence would support moving rings, bustout and identity to
Luna today. It is not being done because the grounding gap is real on all three, and
because moving three of eight leaves a split fan-out whose remaining agents have *less*
evidence than the ones that moved.

### What holding costs — and it is not free

Stated because a hold with an unstated price is not a decision. Both figures are
**PROJECTIONS** from the measured per-agent tokens (`projected_cost_if_gpt_specialists`),
not benchmarks:

| | Hold on Claude | Move the four swept agents to Luna |
|---|---|---|
| $/adjudication (projected, 2026-07-28 rates) | $0.1247 | **$0.0651** |
| Delta | — | **−$0.0596 (−47.8%)** |
| Fan-out floor lower bound | 22.05 s (rings) | **8.09 s (income)** |

We are declining a projected **47.8% cost reduction** and roughly **14 s of fan-out floor**.
Note what the second column exposes: after such a move the binding agent becomes `income`,
**which has no quality evidence of any kind** — so the agent with the least evidence would
set the pipeline's latency. That is an argument for sweeping it, and a second argument
against a partial migration.

The floor figures are **lower bounds**, not predicted end-to-ends: fan-out is bounded by a
per-run *maximum*, which per-agent medians cannot reconstruct.

### What would change this answer

1. **One prompt-engineering attempt on grounding.** Instruct the model to quote the paired
   `_context` row it validated against, then re-measure `grounded_fraction`. The fix may be
   cheap, and it is the only thing standing between this evidence and a migration.
2. **Extend the sweep to the other four agents**, including APP-1006/1007/1008 so the
   anti-false-positive result covers the whole fan-out.

**If grounding closes, the latency and cost case is already made and this assignment should
change.** Nothing here says GPT is worse at the fraud judgement — on band agreement the two
families are within one case on every swept agent except identity/Sol, and one case is 17
points at n=6. It says the evidence does not yet cover enough of the fan-out to move it
before a customer milestone, plus one measured compliance gap.

**One asymmetry that cannot be removed and rides along with every GPT number here:**
GPT-5.x is not on Bedrock Converse, so a GPT specialist receives the prompt concatenated
into the user turn while a Claude specialist receives it as a cached system prompt.

**The plumbing is not the blocker.** `agents/fanout.py` routes a GPT specialist today
(`transport_for('openai.gpt-5.6-luna') == 'bedrock-mantle'`), so reversing this decision is
one value in `SPECIALIST_MODELS`. The hold is a measurement result, not a missing code path.

### rings: Opus 5 → Sonnet 5

The single largest median-latency lever in the pipeline, and the clearest case that the
original prompt-reading was wrong. n=5 applications, paired so only same-payload analyses
are compared:

| | Opus 5 | Sonnet 5 |
|---|---|---|
| Band agreement vs pinned expectation | 4/5 | **4/5** |
| Judge-confirmed false positives | 1 | **1** |
| Judge calibration (mean) | 4.20 | 4.00 |
| Wall clock p50 | 32.81 s | **22.05 s** |
| $/call p50 | $0.0696 | **$0.0241** |
| Output tokens p50 | 2448 | 2026 |

Opus's **only** remaining advantage is +0.20 judge calibration. The judge is Opus 5, and
on its own 16 rows it scored calibration **+0.31** and substance **+0.78** versus the other
32 — while the band-agreement margin it cannot influence moved **−0.026**. A +0.20 lead
inside a +0.31 self-preference margin is not distinguishable from bias, so it is refused.

Its extra length is padding, not analysis: **1.76× the characters carrying 1.71× the
distinct evidence, for a judge substance delta of +0** (n=5 paired). And the verbosity does
*not* drift toward over-escalation — in **0 of 48** paired comparisons did the longer
analysis escalate where the shorter one did not. So the case against Opus here is cost and
latency, not a false-positive risk.

Why it matters more here than anywhere else: rings was the slowest agent in **25 of 42**
runs, and the fan-out floor is **96.3% of fan-out** time. Fan-out cannot finish before its
slowest member.

**Ceiling on this fix, measured:** slowest-minus-second-slowest is **6.34 s p50** (n=42).
Even an instantaneous rings leaves the next agent as the floor. This is a median
improvement, not a tail fix.

### synthetic: Opus 5 → Sonnet 5 — and this one costs us latency

The row that proves the table is not cost-driven. n=5 applications:

| | Opus 5 | Sonnet 5 | Haiku 4.5 |
|---|---|---|---|
| Band agreement | 4/5 | **5/5** | **5/5** |
| Judge calibration | 4.20 | **4.80** | 4.40 |
| False negatives | **1** | 0 | 0 |
| Output-contract violations | 5 | 5 | **8** |
| Wall clock p50 | **8.51 s** | 13.54 s | 9.56 s |
| $/call p50 | $0.0293 | $0.0194 | **$0.0059** |

Sonnet 5 is **slower** here — 13.54 s vs 8.51 s p50, slower on **4 of 5** paired
applications. The move is made anyway, because Opus produced the sweep's only **false
negative**: on APP-1014 it declared LOW RISK against a pinned POSSIBLE RISK (SYN-008) and
the judge scored it "dismisses". On a fraud product a missed synthetic identity costs more
than five seconds of median latency. **That is the trade-off, stated explicitly: we spent
latency to buy back a fraud dimension.**

Note also what was *refused*. Haiku 4.5 banded 5/5 at $0.0059 and 9.56 s — cheaper **and**
faster than the Sonnet now assigned. It was rejected on the customer's own output
contract: 8 violations against Sonnet's 5, including citing numeric alert IDs on 3 of 5
calls, which `synthetic_agent_prompt.txt` forbids. A purely cost-driven table would have
taken Haiku.

This is also the one cell where the cheaper model wins with **no bias caveat available**:
the margin runs *against* the judge's own family, so self-preference cannot explain it.

### bustout and identity: swept, and deliberately NOT moved

Both were compared at **n=3 applications**, and a band-agreement claim needs 5 — one
application moves the rate by 33 points. On that thin sample Haiku 4.5 lost on no axis:

| Agent | Incumbent Sonnet 5 | Haiku 4.5 |
|---|---|---|
| bustout | band 1/3, calibration 2.67, 2 false positives, $0.0254, 25.31 s | band 2/3, calibration 2.67, 1 false positive, **$0.0087, 13.65 s** |
| identity | band 2/2, calibration 5.00, 2 contract violations, $0.0171, 13.65 s | band 3/3, calibration 4.00, **7 contract violations**, **$0.0051, 8.04 s** |

Moving either would save roughly $0.017/application and ~11 s of critical path. **We are
not doing it on three applications.** identity's Haiku rows also carry 7 output-contract
violations against Sonnet's 2 — including alert IDs its prompt forbids and a risk label in
a section header on all three calls — so a swap there has a real fidelity cost, not just a
statistical one.

**The recommended next eval:** extend the sweep to 5+ applications on bustout and
identity, and include `income` and `employment`, which have never been swept at all.
`income` deserves it most: its p95/p50 spread (31.44 s / 8.09 s) is the second widest of
the eight.

### What no model choice fixed — read this before the calibration conversation

The customer's calibration bar is *not* met by any tier on two fixtures, and this is the
finding most likely to matter to them:

- **APP-1014 rings**: pinned LOW RISK (RNG-020). **All five models across both families
  declared POSSIBLE RISK — 0 of 5 correct.**
- **APP-1004 bustout**: pinned POSSIBLE RISK (BST-020). **All five models across both
  families over-escalated to HIGH RISK — 0 of 5 correct.**

The judge named the same fault in every case: a second alert firing off **the same
underlying behaviour** treated as independent corroboration. The cross-family sweep
upgrades this from "no *tier* fixes it" to **"no model choice fixes it"** — Claude Opus 5,
Claude Sonnet 5, GPT-5.6 Luna, Terra and Sol all get both wrong, and every one of them gets
them wrong *the same way*. That is the signature of a shared prompt defect, not five
independent mistakes.

**This is the most important thing to tell the customer before Friday**, because it is the
only quality problem in the whole study that no model selection addresses. It is a
prompt-and-guardrail problem against her own stated bar — "LOW RISK should be the most
common outcome", "alert volume alone does NOT indicate risk" — and the fix is a rule that
says *alerts arising from one underlying behaviour count as one signal*. Buying a bigger
model, or switching families, does not buy it.

One more fidelity detail an engineer will check first: **all 24 bustout and rings analyses
on the Claude models invented a title their prompt does not define** (23 of the Claude
set's 31 contract violations). Their prompts say only "Return only the final analysis" — no
title, no length cap, no emoji ban, no alert-number ban. Unlike the two calibration
failures this one **is** model-sensitive — the GPT set is 72/72 clean and invents no titles
— but the fix is still the prompt, which should say what heading it wants.

## The `max_tokens` story: a cap is not a brevity instrument

`max_tokens` was **measured** as a latency fix and it **failed on all three criteria.** 30
live calls in `us-east-1` across bustout and rings at caps 1024/1536/2048/3072/4096, each
against a same-session 4096 control ($0.6278 measured + ~$0.4378 bounded on truncated
calls):

| Criterion | Result |
|---|---|
| (a) Lowers latency? | **No.** Of the 10 cells that returned an analysis at both their cap and the control, 5 were faster and 5 slower. Mean delta **−0.23 s** (min −10.39 s, max +17.84 s). |
| (b) Truncates nothing? | **No.** **14 of 30** cells raised `MaxTokensReachedException`, burning 263.2 s of wall clock for zero analysis. |
| (c) Leaves bands unchanged? | **No.** 13/30 agreed with the control; 3 disagreements were genuine band moves. |

The mechanism, worth stating to the customer: **the model writes what it intends to write.**
The cap either does not bind, or it raises and destroys the whole analysis. APP-1004 rings
proves it — the cap *did* cut output from 2876 to 1980 tokens and the call still took
28.50 s.

**A truncated call is the worst outcome available, not a cheap one.** `strands` raises
rather than returning partial text, so the call spends full wall clock (up to 29.0 s), is
billed for every token it generated, and returns nothing — fan-out degrades to seven of
eight fraud dimensions. *A cap low enough to shorten these agents is a cap low enough to
delete them.*

**The failure boundary is a probability, not a threshold**, which is a stronger reason not
to ship a lower cap than a clean boundary would have been. Both agents truncated **and**
completed at 1536 **and** 2048, depending on the application and the draw. 1024 truncated
6 of 6 cells. 3072 was the lowest cap where every cell completed — one draw each, which is
not enough to call it safe. Recorded in
`agents/models.py:MEASURED_TRUNCATION_BOUNDARY` so nobody tightens it blindly.

### What changed instead: the cap is now a property of the agent, not the model tier

Caps moved from `MAX_TOKENS_BY_TIER` to **`MAX_TOKENS_BY_AGENT`**, with every value at its
agent's own measured ceiling plus headroom:

| Agent | Max output observed | Cap | Headroom | Its prompt says |
|---|---|---|---|---|
| straw | 989 | 3072 | 3.1× | max 4000 chars |
| dealer | 1464 | 3072 | 2.1× | max 4000 chars |
| employment | 1647 | 4096 | 2.5× | max 4000 chars |
| synthetic | 2737 | 4096 | 1.5× | max 4000 chars |
| income | 2783 | 4096 | 1.5× | max 4000 chars |
| rings | 2876 | 4096 | 1.4× | **nothing** |
| bustout | 3257 | 4096 | 1.3× | **nothing** |
| identity | 3582 | 4096 | 1.1× | max 4000 chars |
| synthesizer | 1005 | 4096 | 4.1× | ~1200 chars/summary |

"Max output observed" counts every live call on disk **at the model that agent is now
assigned** — the 42-run benchmark, the tier sweep's Sonnet 5 cells, and the cap sweep. Opus
5 rows are excluded because no agent runs on Opus any more; had they been included,
bustout's figure would read 3385 from an Opus call and would be measuring a model this
assignment does not use. `identity`'s 1.1× is the thinnest margin in the table and is
deliberately *not* tightened: its 3,582-token call carried only 1,191 characters, so it is
the agent whose token count is least predictable from its visible output.

**No effective cap was lowered.** The values reproduce the shipped ones exactly
(`test_per_agent_caps_preserve_the_shipped_effective_values`); what changed is that the cap
no longer moves when the *model* does. Under the old table `rings` drew its cap from its
tier, so this very re-assignment would have silently changed the truncation risk of one of
only two agents ever measured to truncate. That coupling is now gone.

**A character cap and a token cap are not the same constraint**, which is why no cap here
is derived from the customer's 4000-character rule. Measured chars-per-output-token runs
from 4.51 (dealer) down to 1.55 (identity), and identity's worst calls hit 0.33 — **3,582
billed output tokens behind 1,191 characters of analysis**, and those were its slowest
calls. A cap computed as 4000/4 = 1024 would truncate identity at a third of its own
contract, and 1024 truncated every cell in the sweep. The character rule belongs in the
customer's prompt text and in the output-contract tests, not in a token cap.

### The instrument that *does* match the verbosity mechanism

Wall clock is linear in output tokens (r²=0.953 over n=332 calls, excluding two server-side
stalls): `wall_ms = 2980 + 11.34 × output_tokens`. The two agents with **no character cap
in their prompt** write **1331 output tokens p50** against **381** for the six that have
one — 30.1% of their calls exceed 4000 characters against 3.6% for the capped six.

So the lever is the customer's own prompt text: add the "max 4000 characters" sentence
that `bustout` and `rings` are missing. That is a one-line change to two verbatim customer
artifacts, it is **their decision, not ours**, and it is **NOT MEASURED** — nobody has run
it. It is the recommendation, not a result.

## What this costs — PROJECTION, not a measured end-to-end

**The full pipeline has not been re-run under this assignment, so there is no measured
end-to-end cost for it.** What follows is a projection: a sum of nine per-agent costs, each
priced from the token counts **that model actually produced**. Computed by
`agents/models.py:projected_cost_per_adjudication`.

Why it cannot be done any other way: a model swap changes output length. rings on Opus
wrote **1568.8** output tokens mean; on Sonnet, **1862.6** on the same five applications.
Pricing one model's rates against the other's token counts is fiction.

| Role | Model | Recorded tokens (in / out / cacheRead) | n | $/call |
|---|---|---|---|---|
| identity | Sonnet 5 | 3110 / 1105 / 0 | 42 | $0.01727 |
| dealer | Haiku 4.5 | 3518 / 379 / 0 | 42 | $0.00541 |
| straw | Haiku 4.5 | 2400 / 224 / 0 | 42 | $0.00352 |
| employment | Sonnet 5 | 2363 / 527 / 1091 | 42 | $0.01021 |
| income | Sonnet 5 | 3184 / 900 / 0 | 42 | $0.01537 |
| bustout | Sonnet 5 | 1786 / 1316 / 1297 | 42 | $0.01699 |
| **synthetic** | **Sonnet 5** | 3793 / 1518 / 0 | **5** | $0.02276 |
| **rings** | **Sonnet 5** | 1621 / 1863 / 705 | **5** | $0.02259 |
| synthesizer | GPT-5.6 Luna | 2 / 716 / 0 (+4300 cache write) | 42 | $0.01063 |
| **Projected total** | | | **smallest n = 5** | **$0.1247** |

Specialists $0.1141, synthesizer $0.0106.

**Against what was actually measured.** The 42-run benchmark measured **$0.14396 p50 /
$0.15616 mean** per adjudication under the *old* assignment (rings and synthetic on
Opus 5), n=41 priced runs. The projection is a sum of per-agent means, so it is comparable
to the **mean**, not the p50:

| | Value | Status |
|---|---|---|
| Old assignment, measured mean | $0.15616 (n=41) | **MEASURED** |
| New assignment, projected | $0.1247 | **PROJECTION** |
| Delta | **−$0.031 (−19.9%)** | projection vs measurement |

**This method is validated where a measured answer exists.** Applying exactly the same
arithmetic to the old assignment's own recorded tokens reproduces the benchmark's measured
mean to within $0.0005 (`test_the_same_method_reproduces_the_measured_cost_under_the_old_assignment`).
So the *method* is sound; only the *new configuration's* end-to-end result is unconfirmed.

### The saving mostly evaporates on 2026-09-01

Sonnet 5's $2.00/$10.00 is **introductory** and reverts to $3.00/$15.00. Repricing **both
assignments at the same date** — which is the only honest comparison, since the benchmark
ran inside the promo window:

| Priced as of | New assignment | Old assignment | Delta |
|---|---|---|---|
| 2026-07-28 (promo live) | $0.1247 | $0.1557 | **−$0.031 (−19.9%)** |
| 2026-09-01 (promo lapsed) | $0.1688 | $0.1771 | **−$0.008 (−4.7%)** |

Read that before it goes on a TCO slide. The re-assignment is a **latency and quality**
decision that is also cheaper today; from September the cost argument is worth about
4.5%, because the new assignment sits entirely on the promotional rate while the old one
had two rows on non-promotional Opus. A slide quoting −20% past that date would be wrong,
and differencing a post-expiry projection against the *fixed* measured constant manufactures
a fake **cost increase** of ~$0.021 — the trap is asserted against in
`test_the_post_expiry_comparison_reprices_both_sides_not_just_one`.

Also note the two n=5 rows are drawn from **APP-1004/1009/1012/1013/1014**, the harder half
of the fixture set: those same two agents' Opus rows cost 31.1% and 26.6% *more* on these
five than their 14-fixture means did. So the projection for those rows is, if anything,
pessimistic.

### The one run that turns this into a measurement

```
AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.bench --pipeline --repetitions 3
```

42 live adjudications, ~$5 of Bedrock spend at the projected rate. It re-measures
end-to-end p50/p95, per-agent latency, cost per adjudication and verdict stability under
the assignment above. Until it runs, `$0.1247` is a projection and this page says so.

### The projection for the assignment we did NOT take

For completeness, since the hold is the expensive option: moving the four cross-family-swept
agents to GPT-5.6 Luna, priced at **Luna's own measured tokens** from the set comparison,
projects **$0.0651** per adjudication against the held assignment's **$0.1247** — a
**47.8%** reduction. `agents/models.py:projected_cost_if_gpt_specialists` computes it and
labels itself `PROJECTION OF A REJECTED COUNTERFACTUAL`, which is a weaker object than the
projection above: **no benchmark run would confirm it without changing the assignment
first.**

It also *understates* the GPT case, deliberately. The other four agents are priced at
Claude in that figure, because there is no measured GPT token count for them and inventing
one would be exactly the fabrication this page exists to avoid. A full-fan-out GPT figure
would be lower and is **not offered**.

### What latency is claimable, and what is not

**Claimable, paired on the same five applications:** rings on Sonnet 5 ran 22.05 s p50
against Opus's 27.95 s p50 on those same five applications (n=5 vs n=15 calls), and was
faster on 3 of 5 paired draws. synthetic went the other way: 13.54 s against 10.70 s.

**Not claimable:** a new end-to-end p50. Fan-out is bounded by its *slowest* agent per run,
and that is a per-run maximum that cannot be recomputed from per-agent medians. The old
end-to-end was 31.4 s p50 / 46.5 s p95 (n=42, 40 of 42 under 60 s); the new one is
unmeasured until the command above runs.

**Also not fixable by any assignment:** the tail. 61.0% of end-to-end variance is
*within*-application — across repetitions of byte-identical input. The two runs over 60 s
were single calls generating at 16.1 and 4.7 output tok/s against a 66 tok/s median, inside
Bedrock's own reported latency, with zero throttles and `stop_reason=end_turn`. That is
irreducible run-to-run Bedrock variance. For a live demo, prefer the applications with
measured-stable profiles — **APP-1010** (28.0/28.4/28.6 s), **APP-1002** (22.3/22.9/23.2 s),
**APP-1011** (21.2/22.4/22.8 s) — stated with its limit: n=3 per application makes that a
low-variance observation, not a guarantee, and the stall rate is per *call* while every run
makes eight.

**Orchestration is not a lever and should be dropped from the conversation.** `asyncio`
fan-out costs 0.83 s p50 and never more than 2.71 s above its slowest member (n=42).

## What is still unproven

Ranked by how likely a customer engineer is to ask:

1. **The end-to-end cost and latency of this exact assignment.** Projected, not measured.
   One command fixes it (above).
2. **The calibration failures no tier fixed.** APP-1014 rings and APP-1004 bustout are
   anti-false-positive misses shared by all three models. Prompt/guardrail work, unmeasured.
3. **bustout and identity at n≥5.** Haiku lost on no axis at n=3 and is ~3× cheaper. Real
   money is sitting on an eval nobody has run.
4. **income and employment: never swept at all.** No quality evidence in either direction.
5. **dealer and straw have no quality evidence either.** LIGHT rests on n=42 latency/cost
   and their prompts' own scope, not on a judged comparison.
6. **Whether adding the missing character cap to the bustout and rings prompts shortens
   their output.** The recommended verbosity fix, entirely unmeasured, and a change to the
   customer's verbatim artifacts.
7. **Luna's fraud reasoning versus a Claude synthesizer.** 42 runs validated 21 keys every
   time, but no blind judge has scored Luna against Claude across the fixture set. The
   specialist set comparison is mild corroboration that Luna reasons acceptably on this
   customer's fraud material — but it is not a synthesizer result: that prompt is 31,343
   characters against a specialist's ~7,500 and emits a 21-key object rather than prose.
8. **Whether a grounding instruction closes GPT's `_context` gap.** The single experiment
   that would unlock a 47.8% projected cost reduction. Unmeasured.
9. **GPT on dealer, straw, employment and income.** Entirely unmeasured, and three of the
   five anti-false-positive fixtures live there.

## What the customer has to decide

Four things are hers, not ours. Each is surfaced rather than silently resolved.

1. **Two of her eight prompts have no character cap.** `bustout_agent_prompt.txt` and
   `rings_agent_prompt.txt` say only "Return only the final analysis" — no length rule, no
   title, no emoji ban, no alert-number ban, where the other six say "max 4000 characters".
   Measured consequence: those two write **1331 output tokens p50** against **381** for the
   capped six, and **30.1%** of their calls exceed 4000 characters against **3.6%**. They
   are also the only two agents ever measured to truncate, and the source of all 23
   invented titles. Adding the missing sentence is a one-line change to two verbatim
   customer artifacts — **her decision, and NOT MEASURED.** It is the recommendation, not a
   result.
2. **The enum conflict between her two artifacts.** Her process-guide PDF uses a mid-band of
   `POSSIBLE RISK` and a four-value recommendation enum:
   `APPROVE WITH STIPULATIONS / MANUAL REVIEW / APPROVE / DECLINE`.
   Her master prompt file uses `MEDIUM RISK` and three values:
   `APPROVE / REVIEW AND APPLY STIPULATIONS / DECLINE`.
   **The prompt file is the executable artifact and wins in this repo**, because it is what
   the model actually receives — but the two documents disagree and only she can settle
   which is canonical.
   Anything downstream that keys off the band string (a queue router, a dashboard, an
   audit trail) will be wrong for one of the two.
3. **The calibration rule that no model fixes.** APP-1014 rings and APP-1004 bustout, above.
   The fix is a sentence in her prompts stating that alerts arising from one underlying
   behaviour count as one signal. We can draft it; she owns the artifact.
4. **Whether to spend one more eval cycle before committing the specialists.** The
   grounding experiment plus a four-agent sweep extension is what stands between the
   current assignment and a projected 47.8% cost reduction with a ~14 s faster fan-out
   floor.

One model, in one place, was chosen on a cross-family measurement: the synthesizer. The
specialists were *offered* the same measurement and declined it, above.

## Why the synthesizer is not Claude

The synthesizer is the pipeline's bottleneck. It reads all eight specialist narratives
(~28K input tokens) and emits the 21-key adjudication object. Measured on APP-1004,
against the same eight analyses, n=1 each:

| Synthesizer | Wall time | Cost | Result |
|---|---|---|---|
| Claude Sonnet 5 | 55.2 s | $0.11731 | 21/21 keys, HIGH RISK / DECLINE |
| GPT-5.6 Luna | 6.1 s | $0.01488 | 21/21 keys, HIGH RISK / DECLINE |

Identical verdict, ~9x faster, ~87% cheaper on that call. That single substitution is
what brings a measured end-to-end from 101.1 s to **37.5 s** (fan-out 31.4 s + synthesis
6.1 s, $0.245/application, 8/8 specialists, one live run, `us-east-1`) — i.e. the
difference between missing and meeting the sub-minute target.

**Not claimed:** that Luna reasons as well as Sonnet 5 on this task. That is one
application. Reasoning parity is a separate workstream — the calibration judge in
`evals/evaluators/pp_calibration_judge.json` across the full fixture set, on both. Until
that runs, this is a latency and cost result and nothing more. `BEDROCK_MODEL_SYNTHESIZER`
switches back to any Claude id; `agents/synthesize.py` routes on the model id, not a flag.

## Which GPT-5.6 variant, and which region

Measured on the **real 31,343-character synthesis prompt** (the customer's verbatim
master prompt with all eight specialist narratives substituted in, APP-1004), streamed
Responses calls, `effort=low`, **n=3 per cell, 24 calls total, 2026-07-28**.

All 24 calls returned a valid 21-key `Adjudication` and all 24 adjudicated
**HIGH RISK / DECLINE**. The latency spread below is therefore not bought with a shorter
or worse answer.

| Model | Region | p50 total (n=3) | p50 TTFT | p50 tok/s | p50 out tok |
|---|---|---|---|---|---|
| **luna** | **us-east-1** | **4.61 s** | **0.79 s** | 176.0 | 812 |
| luna | us-west-2 | 5.02 s | 1.19 s | 180.0 | 903 |
| luna | us-east-2 | 6.79 s | 1.63 s | 115.8 | 878 |
| terra | us-east-2 | 6.29 s | 0.63 s | 126.4 | 825 |
| terra | us-east-1 | 6.72 s | 0.69 s | 122.3 | 785 |
| terra | us-west-2 | 7.63 s | 0.89 s | 95.1 | 790 |
| sol | us-east-1 | 11.19 s | 4.19 s | 91.1 | 1020 |
| sol | us-east-2 | 30.66 s | 22.07 s | 31.6 | 900 |

Three notes on reading this table honestly:

- **n=3 is a median of three points, not a percentile.** It is enough to separate 4.61 s
  from 30.66 s. It is not enough to defend 4.61 s against 5.02 s as a stable ranking, and
  the code says so — `MIN_TRIALS_FOR_A_RANKING` is 3 and every rendering prints its n.
- **The four p50 columns are independent medians.** `tok/s` is the median of the
  per-trial throughputs, not `out tok / total`. Those coincide exactly in 3 of the 8
  cells and differ by up to 10.5% elsewhere. Kept as measured rather than recomputed.
- **Totals are streamed totals.** Streaming is how TTFT is observed at all;
  `agents/synthesize.py` calls the endpoint non-streamed.

### Paying more buys more latency, not less

$/1M tokens on `bedrock-mantle` (published in-region on-demand; input / cache-read /
output):

| Variant | Input | Cache read | Output | Best measured p50 |
|---|---|---|---|---|
| GPT-5.6 Luna | $0.22 | $0.022 | $1.32 | 4.61 s |
| GPT-5.6 Terra | $2.20 | $0.22 | $13.20 | 6.29 s |
| GPT-5.6 Sol | $5.50 | $0.55 | $33.00 | 11.19 s |

Rates shown are current: Terra and Luna were repriced on 2026-07-30 (Luna cut ~5x from
$1.10/$6.60). Costs for runs measured before that date are computed at the prior card, so
the measured benchmark below still reproduces.

25x the token price is **2.4x slower** on this workload. There is no speed-for-money
trade to make here, and Luna is not the compromise pick — it is both the cheapest and
the fastest. (Reasoning quality is the axis this table says nothing about.)

### Sol's regional gap

Sol is served in **us-east-1 and us-east-2 only — not us-west-2**. A stack deployed in
us-west-2 that asks for Sol gets a confusing 404, so `resolve_mantle_region` replaces an
unavailable region with the measured-best *available* one and says so in the log.
Availability overrides both an explicit argument and the `BEDROCK_MANTLE_REGION` override,
because the alternative is a call that cannot succeed.

Sol in us-east-2 measured **30.66 s p50 with 22.07 s TTFT** — 2.7x its own us-east-1
number and 6.7x Luna's us-east-1 number. That cell alone would blow the sub-minute target
on the synthesis call by itself.

## The region is pinned to the workload, not to `AWS_REGION`

`resolve_mantle_region` deliberately does **not** read `AWS_REGION`. Precedence:

1. explicit `region=` argument
2. `BEDROCK_MANTLE_REGION` (operator override — data residency, a new region, or simply
   disagreeing with the measurement)
3. the measured-fastest region for that model
4. the model's first declared available region (for a variant we never measured)
5. `us-east-1`

...with the availability check applied to 1 and 2. The chosen region and the reason are
logged once per distinct choice, not once per adjudication.

Why `AWS_REGION` is excluded: with `AWS_REGION=us-west-2` — this repo's own dev default —
the previous revision sent every synthesis to us-west-2 at 5.02 s p50 instead of
us-east-1 at 4.61 s p50 (n=3 each), an 8.9% latency tax on the pipeline's bottleneck for
no benefit. A mantle call is in-region only, with no `us.*`/`global.*` prefix and no
cross-region inference profile, so co-locating it with the Converse specialists buys
nothing either.

### THIRD-PARTY corroboration — not our measurement

Customer-supplied third-party dashboard readings on a **~16-32 output-token** prompt. No
sample count, no prompt text, no verdict validation came with them. Recorded in
`agents/mantle.py:THIRD_PARTY_SHORT_PROMPT_PROBE`, labelled as third-party, and they
**never select a region**.

| Model | Region | TTFT | tok/s |
|---|---|---|---|
| luna | us-west-2 | 531 ms | 405 |
| luna | us-east-1 | 666 ms | 220 |
| luna | us-east-2 | 2703 ms | 73 |
| terra | us-west-2 | 796 ms | — |
| terra | us-east-1 | 914 ms | — |
| terra | us-east-2 | 1105 ms | — |
| sol | us-east-1 | 3896 ms | — |
| sol | us-east-2 | 15636 ms | — |

**Where it agrees with ours** — this is what makes it worth recording:

- us-east-2 is the worst region for Luna in both datasets.
- us-east-2 is catastrophic for Sol in both (15636 ms there vs 3896 ms in us-east-1;
  ours: 22.07 s vs 4.19 s TTFT).
- Sol has by far the highest TTFT of the three variants in both.

**Where it diverges, and why that is the actual lesson:**

The short-prompt reading ranks **us-west-2 first** for Luna (531 ms / 405 tok/s vs
us-east-1's 666 ms / 220 tok/s). On our 31,343-character prompt, **us-east-1 wins** on
total latency (4.61 s vs 5.02 s p50) even though us-west-2 edges it on throughput
(180.0 vs 176.0 tok/s).

Both readings are correct about their own workload:

- A ~32-token reply is almost entirely time-to-first-token, so the lowest-TTFT region
  wins. The third-party data puts Luna's lowest TTFT in us-west-2 (531 ms).
- Our reply is ~800-1000 tokens, so ~83% of the wall clock is generation
  ((4.61 − 0.79) / 4.61). Throughput decides the total and TTFT is a smaller additive
  term. On the long prompt Luna's lowest TTFT is in **us-east-1** (0.79 s vs us-west-2's
  1.19 s) — so the two datasets disagree about the TTFT ordering itself, not merely about
  which metric to rank on.

Two precise consequences worth stating, because it is easy to draw the wrong one:

- **Throughput is the axis that actually flips the answer for us.** Ranked by tokens/s,
  Luna's us-west-2 comes first (180.0 vs 176.0) despite the worse total (5.02 s vs
  4.61 s), because it also carries 1.5x the TTFT. That is the axis the short-prompt probe
  ranks on.
- **Ranking by TTFT would coincidentally give the same answer as total on our data** for
  all three variants. So "we rank on total, not TTFT" is a statement of intent, not a
  claim that it changed the outcome — and it is asserted as such in the tests rather than
  overstated.

Ranking on a short probe and then deploying our workload picks the wrong region. That is
why the table in the code is labelled as a measurement of *our* prompt, why every cell
carries its `prompt_chars`, and why a re-measurement on a materially different prompt size
refuses to diff itself against the recorded table (`prompt_comparability`).

## Claude-side facts a cost slide needs

- **`global.*` is the default prefix.** `us.*` and `global.*` address the same weights,
  but `us.*` bills at exactly **1.10x** on every rate category. Pricing a `us.*` call at
  global rates under-reports by 9.09%. `agents/pricing.py` applies the multiplier from the
  prefix carried by the model id. The prefix is env-overridable for data residency.
- **Claude `$/1M` (global, input/output):** Haiku 4.5 $1.00/$5.00; Sonnet 5 $2.00/$10.00
  (**introductory — reverts to $3.00/$15.00 on 2026-09-01**); Sonnet 4.6 $3.00/$15.00
  (the source system's model, and therefore the honest cost baseline); Opus 5 $5.00/$25.00.
- **The tier *shape* saved $0.00 under the old roster, and that arithmetic is why.**
  Haiku→Sonnet is a $2/$10-per-MTok step down and Sonnet→Opus is the identical step up, so
  at equal token counts two LIGHT agents funded exactly two HEAVY agents. With no HEAVY
  agent left the shape is "two cheap, six standard" and is genuinely positive — but the
  saving is now just *not paying Opus rates for output that measured no better*, which is a
  model-choice result, not a property of tiering.
  `agents/pricing.py:compare_tiered_vs_uniform` splits any reported saving into
  `structural_saving_usd` (the shape) and `model_saving_usd` (the generation) from real
  usage, so the two can never be conflated on a slide.
- **Prompt caching does not engage for the specialists.** Their prompts are 547-956
  estimated tokens against minimum cacheable prefixes of 512 (Opus 5), 1024 (Sonnet 5)
  and 4096 (Haiku 4.5), and the large part of each request — the per-application payload —
  differs on every application, so a cache entry is never reused. It is left enabled
  because a miss is free, and it is not counted as a cost lever.
- **GPT-5.6 cache-WRITE tokens bill at 1.25x input.** Dropping that term under-reported a
  measured Luna call by 62%. GPT-5.4/5.5 cache automatically with no write charge.

## Keeping this page honest

The recorded matrix is re-derivable, not just assertable:

```
python3 -m agents.mantle                    # print the recorded table; spends nothing

AGENTCORE_MANTLE_ALLOW_MEASURE=1 \
  python3 -m agents.mantle --measure --dry-run   # call plan + spend upper bound, then stop

AGENTCORE_MANTLE_ALLOW_MEASURE=1 \
  python3 -m agents.mantle --measure             # re-run the matrix, diff vs the table
```

Re-measuring is double-gated on the flag **and** the env var, because it is 24 billed
inference calls on a 31K-character prompt. `--dry-run` prints an explicit upper bound
whose basis is stated (input estimated at chars/4 — the mantle endpoint has no
`CountTokens` API — priced at the 1.25x write rate, output priced at the full token cap
that no measured call reached).

The diff reports each cell as `unchanged` / `faster` / `slower` / `new` / `failed` /
`missing`, and separately reports whether the **ranking** moved. A changed ranking means
the default region in `agents/mantle.py` is stale; the fix is to update
`MEASURED_SYNTHESIS_LATENCY` with the new n and date, not to paper over it with
`BEDROCK_MANTLE_REGION`. Past 90 days the renderer warns that the table is stale.

Latency and cost that a customer sees come from `evals/bench.py`, which refuses
percentiles below a documented sample count and stamps every report with git sha, region
and model ids. The matrix in `agents/mantle.py` is a routing *input* — which region
answers the synthesizer fastest — and publishes nothing about an application's processing
time.

## What this page does not claim

- That Luna's reasoning matches Claude Sonnet 5 on fraud adjudication. Separate
  workstream; the calibration judge has not been run across the fixture set on both. The
  24 matrix calls agreeing with each other is a determinism observation, not an accuracy
  one.
- That the variant or region ranking holds for any other prompt size. It is a property of
  a ~31K-character prompt with an ~800-1000 token reply.
- Any p95 or p99 anywhere. n=3 per cell does not support one.
- That the 37.5 s end-to-end is a benchmark. It is one live run on one application, which
  shows the target is reachable and is not a distribution.
- Any figure for GPT-5.5 or GPT-5.4. They are priced and region-mapped in the code but
  were never measured on this prompt, so they carry no latency cell at all.
- **That GPT-5.6 is worse at the fraud judgement.** It is not what the set comparison
  found. On band agreement the two families are within one case of each other on every
  swept agent except identity/Sol, and one case is 17 points at n=6. The specialists are
  held on a measured `_context` grounding gap and on half the fan-out being unswept — not
  on a finding that GPT reasons less well.
- **Any p95 from the set comparison.** 6 applications per cell supports a p50 and a band
  rate; `evals.bench.summarize_values` refuses a p95 below 20 samples and the report
  records the refusal rather than a number.
- **That the four unswept agents would behave like the four swept ones on GPT.** No
  evidence either way exists for `dealer`, `straw`, `employment` or `income`, and the three
  anti-false-positive fixtures that target them were never run on GPT.
