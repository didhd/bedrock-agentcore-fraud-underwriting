# CLAUDE.md — working notes for this repo

Engineering context for anyone (human or agent) picking this up. Read this before
changing anything: several "obvious" improvements here are traps that were already
measured and rejected.

## What this repo is

A 1:1 replacement, on Amazon Bedrock AgentCore, of Point Predictive's agentic
auto-loan fraud underwriting platform currently running on Snowflake Intelligence
(Cortex Agents): **8 specialist fraud agents running in parallel + 1 master
synthesizer**. The customer's engineer wrote every one of those agents, so
fidelity to their prompts and output contracts is the product, not a detail.

Customer targets: forensic underwriting **< 1 minute per application** (today:
4,500 applications took > 48 hours), and a materially lower cost than their
projected $700K–$1M ARR Snowflake AI spend. Data already lives in AWS (Aurora
PostgreSQL, ~10 GB, 3.4M reads/day).

## Ground rules

1. **Never fabricate a number.** No nominal latency tables, no illustrative token
   counts, no invented benchmarks, no unattributed "vs Snowflake" comparisons.
   Numbers come from `evals/bench.py` or they do not appear. The previous version
   of this repo violated this in four places and it was the single biggest
   liability (see "What was wrong before").
2. **Never paraphrase a customer prompt.** `prompts/verbatim/` holds byte-identical
   copies of the customer's files; `prompts/loader.py` sha256-verifies them at
   import and raises `PromptIntegrityError` on drift. `tests/test_prompt_fidelity.py`
   additionally diffs them against `docs/`, so regenerating the manifest is not a
   bypass.
3. **Agents interpret; they never query.** Signal generation is a separate layer
   (`signal_layer/`). Each specialist receives only its own domain's view. This
   mirrors what the customer found worked best, and it is what keeps Cortex
   Analyst's ~27s text-to-SQL latency out of the per-application hot path.
4. Framework is **Strands Agents SDK** (`strands-agents==1.26.0`) throughout,
   hosted on **AgentCore Runtime** (`bedrock-agentcore==1.14.0`).

## Architecture

```
signal_layer/            precomputed governed signals + the customer's General Rules
  registry.py            SIGNAL_SPEC: every signal, owner domain(s), verbatim thresholds
  schema.py              SignalPayload / DomainView / AlertFinding / render_for_agent
  alert_categories.py    the ten alert-ID categories (166 IDs), category_of, alerts_for_domain
  bands.py               fraud-score bands, dealer-score bands, PRODUCT decode, credit validity
  rules/                 one module per numbered General Rule + catalog.py coverage table

prompts/
  verbatim/              byte-identical customer prompts (9) + orchestration docs (9)
  loader.py              sha256-guarded loading; SPECIALIST_PROMPTS, MASTER_SYNTHESIS_PROMPT,
                         FRANCIS_ORCHESTRATION_PROMPT, ORCHESTRATION_NOTES, AGENT_NAMES

agents/
  models.py              per-agent model tiering, shared bedrock client, cache guards
  pricing.py             real rate card (global vs us. prefix), cache + batch rates
  fanout.py              asyncio.gather over 8 fresh Agents, per-agent real usage capture
  contracts.py           Adjudication: exactly 21 required fields, exact customer order
  synthesize.py          structured_output_model synthesis off the verbatim master prompt

app/
  underwriter/main.py    AgentCore Runtime entrypoint — batch fan-out + synthesis (SSE)
  francis/main.py        AgentCore Runtime entrypoint — interactive router, 8 agents-as-tools

fixtures/                applications with every registry signal, paired _context, alerts_fired
evals/bench.py           the ONLY code allowed to publish latency or cost numbers
ui/                      Vite + React + Tailwind + shadcn/ui customer-facing demo
agentcore/               agentcore.json + aws-targets.json for the real CLI
```

## The output contracts (exact — do not "improve" these)

- Specialist risk bands: `LOW RISK | POSSIBLE RISK | HIGH RISK`.
- Specialist output is **titled prose**, not key/value. Exact titles:
  "Identity Fraud Risk Analysis", "Income Fraud Risk Analysis",
  "Employment Fraud Risk Analysis", "Dealer Fraud Risk Analysis",
  "Straw Borrower Fraud Risk Analysis", "Synthetic Identity Fraud Risk Analysis".
- **bustout and rings define NO title** ("Return only the final analysis").
  Do not invent one; a test enforces this.
- Length: LOW = 3–5 sentences; POSSIBLE/HIGH ≤ 4000 chars; straw with no
  co-borrower = 2–3 sentences.
- Master adjudication = **exactly 21 keys in the customer's order**, starting at
  `master_summary`. There is **no `application_id` key** — it travels in the
  transport envelope. `overall_risk_decision ∈ {LOW RISK, MEDIUM RISK, HIGH RISK}`,
  `recommendation_decision ∈ {APPROVE, REVIEW AND APPLY STIPULATIONS, DECLINE}`,
  flags are `int 0|1`, summaries < 1200 chars, `LOW RISK ⇒ top_risk_factors == ""`.
- Master synthesis input order is `IDENTITY, DEALER, STRAW, EMPLOYMENT, INCOME,
  SYNTHETIC, BUSTOUT, RINGS` (not alphabetical) — `prompts.loader.DOMAINS`.
- Francis (interactive): must never identify as Claude, deflects RETRO questions
  to RETRO_FRANCIS, **maximum 2 tools per query**, response shaped as
  `## EXECUTIVE SUMMARY` / `## ANALYSIS` / conditional `###RECOMMENDATIONS`.

## Findings that change decisions

### Prompt caching does not engage on the system prompt alone — measured
The customer's specialist prompts are 547–956 estimated tokens. Verified minimum
cacheable prefixes: **Opus 5 = 512, Sonnet 5 / Sonnet 4.6 / Opus 4.8 = 1024,
Haiku 4.5 = 4096**. So only the two Opus-tier agents (synthetic, rings) cache at
all; the other six accept the `cachePoint` silently, report
`cacheWriteInputTokens=0`, and pay full price. `agents.models.cache_prefix_report()`
reports this truthfully at runtime. To actually benefit, the cache point must sit
after **system prompt + the stable prefix of the signal payload**, and
`render_for_agent` must be deterministic for that prefix to be identical across
applications. Also note: `dealer` sits on Haiku and does not reach 4096 even with
its orchestration notes prepended.

### Per-agent model tiering saves exactly $0.00 structurally — arithmetic, asserted by tests
Haiku→Sonnet is a $2/$10-per-MTok step down; Sonnet→Opus is the identical
$2/$10 step up. Two LIGHT agents fund exactly two HEAVY agents at equal token
counts. The measured 20.13% saving is **entirely** Claude Sonnet 5's introductory
$2/$10 rate, which **reverts to $3/$15 on 2026-09-01** — after which the saving
is 0.00%. Under the customer's own brevity rules (straw "2-3 sentences") the
structural component is slightly negative. Any deck claiming tiering as "the
Bedrock differentiator" is wrong; say "right-sized models plus the levers below".

### Defensible cost levers (verified)
- `global.*` instead of `us.*` inference profiles: exactly **−9.09%** (identical API).
- Batch inference **−50%**, but only for Opus 5 / Sonnet 4.6 / Haiku 4.5 — **not**
  Opus 4.8 / Sonnet 5 / Opus 4.7 / Fable 5 — and mutually exclusive with caching.
- Eliminating per-application Cortex Analyst calls: 8 Analyst messages + 8
  warehouse executions per application disappear when signals are precomputed.
  This is often more persuasive to their leadership than latency.

### Latency does not track price
Measured on a real fraud prompt (maxTokens=120, us-east-1): Haiku 4.5 1848ms,
**Sonnet 4.6 4481ms**, **Opus 4.8 2990ms**, Sonnet 5 @effort=low 2393ms, Opus 5
@effort=low 2924ms. Sonnet 4.6 is *slower* than Opus 4.8. `effort` is set via
`additional_request_fields={"output_config": {"effort": "low"}}`.

### The Cortex Analyst MCP integration in the old repo was fictional
`cortex_analyst_query(semantic_view=..., question=...)` does not exist as written.
Snowflake's managed MCP server has **user-defined** tool names (the fixed part is
`type: "CORTEX_ANALYST_MESSAGE"`), takes a single `message` argument, and binds
the semantic view server-side. Cortex Analyst `/message` **returns generated SQL,
not rows** — a second call is needed to execute it. SigV4 outbound auth to
Snowflake is impossible (only AgentCore Gateway/Runtime, API Gateway, Lambda
Function URLs verify SigV4). Snowflake's own official AgentCore guide uses an
**OpenAPI** Gateway target with a PAT as an API-key header, not an MCP target.
Recommended path: a **Lambda Gateway target** with a `SIGNAL_MODE` flag
(`cortex` | `aurora`), so live-Cortex and precomputed-Aurora are one config change.

### AgentCore CLI: the old commands do not exist, and which binary you have decides how badly
`agentcore configure` and `agentcore launch` **do not exist** in
`@aws/agentcore 1.0.0-preview.22`. Re-verified on 2026-07-28: the npm CLI prints
`error: unknown command 'configure'` plus root help and **exits 1** — the safe
failure, which stops a `set -e` script. The silent-success failure comes from the
**deprecated pip `bedrock-agentcore-starter-toolkit`**, which installs a
*different* binary also named `agentcore` on which those subcommands parse and
then no-op. Whichever resolves first on `PATH` decides. So the danger is not the
exit code, it is the **ambiguity**: `deploy/ci.sh` step 0 both greps for the two
strings and fails if the pip toolkit is installed at all.

(An earlier revision of this file said the npm CLI itself exits 0. It does not;
that was the pip binary's behaviour attributed to the wrong program.
`deploy/ci.sh:19` had it right.) Real flow: `create` / `add agent` /
`(cd agentcore/cdk && npm install)` / `validate` / `deploy --dry-run` / `deploy`.
`deploy` has no `--region/--profile/--agent`; region+account come from
`agentcore/aws-targets.json`. `agentcore/.cli/deployed-state.json` must be
committed. Renaming a resource changes its CFN logical ID and **destroys and
recreates** it. `agentcore import` is **not** local — it publishes CDK assets to
S3 before it can fail.

### Strands 1.26.0 gotchas that bit us
- An `Agent` instance **cannot be invoked concurrently** (`ConcurrencyException`).
  Build a fresh `Agent` per invocation; never module-level.
- `asyncio.gather` is the right primitive, not `ThreadPoolExecutor`. Do **not** use
  `strands.multiagent.GraphBuilder`: it forbids `session_manager` on nodes, forbids
  shared `Agent` instances, is fail-fast on any node error (we need 7-of-8 graceful
  degradation), stringifies typed results between nodes, and zeroes
  `GraphResult.execution_time`.
- `BedrockModel` defaults to `max_pool_connections=10` — a hard ceiling for an
  8-way fan-out. Supplying `boto_client_config` **silently drops** strands'
  `read_timeout=120` back to botocore's 60, so set it explicitly.
- Unknown `BedrockConfig` keys are accepted with only a `UserWarning`. Run CI with
  `-W error::UserWarning` or typos never take effect.
- Structured output is the `structured_output_model=` kwarg;
  `Agent.structured_output()` is deprecated. **Avoid `Optional` fields** — they
  drop out of the generated `required` array.
- `result.metrics.accumulated_usage` is **cumulative** on a reused Agent, and
  `cacheReadInputTokens` / `cacheWriteInputTokens` are **optional keys** (use
  `.get()`). Bedrock's `inputTokens` counts **only non-cached** tokens.
- Correct retry tuning: `ModelRetryStrategy(max_attempts=4, initial_delay=1,
  max_delay=8)` → `[1,2,4,8]s`. The SDK default is `[4,8,16,32,64,128]s`; one
  throttle costs 4s against a <60s SLA. Import path is `from strands.agent import
  ModelRetryStrategy`.
- Set `callback_handler=None` (the default prints every token to stdout) and
  `conversation_manager=NullConversationManager()` for stateless calls.

### AgentCore Runtime gotchas
- `@app.entrypoint`'s second parameter must be **literally named `context`** or it
  is not injected and the call raises `TypeError`.
- An **async-generator** entrypoint returns SSE automatically, each yielded object
  serialized as `data: <json>\n\n`. Never hand-format SSE.
- Exceptions raised mid-stream become a terminal `data: {"error": ...}` frame
  **after HTTP 200** — clients must inspect every frame for `error`.
- Synchronous request timeout is **15 minutes, not adjustable**; streaming gets 60.
- Only **one** entrypoint per app; a second `@app.entrypoint` silently overwrites.
- `runtimeSessionId` has a client-side **33-character minimum** (`uuid4().hex` is
  32 — use `str(uuid.uuid4())`).
- The SDK emits **no** token/cost/latency span attributes, and Strands' OTel token
  histograms carry no attributes. Per-agent spend telemetry is our code's job.
- AgentCore Memory does **not** work under `agentcore dev`.

### A first real deploy hit four defects, and three of them succeed silently
Verified 2026-08-11 deploying to us-west-2 (stack `AgentCore-ppFraud-default`).
Listed in the order they surfaced, because each one masked the next:

1. **The shared packages are not in the zip.** `agentcore package` roots the archive
   at `codeLocation`, so `prompts/`, `signal_layer/`, `agents/` are absent and the
   container dies on `ModuleNotFoundError: No module named 'app'`. It surfaces as
   *"Runtime initialization time exceeded... 30s"*, which points at a timeout, not a
   missing module — read CloudWatch, not the CLI error. `deploy/vendor.sh` fixes it
   and must be run before **every** deploy.
2. **`MOCK_MODE` defaults to mock.** `app.runtime_support.mock_mode()` is
   `os.environ.get("MOCK_MODE", "1") == "1"`, so a runtime with no env block runs
   entirely offline. It returns HTTP 200 with a complete, plausible SSE stream and
   calls Bedrock zero times. The tell is in the data, not the status: the `start`
   frame carries `"mock_mode": true`, and `underwriting_fanout` completes in
   **0.11s**. `agentcore.json` now sets `MOCK_MODE=0` on both runtimes.
3. **Each runtime's `pyproject.toml` is a second dependency list that drifts.**
   Both still pinned `strands-agents==1.26.0` / `bedrock-agentcore==1.14.0` long
   after `requirements.txt` moved to 1.50.2 / 1.19.0 — and
   `strands.models.openai_responses` does not exist in 1.26.x, so the GPT-5.6
   synthesizer could not be constructed at all. The pin is now
   `strands-agents[openai]==1.50.2`; the extra is what pulls `openai` and
   `aws-bedrock-token-generator`. (`aws-bedrock-token-generator` was missing from
   `requirements.txt` too, and every local run still worked because it happened to
   be installed in the dev environment.)
4. **The CLI-generated execution role cannot reach GPT-5.6.** It grants
   `bedrock:InvokeModel` on `foundation-model/*` + `inference-profile/*`, which
   covers the eight Claude specialists and nothing else. Mantle is a separate IAM
   namespace needing **two** actions with **different** resources:
   `bedrock-mantle:CreateInference` on
   `arn:aws:bedrock-mantle:*:<account>:project/*` and
   `bedrock-mantle:CallWithBearerToken` on `*`. Granting one only reveals the other,
   so the first fix looks complete and is not. This fails at the *last* step, after
   all eight specialists have been paid for, and there is no degradation path
   because the adjudication is the product. Both statements are added in
   `agentcore/cdk/lib/cdk-stack.ts` — via CDK deliberately, since an out-of-band
   `put-role-policy` is drift that the customer's own `agentcore deploy` would not
   reproduce.

`agentcore/cdk/` is checked in and the CLI does **not** regenerate it (verified: four
deploys left `git status` clean there), so editing the stack is a supported extension
point. Run `npm run build` in `agentcore/cdk` after editing — `agentcore deploy`
compiles from `dist/`.

### `agentcore invoke` renders nothing for a JSON SSE stream
`--prompt '{"application_id":"APP-1004"}'` reports `"success": true, "response": ""`
while the runtime is in fact streaming correctly. The CLI renders text deltas, and
our frames are JSON objects. Do not debug the runtime off that empty string: call
`bedrock-agentcore.invoke_agent_runtime` with boto3 and read the bytes. The same
invocation that showed `response: ""` returned 16,224 bytes of valid frames.

### There is no CloudFront (or any static hosting) in AgentCore
`agentcore add` has no frontend/site/distribution subcommand and the config schema
has no such resource, so "AgentCore + CloudFront" is not a deployment mode the CLI
offers — it is a thing to build. Note also that CloudFront cannot front the runtime
directly: OAC signs SigV4 for S3 / Lambda function URLs / VPC origins, not for
`bedrock-agentcore`, so a compute shim is unavoidable. And a shim cannot be a plain
passthrough, because **the repo has two SSE frame vocabularies**: the runtime emits
`start / agent_start / agent_done / synthesis_start / done`, while `ui/server.py`
emits — and `ui/src/hooks/useRun.ts` consumes — `run_started / agent_started /
agent_completed / agent_failed / synthesis_started / synthesis_completed /
run_completed`. Any browser-to-runtime path needs that translation somewhere.

## What was wrong before (do not reintroduce)

- `agents/prompts.py` hand-rewrote all nine customer prompts ("verbatim in intent")
  and invented a `RISK:` / `FINDINGS:` output format. The parser then defaulted to
  `LOW RISK` whenever it could not find a `RISK:` line — feed it real prompts and
  every application clears.
- The master JSON had **22 keys** with `application_id` first (customer: 21, no such key).
- Francis and the eight persona names (ISAAC_IDENTITY … RENEE_RINGS) appeared nowhere.
- The ~29 numbered General Rules and the ten alert-ID categories existed only as
  6 prose display strings.
- `NOMINAL_LATENCY_S` / `NOMINAL_TOKENS` fabricated the "26s → 8.5s" headline and
  every cost figure; the "measured wall-clock" beside them was
  `time.sleep(nominal * 0.05)`.
- The signal/analysis split existed only inside a CLI display function; the real
  path fetched the full record 9 times and gave every specialist every signal.
- `deploy/deploy.md` instructed two commands that do not exist.

## Status

Landed and verified by execution:
- `prompts/` — 18 files byte-identical to source, sha256-guarded. **215 tests pass**;
  5 mutation attempts (including regenerating the manifest) all caught.
- `agents/models.py`, `agents/pricing.py` — real rate card, cache guards,
  fabricated tables deleted. **58 tests pass.**
- `agents/fanout.py`, `contracts.py`, `synthesize.py` — async fan-out, 21-key model.
- `signal_layer/rules/` + `bands.py` + `alert_categories.py` — **29 rules: 27
  enforced in code, 2 prompt-enforced. 66 tests pass**, rule text extracted from
  the verbatim docs rather than retyped.
- `evals/bench.py` + golden dataset + 3 evaluators — **70 tests pass**; 4 anti-
  fabrication guards mutation-tested; evaluator JSON validated against the CLI's
  own Zod schema.
- `ui/` — real shadcn (15 Radix+CVA primitives), `npm run build` passes,
  SSE verified with raw curl, 21-key contract validated in-browser, zero JS
  console errors via Playwright, no "Snowflake" string anywhere.
- `agentcore/` — hand-authored project for the real CLI; `agentcore validate` passes.
- `app/underwriter/main.py` — streaming AgentCore entrypoint.

## TODO

**Blocking a full end-to-end demo**
- [ ] `signal_layer/registry.py` and `signal_layer/schema.py` have not landed.
      `signal_layer/__init__.py` already imports them, so the whole package is
      currently unimportable and `tests/test_rules.py` fails collection.
- [ ] `fixtures/applications/*.json` (12+ scenarios) not yet written. Until then
      the UI and bench fall back to the legacy 5-record `data/applications.py`,
      and the anti-false-positive scenarios (dealer 900+ without coordination,
      all-null employer contact fields, staffing agency, multi-unit address,
      alert-volume-heavy) do not exist as data.
- [ ] `app/francis/main.py` + its 8 `@tool` wrappers.

**Cleanup of superseded code**
- [ ] Retire `agents/prompts.py` (paraphrases), and update
      `agents/specialists.py` + `agents/orchestrator.py`, which still import it
      and still import the deleted `nominal_latency` / `nominal_tokens`.
- [ ] Rewrite or delete `tests/test_local.py` — it asserts the old vote-counting
      synthesis and the deleted nominal tables, i.e. it currently encodes spec
      violations.
- [ ] Remove `SIM_LATENCY_SCALE` from `app.py`.
- [ ] Fix the three stale `agentcore configure/launch` references that `ci.sh`
      flags: `README.md:129`, `agentcore_runtime.py:6-7`.
- [ ] `tools/cortex_analyst_mcp.py` still calls the fictional
      `cortex_analyst_query`; replace with the Lambda Gateway target design, and
      correct `docs/semantic-layer-mcp.md` (it claims SigV4 to Snowflake).

**Then**
- [ ] Raise `max_tokens` per tier — currently 1536/2048, while the customer's
      contract allows 4000-character POSSIBLE/HIGH narratives.
- [ ] Move the cache point to `system prompt + stable payload prefix` so caching
      actually engages, then re-measure and only then quote a caching benefit.
- [ ] Run `evals/bench.py --live` end-to-end for a real per-application **p50/p95**.
      A single verified run now exists (`evals/results/deployed_runtime_e2e.json`,
      2026-08-11, **n=1**) against the deployed runtime: 41.86s end-to-end,
      $0.154764, 8/8 specialists, 21 keys valid. That is an existence proof, not a
      distribution — no percentile may be quoted from it, and the "<1 minute" claim
      still needs a real sample.
- [ ] Consider moving `dealer` off Haiku (it can never cache there).
- [ ] Evaluate whether `bustout` and the synthesizer belong on HEAVY — flagged in
      `TIER_RATIONALE`; needs an eval, not a judgement call.

## Questions for the customer

1. **Enum conflict.** `master_agent_prompt.txt` says mid-band `MEDIUM RISK` with a
   3-value recommendation; the process-guide PDF §5.1/5.2 says `POSSIBLE RISK` with
   **4** values (`APPROVE / APPROVE WITH STIPULATIONS / MANUAL REVIEW / DECLINE`)
   and shows no `top_risk_factors` or `*_flag` columns. We implement the prompt file
   and expose `PDF_TO_PROMPT_ENUM_MAP`. This is a real DDL question for
   `UNDERWRITING_REPORT_OUTPUT`.
2. **Is their Cortex Analyst backed by a semantic VIEW or a stage-based semantic
   model YAML?** The managed MCP server supports views only. This single answer
   gates the whole "keep the semantic layer" proposal.
3. **`full_underwriting_signal_dictionary.xlsx`** — the authoritative signal
   contract — is only an embedded thumbnail on page 3 of the PDF (0 extractable
   attachments). Our ~75-signal registry is reconstructed from the 9 prompts.
   Requesting the xlsx and `CLEAN_MAIN_STREAM_FILE.sql` would settle the signal
   set, the output DDL, and every enum conflict at once.
4. The three `PP_Forensic_{Hi,M,L}...pdf` attachments on page 10 are almost
   certainly gold-standard example outputs per risk band — i.e. the actual
   acceptance criteria. Worth asking for.
5. Does their Snowflake account enforce network policies? AWS publishes no stable
   Gateway egress IPs, so this may need PrivateLink.
6. Bedrock quota headroom: 300k apps/day × 9 calls × ~2,250 tokens ≈ 6.1B
   tokens/day. Sonnet 5's daily ceiling is 8.64B and is **not adjustable** — that
   is an account-team escalation, not a self-serve ticket, and it should be raised
   before any load test.

## Commands

```bash
# offline test suite (no AWS)
MOCK_MODE=1 python3 -m pytest tests/ -q

# the demo UI
cd ui && npm install && npm run build && cd ..
MOCK_MODE=1 python3 -m uvicorn ui.server:app --port 8080     # then open localhost:8080

# measured benchmarks (offline = orchestration overhead only)
python3 -m evals.bench --offline --concurrency 1,4,16 --iterations 40
AGENTCORE_BENCH_ALLOW_LIVE=1 python3 -m evals.bench --live --iterations 3   # real Bedrock spend

# deploy (see deploy/deploy.md; never `agentcore configure`/`launch`)
agentcore validate --json
agentcore deploy --dry-run
```
