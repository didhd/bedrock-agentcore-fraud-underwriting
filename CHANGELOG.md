# Changelog

## 2026-07-28 — Cross-family set comparison: the specialists stay on Claude, and the hold has a price

The master synthesizer already runs on GPT-5.6 Luna on a measurement. Nobody had tested
whether the eight **specialists** should follow it. A first latency probe said GPT was
dramatically better on both latency and cost. It is. **They are held on Claude anyway.**

**What was measured.** `evals/results/set_comparison.json` — 4 agents (rings, synthetic,
bustout, identity) x 6 applications x 5 models (Claude Opus 5, Sonnet 5; GPT-5.6 Luna,
Terra, Sol) = **120 blind-judged analyses**, identifiers stripped, order shuffled.
**$16.8869 of measured spend**, $14.1343 of it judging. Scored by **two judges biased in
opposite directions** — Claude Opus 5 (*also a candidate*) and GPT-5.5 — because the prior
tier study measured a single judge preferring its own family by +0.31 calibration / +0.78
substance while the band margin it could not influence moved −0.026.

**The case for GPT, none of it disputed:**

| Axis | Claude | GPT |
|---|---|---|
| Wall clock p50, pooled | 18.88 s | **4.73 s** (rings: 3.20 s vs Opus 5's 26.22 s) |
| $/call p50, pooled | $0.03125 | **$0.01066** (rings: $0.0050 vs $0.0583) |
| Output-contract clean | 17/48 | **72/72** |
| Judged substance, pooled | — | **+0.398 / +0.684 — better on BOTH judges** |

The Claude judge scored GPT higher on substance (−0.398 **against its own family**), which
is much harder to dismiss than a single-judge win. And **the length gap is padding, not lost
evidence**: paired on identical payloads Opus writes 2.83x Luna's characters on rings while
naming 1.00x the payload's signals, same band in 23 of 24 pairs. The intuitive objection —
"GPT writes a fifth of the words and this is fraud detection" — does not survive pairing.

**What decided it: `_context` grounding.** Every specialist prompt requires signals to be
validated against the paired `*_context` columns. GPT grounds **0.357** of the signals in
its view against Claude's **0.642** pooled, and is lower on **4 of 4** agents against both
Claude incumbents — deterministic, no judge involved, and invisible in the band, the latency
and the cost. On synthetic it collapses to 0.150 against Opus's 0.517.

**Plus coverage.** 4 of 8 agents swept; dealer, straw, employment and income never ran on
GPT, and **APP-1006/1007/1008 — three of the five anti-false-positive fixtures — target
exactly those three.** The study contains a demonstration of why that matters: GPT-5.6 Sol
on identity escalated **both** anti-FP fixtures, dismissed a corroborated application, and
banded 2/6 — at 4x Luna's price. Paying more did not buy calibration.

**The hold is not free, and the page says so.** Moving the four swept agents to Luna
projects **$0.0651/adjudication against $0.1247 — a 47.8% reduction** — and would drop the
fan-out floor lower bound from 22.05 s to 8.09 s. Both are PROJECTIONS from measured
per-agent tokens (`projected_cost_if_gpt_specialists`, labelled `PROJECTION OF A REJECTED
COUNTERFACTUAL`); `python -m evals.bench --pipeline` is what would confirm either.

**Luna is graded "as good" on 3 of 4 agents at n=6 and still not taken.** That is the honest
shape of it: the per-agent evidence would support moving rings, bustout and identity today.
It is declined because the grounding gap is real on all three, and because a partial
migration would leave `income` — which has **no** quality evidence — as the binding
fan-out agent.

**What no model choice fixes, now a stronger claim than before.** APP-1014 rings (pinned
LOW) and APP-1004 bustout (pinned POSSIBLE) are wrong on **all five models across both
families, 0 of 5 correct each**, every one of them wrong the same way: alerts arising from
one underlying behaviour treated as independent corroboration. That upgrades "no tier fixes
it" to **"no model choice fixes it"** — it is a prompt defect, and it is the most important
thing to raise with the customer before Friday. The 23 invented titles on bustout/rings
*are* model-sensitive (GPT invents none), but the fix is still the prompt.

### Changed

- **`agents/models.py`** — `SPECIALIST_MODELS` unchanged in value and no longer unexamined:
  new `CROSS_FAMILY_COMPARISON` (the decision, the rejected option's numbers, the disclosed
  judge conflict quantified for both judges, and the price of holding),
  `CROSS_FAMILY_SWEPT_AGENTS`, `KNOWN_FAILURES_NO_MODEL_CHOICE_FIXES`,
  `RECORDED_USAGE_PER_CALL_GPT_ALTERNATIVE` and `projected_cost_if_gpt_specialists`. All
  four swept agents' `TIER_RATIONALE` entries cite the study, its n, and where its evidence
  runs out; all four unswept ones state that they were **not** swept and name the anti-FP
  fixture that was therefore never run.
- **`docs/model-selection.md`** — leads with the cross-family question, separates
  "Claude-tier swept" from "GPT-swept" in the assignment table, and adds
  **What the customer has to decide**: the two prompts with no character cap, the enum
  conflict between her process guide (`POSSIBLE RISK`, four values) and her master prompt
  file (`MEDIUM RISK`, three values), the calibration rule, and whether to fund one more
  eval cycle.
- **`tests/test_models.py`, `tests/test_pricing.py`** — 12 new guards, including one that
  re-reads the study's shape from the artifact. That test exists because a draft of this
  work claimed "130 analyses over 8 domains, judged by GPT-5.6 Sol, band 23/26 per model,
  12.3 s end-to-end at $0.0508" — **none of which appears in any artifact on disk.** The
  grounding margin, the contract counts, the per-agent figures and both judges'
  self-preference numbers are now all re-derived from the 120 rows rather than trusted.

---

## 2026-07-27 — 1:1 fidelity pass, real measurement, and a sub-minute adjudication

The goal of this pass was to make the repo a **1:1 replacement** of AnyCompany's
production agents rather than a demo that resembles them, and to replace every asserted
number with a measured one.

**Headline: a live adjudication now completes end to end in 37.5 s**, against the
customer's sub-minute target. Measured on Amazon Bedrock, fixture APP-1004.

---

### The three things that would have lost the technical review

**1. The nine agent prompts were paraphrased, not the customer's.**
`agents/prompts.py` hand-rewrote all nine and invented a `RISK:` / `FINDINGS:` output
format. Its own docstring conceded it: *"preserved verbatim in intent."* The customer's
engineer wrote those prompts and would have diffed them.

Worse, the parser that read the invented format defaulted to `LOW RISK` whenever it
could not find a `RISK:` line — so feeding it the real prompts would have cleared
every application silently.

Now: `prompts/verbatim/` holds byte-identical copies, sha256-verified at import, with
`tests/test_prompt_fidelity.py` diffing them against source. Five mutation attempts
(including regenerating the manifest to cover a paraphrase) were all caught.

**2. The per-agent model tiering saved exactly $0.00.**
Haiku→Sonnet is a $2/$10-per-MTok step down; Sonnet→Opus is the identical step up. Two
light agents fund exactly two heavy agents at equal token counts. The README presented
this as "the Bedrock differentiator." A test now asserts the arithmetic so the claim
cannot come back.

**3. The performance headline was fabricated.**
"26 s sequential → 8.5 s parallel" was computed from a hand-written `NOMINAL_LATENCY_S`
dict, and the "measured wall-clock" printed beside it was `time.sleep(nominal * 0.05)`.
Per-agent cost came from hard-coded `NOMINAL_TOKENS`. All of it is deleted;
`evals/bench.py` is now the only code allowed to publish latency or cost.

---

### Measured results (live Bedrock, APP-1004)

| | Measured |
|---|---|
| End to end | **37.5 s** (target: under 60 s) |
| Fan-out | 31.4 s, vs 157.6 s if run sequentially — **5.0x** |
| Master synthesis | **6.1 s** on GPT-5.6 Luna (was 55 s on Claude Sonnet 5) |
| Cost per application | **$0.245**, from real token usage |
| Contract | 21 / 21 keys, exact order, validated |
| Specialists | 8 / 8 returned |

`media/demo.mp4` is a recording of that run.

---

### Added

- **`prompts/`** — byte-identical customer prompts + orchestration docs, sha256-guarded
  (`PromptIntegrityError` on any drift). Exposes `SPECIALIST_PROMPTS`,
  `MASTER_SYNTHESIS_PROMPT`, `FRANCIS_ORCHESTRATION_PROMPT`, `AGENT_NAMES`.
- **`signal_layer/`** — the governed boundary. `registry.py` (60 signals, owner domains,
  the customer's verbatim bands), `schema.py` (`SignalPayload` / `DomainView` /
  `render_for_agent`, which projects each specialist down to **only its own** signals),
  `alert_categories.py` (the ten categories, 166 alert ids, 18 multi-category ids),
  `bands.py` (fraud-score and dealer-score bands, PRODUCT decode, credit validity).
- **`signal_layer/rules/`** — the customer's numbered General Rules as executable, tested
  code. There are **29** rules (she numbers 0.5 and 1.5 and never renumbered):
  **27 enforced in code, 2 prompt-enforced.** Rule text is extracted from the verbatim
  docs, never retyped. `docs/general_rules_coverage.md` is generated from the catalog.
- **`agents/fanout.py`** — `asyncio.gather` over eight fresh Strands `Agent` instances,
  per-agent real usage capture, graceful degradation (7-of-8 still adjudicates).
- **`agents/contracts.py`** — `Adjudication`: exactly 21 required fields in the
  customer's order, with her cross-field rules as validators.
- **`agents/synthesize.py`** — synthesis by substituting into the verbatim master
  prompt's own placeholders, with `structured_output_model` and one bounded repair.
- **`agents/mantle.py`** — GPT-5.6 (Sol / Terra / Luna) on the `bedrock-mantle`
  endpoint. This is what got synthesis from 55 s to 6.1 s.
- **`app/underwriter/main.py`, `app/francis/main.py`** — AgentCore Runtime entrypoints
  (streaming async-generator; the interactive path is Francis with the 8 agents as tools
  and her 2-tool cap).
- **`fixtures/`** — 14 applications, every registry signal populated, paired
  `<signal>_context`, and `alerts_fired` with finding text. **Nine are anti-false-positive
  tests** (dealer score 940 with no coordination, employer with all-null contact fields,
  military-base employer, 240-unit apartment, alert-volume-heavy, …) because "reduce
  false positives" is the customer's actual quality goal.
- **`evals/`** — `bench.py` (p50/p95/p99, real tokens, cache-eligibility reporting, and a
  refusal to print percentiles below a minimum sample count), a golden dataset in the
  CLI's own schema, and three evaluators including a calibration judge built from her
  verbatim CALIBRATION text.
- **`ui/`** — the customer-facing demo rebuilt on Vite + React + Tailwind + shadcn/ui
  (15 vendored Radix primitives). Live SSE, per-agent cards, the 21-key panel, the
  signal-layer view, and a cost panel. Unmeasured fields render an em dash.
- **`agentcore/`** — a real project for the current CLI (two runtimes: `underwriter`,
  `francis`); `agentcore validate` passes.
- **`media/`** — the recorded live run (mp4, gif, poster).
- **750 offline tests**, up from 9.

### Changed

- `agents/models.py` — `global.*` prefix by default (**exactly 9.09% cheaper** than
  `us.*`, identical API), current-generation 5.x ids, shared bedrock-runtime client with a
  raised connection pool, explicit `read_timeout`, tuned retry `[1,2,4,8]s`, and a
  minimum-cacheable-prefix guard that reports the truth per agent.
- `agents/pricing.py` — real rate card with cache-read / cache-write / batch rates.
  `cost_usd` now takes the whole usage dict, because Bedrock's `inputTokens` counts only
  **non-cached** tokens.
- `deploy/` — `deploy.md` rewritten for the CLI that actually exists; `iam_policy.json`
  now permits `global.*` profiles (it previously forbade the cheapest configuration) and
  splits runtime-execution from deploying-principal permissions; `ci.sh` fails the build
  if `agentcore configure`/`launch` ever reappear.
- `requirements.txt` — pinned to the versions actually installed; removed the deprecated
  `bedrock-agentcore-starter-toolkit`, whose binary collides with the npm CLI.
- `README.md` — every fabricated figure removed; cost and latency claims now carry their
  caveats. `CLAUDE.md` added for engineering context.

### Removed

- `agents/prompts.py` (the paraphrases), `agents/orchestrator.py` and
  `agents/specialists.py` (built on them), and `tests/test_local.py` — which asserted the
  vote-counting synthesis the customer's own MSTR-022/023/031 rules forbid, i.e. the test
  suite was encoding a spec violation.
- `NOMINAL_LATENCY_S`, `NOMINAL_TOKENS`, `SIM_LATENCY_SCALE`.
- The UI's "AgentCore vs Snowflake" comparison bar. The string "Snowflake" no longer
  appears anywhere in the UI: their 48-hour figure is their own reported number, and this
  demo does not measure their platform, so there is nothing to compare against.

---

### Things learned the hard way (all verified, details in CLAUDE.md)

- **`agentcore configure` and `agentcore launch` do not exist** in the current CLI. The npm
  CLI rejects them and exits 1; the deprecated pip starter toolkit installs a *different*
  binary of the same name on which they parse and no-op, so a CI script can look successful
  while deploying nothing. Which binary is first on `PATH` decides.
- **A Strands `Agent` cannot be invoked concurrently** (`ConcurrencyException`). Build a
  fresh one per invocation. `BedrockModel` also defaults to `max_pool_connections=10`,
  which throttles an eight-way fan-out.
- **`@app.entrypoint`'s second parameter must be literally named `context`** or it is not
  injected and every request raises `TypeError`.
- **Prompt caching does not engage here.** The prompts are 547–956 tokens against minimums
  of 512 (Opus 5) / 1024 (Sonnet) / 4096 (Haiku), and the large part of the request — the
  projected payload — differs per application, so a cache entry can never be reused. It is
  left on (free when it misses) and is **not** counted as a cost lever.
- **GPT-5.6 is not on Bedrock Converse.** It is only on
  `https://bedrock-mantle.{region}.api.aws/openai/v1` — note `.api.aws`, not
  `.amazonaws.com`, which does not resolve and looks exactly like an auth failure.
- **Cache-write tokens must be priced.** A measured Luna synthesis reported
  `cacheWriteInputTokens=6740`; dropping that term under-reported the call by 62%.
  Now priced at 1.25x input, with a regression test.
- **`us.*` inference profiles cost exactly 1.10x `global.*`** for identical behavior.
- **Latency does not track price**: Sonnet 4.6 (4481 ms) measured *slower* than Opus 4.8
  (2990 ms) on the same prompt.
- **Raising `max_tokens` is not free.** At 8192 the specialists wrote 3,000–6,600
  characters — breaching the customer's own 4000-character rule — and pushed a run to
  105 s. At 2048 a specialist failed outright with `MaxTokensReachedException`. The cap
  belongs above what the model writes; the contract belongs in the prompt and the tests.
- **The customer's own docs disagree** on the enums: `master_agent_prompt.txt` says
  `MEDIUM RISK` with three recommendations; the process-guide PDF says `POSSIBLE RISK`
  with four (adding `MANUAL REVIEW`). The prompt file is the executable artifact and wins;
  the conflict is surfaced in the UI and in CLAUDE.md rather than silently resolved.

### Still open

Tracked in **CLAUDE.md**: retiring `tools/cortex_analyst_mcp.py` (it calls a
`cortex_analyst_query` tool that does not exist as written), a bench sweep across all 14
fixtures for a real p50/p95, and moving `dealer` off Haiku, where its prompt can never
reach the cache minimum.
