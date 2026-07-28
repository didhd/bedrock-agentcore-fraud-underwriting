# Measurement and evaluation

This directory owns every performance and cost number this repo is allowed to
show Point Predictive. Read the measurement-status section first: it is the part
that keeps the rest honest.

---

## 1. Measurement status — what has and has not actually been measured

### What the harness has measured in this repo

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

### Real live Bedrock measurements taken (small sample — do not generalize)

Two `--live` smoke runs were executed to prove the measurement path works, one
per region, one iteration each, `max_tokens` 1200 then 4000. **n=1 per agent, so
every percentile was correctly suppressed.** These are real numbers from real
calls, and they are a sample of one:

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

**No complete adjudication was measured.** In both runs one specialist failed —
`rings` on `MaxTokensReachedException` at `max_tokens=1200`, then `synthetic` on a
transient Bedrock `InternalServerException`. The harness therefore reports
`cost_per_adjudication_usd: null`, because averaging the eight surviving calls
would publish an under-count as a total. Two things follow, both worth raising
with the customer rather than papering over: **`max_tokens` must be ≥ ~4000** for
these prompts (a 1200-token cap truncates the bustout and rings analyses, which
run long by design), and **the fan-out needs a retry policy** for transient
5xx before any throughput claim is credible.

### What HAS NOT BEEN MEASURED

Nothing below has been measured in this repo. Do not put any of it on a slide.

- **A complete end-to-end application-processing time.** See above: no
  adjudication has yet completed all nine calls in one run. The observed
  end-to-end wall clock of ~47s came from a run with a failed agent and a
  1200-token cap, so it is **not** a per-application latency. The customer's
  "<1 minute per application" target remains **unverified in this repo**.
- **Any latency or cost distribution.** n=1 per agent. There is no measured p50,
  p95 or p99 for anything, and the harness refuses to print one.
- **Cost per adjudication.** Null for the reason above.
- **Prompt-cache hit rate under repeat load.** Both runs were cold; every call
  measured a miss. The `never_cacheable` verdict for the two Haiku specialists is
  measured and permanent, but no warm-cache hit rate exists yet.
- **Concurrency behaviour.** Both live runs were `--concurrency 1`. Nothing is
  known about throttling, queueing or tail latency at 4 or 16.
- **AgentCore runtime overhead** — container cold start, session setup, SSE
  framing. A `--live` run measures Bedrock from the developer's host, not through
  a deployed runtime.
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

To produce the figures the customer actually asked about — a per-application time
and a per-application cost with real percentiles — this needs to run at
`--iterations 40 --concurrency 1,4,16`, which is roughly 1,800 calls. Price it
with `--dry-run` first and get sign-off on the spend before running it.

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
