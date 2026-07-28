# Changelog

## 2026-07-27 — 1:1 fidelity pass, real measurement, and a sub-minute adjudication

The goal of this pass was to make the repo a **1:1 replacement** of Point Predictive's
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

- **`agentcore configure` and `agentcore launch` do not exist** in the current CLI. They
  print root help and **exit 0** — a CI script calling them looks successful while
  deploying nothing.
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
