# Measurement and evaluation

This directory owns every performance and cost number this repo is allowed to
show Point Predictive. Read the measurement-status section first: it is the part
that keeps the rest honest.

---

## 1. Measurement status — what has and has not actually been measured

### The pipeline benchmark: 14 applications x 3 repetitions, n=42 adjudications

This is the headline measurement and it is committed:
`evals/results/pipeline_14x3_us-east-1.json` (full report, every raw run) and
`evals/results/pipeline_14x3_us-east-1.md` (the summary below). Produced by
`python -m evals.bench --pipeline --repetitions 3`, which calls
`agents.fanout.fan_out` and `agents.synthesize.synthesize` — the shipped code
path, not a re-implementation — once per (application, repetition).

**378 real model calls** (336 specialist + 42 synthesis), `us-east-1`,
2026-07-28, git `9bb569ba`. Total measured spend **$6.4659**.

| stage | n | min | p50 | p95 | max |
|---|---:|---:|---:|---:|---:|
| **end to end** | 42 | 18.93s | **31.45s** | **46.55s** | 74.97s |
| fan-out (8 specialists) | 42 | 15.37s | 24.93s | 41.05s | 70.62s |
| synthesis (GPT-5.6 Luna) | 42 | 3.08s | 4.20s | 5.59s | 15.21s |
| sum-of-eight (sequential counterfactual) | 42 | 65.52s | 85.62s | 157.14s | 173.00s |

p99 is **refused**, not omitted: 42 samples, and a p99 needs 100.

**Under the 60s target: 40 of 42 runs. 12 of the 14 applications came in under
60s on every repetition; all 14 did on at least one.** `APP-1001` and `APP-1007`
straddle the line — each has one repetition over (70.24s and 74.97s) and two
comfortably under. No application was ever over on all three. That is the honest
form of the claim: the p50 is half the target, and the tail crosses it.

**Parallel speed-up, fan-out vs sum-of-eight (n=42): min 1.85x, p50 3.64x,
p95 4.99x, max 5.11x.** One ratio per run, not one number for the port. The
fan-out is bounded by its slowest dimension, so the ratio moves with which
specialist ran long — the 1.85x run is one where a single specialist took 70.6s
of a 70.6s fan-out.

**Per-specialist latency — the spread is the story.** `rings` was the fan-out's
tail in **25 of 42 runs**; `straw` never was:

| specialist | model | n ok | n failed | p50 | p95 | max | tail in N runs |
|---|---|---:|---:|---:|---:|---:|---:|
| `identity` | sonnet-5 | 42 | 0 | 12.56s | 33.40s | 41.04s | 4 |
| `dealer` | haiku-4-5 | 42 | 0 | 6.56s | 14.56s | 18.85s | 0 |
| `straw` | haiku-4-5 | 42 | 0 | 4.40s | 8.76s | 12.52s | 0 |
| `employment` | sonnet-5 | 42 | 0 | 7.48s | 17.64s | 20.00s | 0 |
| `income` | sonnet-5 | 42 | 0 | 8.09s | 31.44s | 66.01s | 5 |
| `synthetic` | opus-5 | 41 | 1 | 8.52s | 20.82s | 26.70s | 1 |
| `bustout` | sonnet-5 | 42 | 0 | 14.46s | 35.71s | 69.78s | 7 |
| `rings` | opus-5 | 41 | 1 | 20.96s | 32.25s | 36.17s | 25 |

`rings` has the highest p50 (20.96s) but *not* the highest max — `bustout`
(69.78s) and `income` (66.01s) both spiked higher. So the p50 tail and the p95
tail are different agents, and shaving `rings` alone would not fix the two runs
that crossed 60s. Failed calls are excluded from these percentiles: a call that
errored after 200ms would otherwise make its dimension look fast.

**Cost per adjudication (n=41 fully priced runs, 1 excluded): min $0.1177,
p50 $0.1440, p95 $0.2271, max $0.2536.** The variance driver is measured, not
asserted: comparing the cheapest run (`APP-1001` rep 3, $0.1177) with the dearest
(`APP-1004` rep 3, $0.2536), specialist *input* tokens barely move
(20,237 → 21,657) while specialist *output* tokens go 4,098 → 13,735. **Cost
tracks how much the specialists write, and they write more on the fraud-heavy
applications.** The synthesis call is a small and stable share throughout
($0.0089 → $0.0151).

**Failures: 1 degraded run of 42.** `APP-1011` repetition 3 lost both `synthetic`
and `rings` to Bedrock `InternalServerException` (two `server_5xx`). **No
throttling and no `MaxTokensReachedException` occurred in 378 calls.** The
degraded run stays in the latency distribution — it really took 21.22s — and is
excluded from the cost distribution, because eight priced calls presented as the
cost of a nine-call adjudication is an under-count wearing a total's name. The
run still produced a valid 21-key adjudication on six specialists, which is the
graceful-degradation path working.

**All 42 adjudications validated against the 21-key contract, and zero needed a
repair attempt** (`repair_attempts` total: 0).

**Verdict agreement — measured here, and worth the customer's attention.** 12 of
14 applications returned the same `overall_risk_decision` on all three
repetitions. Three applications differ from their pinned fixture expectation:

| application | pinned | observed | matched |
|---|---|---|---:|
| `APP-1003` | MEDIUM RISK | LOW RISK (all 3) | 0 of 3 |
| `APP-1005` | MEDIUM RISK | HIGH RISK / MEDIUM RISK | 2 of 3 |
| `APP-1012` | HIGH RISK | HIGH RISK / MEDIUM RISK | 1 of 3 |

This is reported, not adjudicated. Note that "stable" and "correct" are
independent: `APP-1003` was perfectly stable across three runs and stably
disagreed with its pin. Whether the pin or the model is wrong is Point
Predictive's call; run the calibration judge in `evals/evaluators/` before drawing
an accuracy conclusion from it. It does condition every latency figure above,
though — a per-application time is a time for whichever answer the model gave.

**What this run does NOT measure:** AgentCore runtime overhead (calls left the
developer host directly, so no container cold start, session setup or SSE
framing), signal generation (`build_payload` runs outside the timed window
because the customer's architecture precomputes signals), and **throughput** —
applications ran one at a time, so nothing here says what happens when N
adjudications overlap.

### What else the harness has measured in this repo

| Measured | How | Where the number lives |
|---|---|---|
| Orchestration overhead per specialist and end-to-end, at concurrency 1 / 4 / 16, cold vs warm | `python -m evals.bench --offline` | the JSON that run writes, plus its printed table |
| **Real prompt token counts for the two Haiku specialists**, via Bedrock `CountTokens` | `--live --dry-run` (free metering call, no inference) | see the measured table below |
| That the 21-key contract, the risk bands, the titles and the length rules are enforceable as assertions | `python -m pytest tests/test_bench.py -q` | `tests/test_bench.py` |
| That the evaluator JSON validates against the installed AgentCore CLI's own zod schema | see §5 | `evals/evaluators/*.json` |
| That the golden dataset parses under the CLI's own `parseAndValidate` | see §5 | `evals/datasets/underwriting_golden.jsonl` |

**Measured prompt sizes** — Bedrock `CountTokens`, us-east-1, 2026-07-28, on the
assembled prompt (verbatim system prompt + projected APP-1004 payload):

| Specialist | Model | Measured prompt tokens | Min cacheable prefix | Verdict |
|---|---|---:|---:|---|
| `dealer` | haiku-4-5 | **1829** | 4096 | **never caches** |
| `straw` | haiku-4-5 | **1655** | 4096 | **never caches** |
| the other six | sonnet-5 / opus-5 | not measurable — see below | 1024 / 512 | unknown |

This is the cache cliff in §4, measured rather than assumed: both Haiku-routed
specialists sit at ~1.7k tokens against a 4096-token minimum, so **their prompt
cache hit rate is permanently 0% and no amount of warming changes it**. Fixing it
means either moving those two off Haiku or padding the cached prefix past 4096
tokens — a decision worth raising with Rebecca, not silently absorbing.

`CountTokens` is not offered for every model. In us-east-1,
`anthropic.claude-haiku-4-5-20251001-v1:0` returns a count, while
`anthropic.claude-sonnet-5` and `anthropic.claude-opus-5` both raise
`ValidationException: The provided model doesn't support counting tokens`. It also
rejects inference-profile prefixes — the `global.*` and `us.*` forms fail; the
bare `anthropic.*` id is required. `bench.py` strips the prefix and reports
`None` for the unsupported models rather than substituting an estimate.

### The earlier `--live` smoke runs (superseded — kept for the two findings they produced)

These predate the pipeline benchmark above and are **not** the measurement to
quote; the 42-run report supersedes them. They are retained because the two
failures they caught are still the reason two settings are what they are.

Two `--live` smoke runs, one per region, one iteration each, `max_tokens` 1200
then 4000. **n=1 per agent, so every percentile was correctly suppressed.** Real
numbers from real calls, and a sample of one:

| Agent | Model | wall ms | SDK `latencyMs` | TTFT ms | in tok | out tok | cost $ |
|---|---|---:|---:|---:|---:|---:|---:|
| identity | sonnet-5 | 9049 | 6989 | 3824 | 2702 | 364 | 0.009044 |
| dealer | haiku-4-5 | 5737 | 3838 | 2781 | 2389 | 199 | 0.003384 |
| straw | haiku-4-5 | 5211 | 3432 | 2424 | 2041 | 214 | 0.003111 |
| employment | sonnet-5 | 7455 | 5972 | 4333 | 3206 | 342 | 0.009832 |
| income | sonnet-5 | 6601 | 5264 | 2795 | 2936 | 337 | 0.009242 |
| synthetic | opus-5 | 18624 | 11338 | 13418 | 3028 | 404 | 0.025240 |
| bustout | sonnet-5 | 36623 | 35685 | 2536 | 3618 | 1057 | 0.017806 |
| rings | opus-5 | — | — | 3384 | — | — | **failed** |
| synthesis | sonnet-5 | 5954 | 5401 | 1978 | 2723 | 490 | 0.010346 |

Every cache field measured a **miss** on these runs, and the cacheability check
resolved from real token counts: haiku-4-5 at 2389 tokens → `never_cacheable`
(4096 minimum), sonnet-5 at 2702 → `cacheable`, opus-5 at 3028 → `cacheable`.

Neither smoke run completed all nine calls: `rings` failed on
`MaxTokensReachedException` at `max_tokens=1200`, then `synthetic` on a transient
Bedrock `InternalServerException`. Both findings still stand:

- **`max_tokens` must be ≥ ~4000** for these prompts. A 1200-token cap truncates
  the bustout and rings analyses, which run long by design. `agents/models.py` now
  sets 3072/4096 by tier, and **no call in the 378-call pipeline benchmark hit
  `MaxTokensReachedException`** — that is the fix being confirmed, at n=378.
- **Transient 5xx are real and the fan-out needs a retry policy.** The pipeline
  benchmark hit two `InternalServerException`s in 378 calls at concurrency 1
  (~0.5%), both inside one adjudication. Graceful degradation held — that run
  still produced a valid 21-key adjudication on six specialists — but a lost fraud
  dimension is not a saving, and this rate is measured at concurrency 1 only.

### What HAS NOT BEEN MEASURED

Nothing below has been measured in this repo. Do not put any of it on a slide.

- **A p99 of anything.** The pipeline benchmark is n=42; a p99 needs 100. The
  harness refuses to print one and the committed report carries `null` with the
  reason beside it. 14 x 8 repetitions (~$18) would reach it.
- **A per-application p95.** Each application has n=3 in the committed run, so
  every per-application p95 in that file is suppressed. Only the pooled p95
  (n=42) is supportable, and it describes the workload as a whole, not any one
  application.
- **Throughput, or latency under concurrent load.** The pipeline benchmark ran
  **one application at a time**. Nothing in this repo says what happens to p95 or
  to the throttling rate when 4, 10 or 50 adjudications overlap — and the one
  `server_5xx` pair observed at concurrency 1 is a reason to expect that question
  to matter.
- **Prompt-cache hit rate under repeat load for the specialists.** Every
  specialist call in the 42-run benchmark measured a miss, and that is the
  documented behaviour rather than a defect: the assembled prompts are 547–956
  tokens against minimums of 512 (Opus 5) / 1024 (Sonnet 5) / 4096 (Haiku 4.5),
  and the large per-application payload differs every time, so a cache entry is
  never reused. See §4.
- **Accuracy.** The benchmark measures whether an adjudication *validated* and
  whether repeated runs *agreed*, which is not the same as whether the verdict is
  right. The three pin disagreements above are surfaced, not resolved. Run the
  calibration judge before making any accuracy claim.
- **AgentCore runtime overhead** — container cold start, session setup, SSE
  framing. `--live` and `--pipeline` both measure Bedrock from the developer's
  host, not through a deployed runtime. This is the single largest known gap
  between the 31.45s p50 above and what a deployed customer would see.
- **Any region other than `us-east-1`,** for the pipeline as a whole.
- **Any Snowflake comparison.** This repo has never run the customer's Snowflake
  pipeline and cannot measure it. The customer's own reported figures (4,500
  applications taking >48h; $700K–$1M ARR) are **theirs, by attribution**, and
  must always be labelled as such. Never present them beside one of our numbers
  as though both were measured the same way.

### What this replaces

The previous demo's headline claim — "26s sequential → 8.5s parallel", "under a
minute" — was computed entirely from a hand-written `NOMINAL_LATENCY_S` dict in
`agents/models.py`, and the "measured wall-clock" printed next to it was
`time.sleep(nominal * 0.05)`. Per-agent cost came from a hard-coded
`NOMINAL_TOKENS` table. None of those numbers were measured. `evals/bench.py`
replaces all of them; `tests/test_bench.py` fails CI if that style of fabrication
returns.

---

## 2. Running the harness

Three modes. `--pipeline` is the one that answers "how long does one application
take"; the other two exist for narrower jobs.

| mode | calls a model? | measures |
|---|---|---|
| `--offline` | no | this repo's orchestration overhead only |
| `--live` | yes | per-call Bedrock latency/cost, via this module's own agents |
| `--pipeline` | yes, unless `--mock` | **the shipped pipeline**, end to end, per application |
| `--reaggregate` | no | recomputes a committed report's statistics from its own raw runs |

### Pipeline (the benchmark; real Bedrock; costs money)

```bash
# Structural dry run: exercises the whole path, calls nothing, spends nothing.
python -m evals.bench --pipeline --mock --repetitions 1

# The committed benchmark. 14 applications x 3 repetitions = 42 adjudications,
# 378 model calls. Measured spend for this exact command: $6.4659.
AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.bench --pipeline \
    --repetitions 3 --region us-east-1 \
    --json evals/results/pipeline_14x3_us-east-1.json \
    --markdown evals/results/pipeline_14x3_us-east-1.md

# One application, to check credentials and the mantle endpoint before spending.
AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.bench --pipeline \
    --repetitions 1 --application APP-1001
```

Why this is a separate mode from `--live`: `--live` builds its own
`strands.Agent` per specialist and its own synthesis call, so it measures *a*
fan-out rather than *the* fan-out. It does not go through `agents.fanout`'s
semaphore, widened executor or shared botocore pool, it does not go through
`agents.synthesize`'s structured-output validation or bounded repair, and it
cannot reach the GPT-5.6 synthesizer at all, because GPT-5.x is not on Converse.
`--pipeline` calls `fan_out` and `synthesize` directly, so the number describes
the program AgentCore actually runs.

`--pipeline` is gated on the same `AGENTCORE_BENCH_ALLOW_LIVE=1` as `--live`,
prints the full plan (applications, repetitions, call count, every model id, and
a budget estimate explicitly labelled a *prior from an earlier run*) before
calling anything, and streams one line per adjudication as it goes so a run that
starts throttling is visible immediately rather than at the end.

Choosing `--repetitions`: 3 is the default because it is what the cost allows and
because 14 x 3 = 42 clears the 20-sample minimum for a **pooled** p95. It does
**not** clear it per application (3 each), and the report suppresses those rather
than printing them. Reaching a p99 needs 100 pooled samples, i.e.
`--repetitions 8` and roughly $18.

### Re-aggregating without re-spending

If the aggregation changes but the measurements have not, do not re-run the
benchmark:

```bash
python -m evals.bench --reaggregate evals/results/pipeline_14x3_us-east-1.json \
    --markdown evals/results/pipeline_14x3_us-east-1.md
```

Every measured field lives in `raw_runs`, so every statistic is recomputable from
it. The provenance block is carried through unchanged — the numbers still belong
to the run that measured them, and re-aggregating does not restamp them with
today's git sha — and a `reaggregated_at_utc` field plus a caveat record that the
derivation, and only the derivation, is newer than the measurement.

### Offline (default; no AWS, no credentials, no spend)

```bash
python -m evals.bench --offline
python -m evals.bench --offline --concurrency 1,4,16 --iterations 40
python -m evals.bench --offline --json evals/last_offline_run.json
```

Offline mode measures **only** this repo's orchestration overhead: signal-layer
payload projection, verbatim prompt assembly, `asyncio.gather` fan-out
scheduling, and synthesis-prompt substitution. **No model is called, so no model
latency is measured.** The report says so in its header, and in the `caveats`
array of the JSON.

Offline wall-clock figures describe the host they ran on. They are useful for
catching an orchestration regression and for nothing else. Do not quote one as
an application-processing time.

### Live (real Bedrock; costs money)

Double-gated on purpose — the flag alone is one keystroke from a billed run:

```bash
# Price it first. Makes no model calls.
AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.bench --live --dry-run

# Then run a small sample. Use --max-tokens 4000 or higher: a 1200-token cap was
# measured to truncate the bustout and rings analyses (MaxTokensReachedException).
AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.bench --live \
    --iterations 3 --concurrency 1,4 --region us-east-1 \
    --max-tokens 4000 --json evals/last_live_run.json
```

A per-application time and a per-application cost with real percentiles now come
from `--pipeline` (above), not from this mode. `--live` remains useful for one
thing `--pipeline` does not do: it streams, so it is the only mode that measures
**time-to-first-token** per specialist.

Before calling anything, `--live` prints every model id with its resolved rate
card and an **upper-bound** spend estimate. The input side of that estimate is a
real Bedrock `CountTokens` measurement per prompt; the output side is the
`max_tokens` ceiling, which is a true bound rather than a prediction. If
`CountTokens` is unavailable the estimate reports `null` and says so instead of
approximating.

`--live` defaults to `--iterations 2`. Percentiles will be suppressed at that
sample size — that is the intended behaviour, see §3.

### Reading the output

**The human table.** Per-agent wall clock (p50/p95/p99 with the sample count
`n`), end-to-end by concurrency split cold vs warm, the prompt-cacheability
verdict per model, and the caveat list. `n/a` in a percentile column means the
percentile was **refused**, never that it was zero.

**The JSON.** Same figures, machine-readable, for the UI and the README to
consume instead of inventing their own:

| Key | Contents |
|---|---|
| `run` | mode, UTC timestamp, git sha and branch, whether the tree was dirty, region, model ids, host, package versions |
| `workload` | application source module, application ids, domains, per-agent model assignment |
| `totals` | iterations, calls, **calls with real token usage**, errors, total and per-adjudication cost |
| `per_agent` | wall clock, SDK-reported `latencyMs`, TTFT, cost, cache hits/misses, errors, and the rate card applied |
| `by_concurrency` | end-to-end all / cold / warm at each level |
| `prompt_cacheability` | per model: the minimum cacheable prefix, the measured prompt size, and the verdict |
| `caveats` | every `MEASURED:` and `NOT MEASURED:` statement for that run |
| `raw_iterations` | every individual measurement, so any aggregate can be recomputed |

Two fields decide whether a cost number is real: `totals.calls_with_real_usage`
and `per_agent.<agent>.cost_usd.n_priced_calls`. If either is `0`, no cost in
that report was measured, and every cost field will be `null`.

---

## 3. Why percentiles get refused

`bench.py` will not print a percentile a sample cannot support:

| Percentile | Minimum samples | Why |
|---|---|---|
| p50 | 5 | a three-sample median is not worth showing a customer |
| p95 | 20 | 1/(1−0.95): below this the tail cannot be observed |
| p99 | 100 | 1/(1−0.99) |

A suppressed percentile is `null` in the JSON with its reason recorded beside it
(`"refused: 2 sample(s), need >= 20..."`). A "p99" from one sample is the
maximum wearing a percentile's name, and printing one to this customer is the
same class of error as inventing the number outright.

Percentiles use nearest-rank without interpolation, so every value printed is a
value the run actually observed. The method string is recorded in the output.

---

## 4. The prompt-cache cliff (read before showing a 0% hit rate)

The minimum cacheable prompt prefix is **not monotonic across model
generations**:

| Model | Minimum cacheable prefix |
|---|---|
| Opus 5 | 512 tokens |
| Opus 4.8, Sonnet 5, Sonnet 4.6 | 1024 tokens |
| **Haiku 4.5** | **4096 tokens** |

A ~1800-token specialist prompt therefore caches on Opus 5, Opus 4.8, Sonnet 5
and Sonnet 4.6 — and **silently never caches on Haiku 4.5**. No error, no
warning, just a permanent 0% hit rate. Since `agents/models.py` routes `dealer`
and `straw` to Haiku, this applies directly to two of the eight specialists.

`bench.py` reports this as a separate verdict (`cacheable` / `never_cacheable` /
`unknown`) from the observed hit rate, so a 0% never appears without the
sentence that explains it. `unknown` means the run did not measure a prompt token
count — offline runs cannot — and is not a guess.

---

## 5. AgentCore CLI commands

Verified against **@aws/agentcore 1.0.0-preview.22**.

Two commands that older AgentCore write-ups still teach **do not exist** in this
CLI — `agentcore configure` and `agentcore launch` both print help and exit 0,
so a script built on them fails silently rather than loudly. The real verbs are
`create`, `add`, `validate`, `deploy`, `dev`, `invoke`, `run`, `logs`, `traces`
and `status`.

### Register the evaluators

Each file in `evals/evaluators/` is a complete evaluator config, passed with
`--config` (which overrides `--model` / `--instructions` / `--rating-scale`):

```bash
agentcore add evaluator \
  --name pp_calibration_judge \
  --level TRACE \
  --type llm-as-a-judge \
  --config evals/evaluators/pp_calibration_judge.json

agentcore add evaluator \
  --name pp_false_positive_discipline \
  --level TRACE \
  --type llm-as-a-judge \
  --config evals/evaluators/pp_false_positive_discipline.json

agentcore add evaluator \
  --name pp_output_contract_check \
  --level SESSION \
  --type code-based \
  --config evals/evaluators/pp_output_contract_check.json
```

`--name` and `--level` are required in non-interactive mode. `--config` still
needs them because the CLI validates the flags before reading the file.

The code-based checker declares `codeLocation: app/pp_output_contract_check/`;
`agentcore add evaluator` scaffolds `lambda_function.py` and
`execution-role-policy.json` there, and the deterministic checks listed in the
evaluator's `description` are implemented in that handler before
`agentcore deploy`.

### Register and publish the dataset

```bash
agentcore add dataset \
  --name underwriting_golden \
  --schema-type AGENTCORE_EVALUATION_PREDEFINED_V1 \
  --description "Point Predictive underwriting scenarios with anti-false-positive rules"

# Point the dataset's managed config at evals/datasets/underwriting_golden.jsonl
# in agentcore/agentcore.json, then freeze it as an immutable version:
agentcore dataset publish-version --name underwriting_golden
```

`publish-version` promotes DRAFT to a numbered immutable version. Omit
`--dataset-version` on a run to use the local file, or pass `N` / `DRAFT`.

### Run an evaluation

```bash
# Against a deployed runtime's recent traces
agentcore run eval \
  --runtime underwriter \
  --evaluator pp_calibration_judge pp_output_contract_check \
  --days 7 \
  --json

# Driving the agent with the dataset's scenarios instead of historical traces
agentcore run eval \
  --runtime underwriter \
  --evaluator pp_calibration_judge pp_false_positive_discipline \
  --dataset underwriting_golden \
  --dataset-version DRAFT \
  --output evals/last_eval.json

# One session, with ground truth passed inline (-A is repeatable)
agentcore run eval \
  --runtime underwriter \
  --evaluator pp_output_contract_check \
  --session-id "$SESSION_ID" \
  -A "overall_risk_decision is LOW RISK" \
  -A "top_risk_factors is an empty string" \
  --expected-trajectory call_identity_agent,call_dealer_agent
```

### Batch across sessions

```bash
agentcore run batch-evaluation \
  --runtime underwriter \
  --evaluator pp_calibration_judge pp_false_positive_discipline pp_output_contract_check \
  --name pp-underwriting-regression \
  --dataset underwriting_golden \
  --lookback-days 7 \
  --wait \
  --json
```

`--wait` blocks until the job reaches a terminal state. Results afterwards:
`agentcore batch-evaluations <id>`, or `agentcore evals` for saved runs.

### Continuous monitoring

```bash
agentcore add online-eval \
  --name pp-underwriting-online \
  --runtime underwriter \
  --evaluator pp_calibration_judge pp_output_contract_check \
  --sampling-rate 5 \
  --enable-on-create

agentcore deploy
```

`--sampling-rate` is a percentage (0.01–100). Use `agentcore pause` /
`agentcore resume` to stop and restart sampling without removing the config.

### Validate before deploying

```bash
agentcore validate
```

The definitions here were additionally checked directly against the CLI's own
zod schema and dataset parser (`EvaluatorSchema` from
`@aws/agentcore/dist/schema/schemas/agentcore-project.js`, and the CLI's
`parseAndValidate`), because a shape that only satisfies our own tests is not
evidence.

---

## 6. The golden dataset

`evals/datasets/underwriting_golden.jsonl` — one JSON object per line in the
`AGENTCORE_EVALUATION_PREDEFINED_V1` shape: `scenario_id`, `turns[]` with
`input` / `expectedResponse`, `assertions[]`, `expected_trajectory[]`.

Two extra keys are carried for our own review and ignored by the CLI's parser:

- `expected_provenance` — which fixture `expected` block the scenario encodes,
  plus the exact verbatim prompt line each assertion comes from, quoted as
  `prompts/verbatim/<file>::<SECTION> -> '<line>'`. Every assertion is
  traceable to a line in the customer's own prompt.
- `notes` — why a scenario exists, where that is not self-evident.

Coverage:

| Group | Scenarios |
|---|---|
| End-to-end adjudications | the five shipped applications: LOW/APPROVE, LOW/APPROVE with a spousal co-borrower, MEDIUM/REVIEW on income overstatement, HIGH/DECLINE on synthetic identity plus ring overlap, MEDIUM/REVIEW on bust-out velocity |
| Anti-false-positive | dealer consortium score 900+ with no coordination ⇒ LOW and reported as credit-risk context; employer with all-null address/phone/city/state/ZIP ⇒ LOW data gap; staffing-agency high SSN counts ⇒ not a fraud signal; multi-unit address reuse ⇒ benign; alert-volume-heavy but explainable ⇒ LOW |
| Format contracts | straw with no co-borrower ⇒ 2–3 sentences; rings ⇒ **no title** |
| Interactive (Francis) | `## EXECUTIVE SUMMARY` / `## ANALYSIS` with no `###RECOMMENDATIONS` unless asked, the MAXIMUM-2-tools rule, and the RETRO ⇒ RETRO_FRANCIS deflection |

`fp_employer_all_null_contact_fields_low` references
`fixtures/applications/APP-1006.json`, a fixture whose employer address, phone,
city, state and ZIP are all null. That record is the one input this dataset needs
and does not yet have; the scenario encodes the rule regardless, so it will start
passing as soon as the fixture lands.

---

## 7. Known enum conflict — surfaced, not resolved

The customer's process-guide PDF and their executable prompt disagree:

| | Mid band | Recommendation values |
|---|---|---|
| `Agent_Underwriting_Process_Guide` (PDF) | POSSIBLE RISK | APPROVE / APPROVE WITH STIPULATIONS / MANUAL REVIEW / DECLINE (4) |
| `master_agent_prompt.txt` (**executable**) | MEDIUM RISK | APPROVE / REVIEW AND APPLY STIPULATIONS / DECLINE (3) |

The prompt file is the artifact that runs, so it wins: the dataset asserts
`MEDIUM RISK` and the three-value recommendation enum. This is flagged for
Rebecca rather than silently reconciled — if the PDF is the newer intent, the
prompt needs updating, and that is her call, not ours.

Note the mid-band name differs by layer, and both are correct in their own
place: specialists band as **LOW RISK / POSSIBLE RISK / HIGH RISK**, while the
master adjudication uses **LOW RISK / MEDIUM RISK / HIGH RISK**.

---

## 8. Every number, and where it came from

The one rule this directory exists to enforce: **a number shown to the customer
is either a real measurement from a named run in this repo, or it is the
customer's own reported figure with attribution.** There is no third category.

The only real Bedrock latencies on record in this project are platform-probe
measurements taken on a short fraud prompt with `maxTokens=120` in `us-east-1`
(Haiku 4.5 1848 ms, Sonnet 4.6 4481 ms, Opus 4.8 2990 ms, Sonnet 5 at
effort=low 2393 ms, Opus 5 at effort=low 2924 ms). They establish one useful
fact — price does not track latency, since Sonnet 4.6 is slower than Opus 4.8 —
and they are **not** this pipeline's latency. Only a `--live` run produces that.
