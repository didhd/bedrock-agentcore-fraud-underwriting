# Multi-Agent Auto-Loan Fraud Underwriting on Amazon Bedrock AgentCore

A 1:1 replacement, on **Amazon Bedrock AgentCore**, of an agentic auto-loan fraud
underwriting platform currently running on Snowflake Intelligence (Cortex Agents):
**eight specialist fraud agents running in parallel, synthesized by a master
underwriting agent**.

Built with the **Strands Agents SDK** on **AgentCore Runtime**, with Claude models
on Amazon Bedrock.

> The point of this repo is fidelity. The customer's engineer wrote the production
> agents; their prompts, risk calibration, and output contracts are reproduced
> byte-for-byte rather than reinterpreted. Where a claim cannot be measured, this
> repo says so instead of estimating.

## What it does

Given an auto-loan application, the system produces an underwriting adjudication —
**APPROVE**, **REVIEW AND APPLY STIPULATIONS**, or **DECLINE** — backed by eight
independent specialist analyses across identity, dealer, straw borrower,
employment, income, synthetic identity, bust-out, and fraud rings.

It also answers targeted analyst questions ("is there income fraud on APP-1003?")
through the interactive router path, which calls at most two specialists per query.

The calibration carried over from the production prompts: **LOW risk is the most
common outcome**. Specialists validate precomputed signals against benign
`_context` (household sharing, employer scale, population density,
self-employment, data quality) before escalating, and **alert volume alone never
drives risk**.

## Fidelity: how the customer's agents are preserved

| Aspect | How it is preserved | Enforced by |
|---|---|---|
| The nine agent prompts | Byte-identical copies in `prompts/verbatim/`, sha256-verified at import | `prompts/loader.py`, `tests/test_prompt_fidelity.py` (215 tests) |
| Agent identities | ISAAC_IDENTITY, DIANA_DEALER, SAMMY_STRAW, EMILY_EMPLOYMENT, IRIS_INCOME, SYLVIA_SYNTHETIC, BRUCE_BUSTOUT, RENEE_RINGS, and Francis for the interactive path | `prompts.loader.AGENT_NAMES` |
| Specialist output shape | Titled prose with the exact title string — and **no title** for bust-out and rings, which the source prompts deliberately omit | contract tests |
| Length discipline | LOW = 3–5 sentences; POSSIBLE/HIGH ≤ 4000 chars; straw with no co-borrower = 2–3 sentences | contract tests |
| Master adjudication | **Exactly 21 keys in the source order**, starting at `master_summary`; no `application_id` key | `agents/contracts.py` + Strands `structured_output_model` |
| Synthesis input order | IDENTITY, DEALER, STRAW, EMPLOYMENT, INCOME, SYNTHETIC, BUSTOUT, RINGS — substituted into the source prompt's own placeholders | `agents/synthesize.py` |
| Semantic-layer governance | The 29 numbered General Rules: **27 enforced as tested code**, 2 prompt-enforced. The ten alert-ID categories (166 IDs) are data, not prose | `signal_layer/rules/` (66 tests), `signal_layer/alert_categories.py` |
| Signal/analysis separation | Signals are precomputed; each specialist receives only its own domain view and has no data tool | `signal_layer/` |

## The eight specialists

| Specialist | Detects | Anti-false-positive rule it must honor |
|---|---|---|
| Identity | SSN sharing, PII mismatch, identity volatility | Multiple alerts from one underlying data point = one signal |
| Dealer | Dealer-*orchestrated* fraud | Default/EPD/consortium scores are **portfolio risk, not fraud** |
| Straw | Proxy/nominee co-borrowers, role-switching | No co-borrower ⇒ straw risk inherently minimal |
| Employment | Fabricated/shell employers | Missing employer fields are a **data gap, not fraud** |
| Income | Overstatement, inconsistent reporting | <15% variance is normal; self-employment explains variance |
| Synthetic | Fabricated identity + manufactured credit | Thin files are legitimate for young adults and new immigrants |
| Bust-out | Rapid velocity, unsustainable exposure | Geography alone must not move classification |
| Rings | Shared identifiers, exact-unit reuse | Multi-unit housing and large employers explain overlap |

## See it run

[![Live adjudication on Amazon Bedrock — click to play](media/demo-poster.png)](media/demo.mp4)

**▶ [media/demo.mp4](media/demo.mp4)** — a real run against live Bedrock, recorded at
1920x1080. Not a mockup and not a reconstruction: the latencies, token counts and costs
on screen are the ones that run produced.

## Measured, live on Amazon Bedrock

Fixture APP-1004 (synthetic identity + fraud ring), us-east-1, one run:

| | Measured |
|---|---|
| **End to end** | **37.5 s** — the customer's target is under 60 s |
| Fan-out | 31.4 s, vs **157.6 s** if the eight ran sequentially (**5.0x**) |
| Master synthesis | **6.1 s** on GPT-5.6 Luna (55 s on Claude Sonnet 5) |
| Cost per application | **$0.245**, computed from real token usage |
| Output contract | 21 / 21 keys, exact order, validated |
| Specialists | 8 / 8 returned (7-of-8 still adjudicates by design) |

Nothing above is extrapolated. Re-measure with `evals/bench.py`; if it has not been
measured, this repo shows an em dash.

## Where the latency win comes from

The eight specialists run **concurrently** via `asyncio.gather`, each as a fresh
Strands `Agent` on its own configured model, and the master then synthesizes. So
end-to-end is *slowest agent + synthesis*, not the sum.

Per-model latency on the same short fraud prompt (`maxTokens=120`, us-east-1):

| Model | Latency |
|---|---|
| Claude Haiku 4.5 | 1848 ms |
| Claude Sonnet 5 (effort=low) | 2393 ms |
| Claude Opus 5 (effort=low) | 2924 ms |
| Claude Opus 4.8 | 2990 ms |
| Claude Sonnet 4.6 | 4481 ms |

Note that **Sonnet 4.6 is slower than Opus 4.8** — latency does not track price,
so tiering decisions have to be measured rather than assumed.

**What is still unverified:** a p50/p95 across all 14 fixtures and at concurrency. The
37.5 s figure is one measured run on one application, which is enough to show the target
is reachable and not enough to call it a benchmark. Run `evals/bench.py --live` for that.

## Per-agent model configuration

Snowflake Intelligence pins every agent to one LLM. AgentCore lets each agent use
a right-sized model:

| Tier | Model | Agents |
|---|---|---|
| Light | Claude Haiku 4.5 | dealer, straw |
| Mid | Claude Sonnet 5 | identity, employment, income, bustout |
| Heavy | Claude Opus 5 | synthetic, rings |
| Synthesizer | GPT-5.6 Luna (`bedrock-mantle`) | master adjudication |

The synthesizer is the one place a non-Claude model earned its slot on measurement: it
reads all eight narratives (~28K input tokens) and emits the 21-key object. Claude
Sonnet 5 took 55.2 s and $0.117; GPT-5.6 Luna took 6.1 s and $0.015 for the same verdict
and a valid contract — 9x faster, and it is what brings end-to-end under a minute. The
eight specialists stay on Claude: they are the fraud analysis, and no accuracy
measurement justifies moving them. Override with `BEDROCK_MODEL_SYNTHESIZER`.

Every assignment is env-overridable, and `agents/models.py:TIER_RATIONALE` cites
the prompt text justifying each one (for example, straw's own instruction to keep
no-co-borrower analyses to 2–3 sentences).

### An honest note on cost

**The tier *shape* itself saves $0.00.** Haiku→Sonnet is a $2/$10-per-MTok step
down and Sonnet→Opus is the identical step up, so two light agents fund exactly
two heavy agents at equal token counts. This is asserted by a test so nobody
re-introduces the claim.

The cost reductions that survive arithmetic:

| Lever | Effect | Caveat |
|---|---|---|
| `global.*` instead of `us.*` inference profiles | **−9.09%** exactly | Identical API and model behavior |
| Claude Sonnet 5 introductory rate ($2/$10 vs $3/$15) | −20.13% on the modeled profile | **Reverts to $3/$15 on 2026-09-01** |
| Batch inference | −50% | Opus 5 / Sonnet 4.6 / Haiku 4.5 only; mutually exclusive with prompt caching |
| Precomputing signals | Removes 8 Cortex Analyst messages + 8 warehouse executions per application | Requires the signal layer |

**Prompt caching, measured:** the source prompts are 547–956 estimated tokens, and
the minimum cacheable prefix is 512 (Opus 5), 1024 (Sonnet 5 / 4.6, Opus 4.8) or
4096 (Haiku 4.5). Only the two Opus-tier agents cache today; the rest accept the
cache point silently and pay full price. `agents.models.cache_prefix_report()`
reports this per agent rather than implying a discount.

## Two-layer design: signals, then analysis

Matching the production pattern: governed query results are generated **first**,
then handed to the agents for interpretation. The same agent never both queries
and investigates.

- **Signal layer** (`signal_layer/`) — precomputed signals with their paired
  `_context` example rows and `alerts_fired`, plus the General Rules enforced as
  code (lender join and nickname resolution, address-key normalization,
  `application_id AND hash_lender_id` joins, fake-record filtering with the
  null-safe idiom, no-raw-SSN gating, phone equivalence, retro handling, …).
- **Analysis layer** (`agents/`) — the eight specialists and the master
  synthesizer. They interpret; they never write SQL.

Keeping text-to-SQL out of the per-application hot path matters: a published
production decomposition of a Cortex Agent call attributes ~27 s of a 55 s total to
Cortex Analyst SQL generation alone. For a fixed set of fraud signal groups the
queries are known at design time, so there is no reason to regenerate them per
application. See `docs/semantic-layer-mcp.md`.

## Repo layout

```
prompts/verbatim/        byte-identical customer prompts (9) + orchestration docs (9)
prompts/loader.py        sha256-guarded loading; raises on any drift
signal_layer/
  registry.py            every signal: owner domain(s), verbatim thresholds
  schema.py              SignalPayload / DomainView / AlertFinding
  alert_categories.py    the ten alert-ID categories (166 IDs)
  bands.py               fraud-score / dealer-score bands, PRODUCT decode
  rules/                 one module per General Rule + a coverage catalog
agents/
  models.py              tiering, shared Bedrock client, cache guards
  pricing.py             real rate card incl. cache and batch rates
  fanout.py              asyncio.gather over 8 fresh Agents, real usage capture
  contracts.py           Adjudication — 21 required fields, exact order
  synthesize.py          structured-output synthesis off the verbatim prompt
app/underwriter/main.py  AgentCore Runtime entrypoint (streaming SSE)
app/francis/main.py      AgentCore Runtime entrypoint (interactive router)
fixtures/                applications with full signal coverage + alerts_fired
evals/bench.py           the only code allowed to publish latency or cost
ui/                      Vite + React + Tailwind + shadcn/ui demo
agentcore/               agentcore.json + aws-targets.json for the real CLI
deploy/                  deploy guide, IAM policies, CI gate
```

## Quick start (offline, no AWS)

```bash
MOCK_MODE=1 python3 -m pytest tests/ -q
```

## The demo UI

```bash
cd ui && npm install && npm run build && cd ..
MOCK_MODE=1 python3 -m uvicorn ui.server:app --port 8080
# open http://localhost:8080
```

Pick a scenario and run the underwriting. Eight agent cards light up as each
specialist finishes, showing its model, tier, measured latency, real token usage
and cost. Then the adjudication panel renders the 21-key contract with a
validation indicator, alongside the signal-layer view proving each agent saw only
its own domain.

Fields that have not been measured show an em dash. Token and cost fields are
`null` in MOCK_MODE because no model was invoked — the UI states this rather than
displaying a placeholder figure.

## Measured benchmarks

```bash
# orchestration overhead only — no model latency is measured
python3 -m evals.bench --offline --concurrency 1,4,16 --iterations 40

# real Bedrock; requires explicit opt-in and prints estimated spend first
AGENTCORE_BENCH_ALLOW_LIVE=1 python3 -m evals.bench --live --iterations 3
```

`bench.py` records SDK-reported `latencyMs`, wall clock, TTFT, input/output/cache
tokens, cost from the real rate card, and whether each prompt even met the model's
minimum cacheable prefix. It refuses to print percentiles below a documented
minimum sample count, and every report carries the git sha, region, model IDs and
a `caveats` array stating what was not measured.

## Deploy: the ordered runbook

Written to be followed straight through, by a person or by a coding agent, with no
prior knowledge of this repo. **Do the steps in order.** The order is not stylistic —
each step produces a value the next one needs, and three of the steps fail *silently*
if skipped. Every "why" below is a failure that actually happened here, not a
precaution.

If you only read one thing: **`./deploy/vendor.sh` before every single
`agentcore deploy`**, and **`MOCK_MODE=0`** must be set or the runtime answers
convincingly without ever calling Bedrock.

### Step 0 — prerequisites, and the one that silently corrupts a deploy

```bash
node --version                         # >= 20
npm install -g @aws/agentcore          # 1.0.0-preview.22
agentcore --version                    # must print 1.0.0-preview.22
pip show bedrock-agentcore-starter-toolkit   # MUST be "not found"
python3 -m pip install -r requirements.txt
(cd agentcore/cdk && npm install)      # the vended CDK pins its own tsc
(cd deploy/cdk && npm install)
aws sts get-caller-identity            # account must match agentcore/aws-targets.json
```

`agentcore configure` and `agentcore launch` **do not exist**. The npm CLI rejects
them and exits 1 — the safe failure. The deprecated pip
`bedrock-agentcore-starter-toolkit` installs a *different* binary also named
`agentcore` on which those subcommands parse and then **no-op**, so a script calling
them deploys nothing and reports success. Whichever binary is first on `PATH` decides.
`./deploy/ci.sh` step 0 fails if the pip toolkit is installed at all — run it if you
are unsure. The real verbs are `create` / `add` / `validate` / `deploy` / `status`.

Set your target once, in `agentcore/aws-targets.json`. `agentcore deploy` has no
`--region`, `--profile` or `--account` flag; it reads that file.

### Step 1 — network and database, before any agent exists

```bash
./deploy/one-shot.sh --plan          # synth only, changes nothing, prints the mode
./deploy/one-shot.sh                 # deploy (add --yes for non-interactive)
```

Three modes, selected by what you pass, printed as a `Mode` output so there is no
guessing:

| You have | Command | Stack creates |
|---|---|---|
| nothing | `./deploy/one-shot.sh` | VPC (2 AZs, private-isolated) + Aurora PostgreSQL + interface endpoints |
| a VPC | `--vpc vpc-x --subnets subnet-a,subnet-b` | Aurora inside your VPC + endpoints |
| a VPC and a database | `--vpc vpc-x --subnets a,b --db-sg sg-y` | only the security-group rule and the outputs |

Half-supplied arguments are a **hard error naming the missing value**, never a
defaulted guess: the difference between "create a database" and "use the one you
already have" is not something to be wrong about.

Two things this step decides that are hard to change later:

- **No NAT Gateway.** Interface VPC endpoints exist for every AWS API this system
  calls — `bedrock-runtime`, `bedrock-mantle`, `bedrock-agentcore`,
  `bedrock-agentcore-control`, `bedrock-agentcore.gateway`, `rds-data`,
  `secretsmanager`, `logs`, `sts`, `ecr.api`, `ecr.dkr`, plus a free S3 gateway
  endpoint. So nothing in the data path has a route to the internet. Add
  `--with-nat` only if an agent must reach a third-party API.
- **The Aurora engine version is a context value, not a constant.** Available
  versions differ by region and are withdrawn over time. The default is verified for
  `us-west-2`; elsewhere, check first and pass it:
  ```bash
  aws rds describe-db-engine-versions --engine aurora-postgresql --region <r> \
    --query 'DBEngineVersions[].EngineVersion'
  ./deploy/one-shot.sh --aurora-version 16.13
  ```
  Getting this wrong costs an eleven-minute round trip: CloudFormation builds the
  whole VPC, then fails on the cluster with `Cannot find version X for
  aurora-postgresql`, then rolls all of it back.

The script then writes `agentcore/agentcore.json` with `networkMode: VPC`,
`networkConfig` and the `AURORA_*` variables, and runs `agentcore validate`.

### Step 2 — the reference-data Gateway target, before the agents

```bash
./deploy/reference-tools.sh
```

**This must precede step 3.** `agentcore.json` has to contain the Lambda's ARN, and
the CLI reads that file *before* it synthesises the agent stack — so a function
created by the agent stack cannot be referenced from the config that creates it.

### Step 3 — the ten runtimes, in the VPC from birth

```bash
./deploy/vendor.sh                     # REQUIRED. Not optional. See below.
agentcore validate --json              # must print {"success":true}
agentcore deploy --dry-run
agentcore deploy --target default --yes
agentcore status                       # all ten READY
```

Deployed **already in the VPC**, not deployed public and switched afterwards.
`agentcore deploy` maps resources by CloudFormation logical id, so reconfiguring ten
live runtimes is a second full deploy plus a class of risk that does not exist if the
first deploy is correct. Renaming a resource changes its logical id and **destroys and
recreates** it.

**Why `vendor.sh` is mandatory.** `agentcore package` roots the archive at
`codeLocation`, so `prompts/`, `signal_layer/`, `agents/` and `fixtures/` are absent
from the zip and the container dies at import. It surfaces as
*"Runtime initialization time exceeded... 30s"* — a timeout message for a missing
module. Read CloudWatch, not the CLI error. `vendor.sh` also resolves every dotted
import of a shared package anywhere in the vendored tree, because
`signal_layer.sources.aurora` is imported lazily inside a `SIGNAL_MODE` branch: a
missing submodule otherwise passes CI, passes `validate`, deploys clean, and raises
only on the first aurora-mode invocation.

**Two IAM grants that fail at the last step.** The CLI-generated execution role covers
`bedrock:InvokeModel` on foundation models and inference profiles — the eight Claude
specialists — and nothing else. The GPT-5.6 synthesizer is a separate IAM namespace
needing **two** actions with **different** resources:
`bedrock-mantle:CreateInference` on `arn:aws:bedrock-mantle:*:<account>:project/*`
and `bedrock-mantle:CallWithBearerToken` on `*`. Granting one only reveals the other,
so the first fix looks complete and is not. Both are in
`agentcore/cdk/lib/cdk-stack.ts`; run `npm run build` in `agentcore/cdk` after editing,
because `agentcore deploy` compiles from `dist/`.

### Step 4 — confirm it is really running, not mocking

```bash
./deploy/verify.py                     # 17 checks
./deploy/verify-tracing.py             # reads the agents' `spans` log streams
```

`MOCK_MODE` defaults to `"1"`. A runtime with no env block returns HTTP 200 and a
complete, plausible SSE stream having called Bedrock **zero times**. The tell is in
the data, not the status: the `start` frame carries `"mock_mode": true` and the fan-out
finishes in about a tenth of a second. `agentcore.json` sets `MOCK_MODE=0`; verify it
survived.

`agentcore invoke --prompt '{"application_id":"APP-1004"}'` reports
`"success": true, "response": ""` **while the runtime is streaming correctly** — the
CLI renders text deltas and these frames are JSON objects. Do not debug the runtime
off that empty string; call `bedrock-agentcore.invoke_agent_runtime` with boto3 and
read the bytes.

### Step 5 — connect the real database

```bash
./deploy/connect-rds.sh --probe        # test, change nothing
./deploy/connect-rds.sh --enable       # only if the probe was clean
./deploy/vendor.sh && agentcore deploy --target default --yes
```

`--probe` reports four separate stages — config, connect, signal table, write — so a
cluster without the Data API, a wrong database name, a missing table and a role
without `CREATE` are four different messages rather than one failure. If the Data API
is unavailable, use the VPC-attached Gateway Lambda instead:

```bash
./deploy/connect-rds.sh --vpc subnet-a,subnet-b sg-xxxx
```

`SIGNAL_MODE=aurora` with a broken connection **raises**. It does not fall back to
fixtures — an adjudication computed from 14 demo applications while everyone believed
it came from the staging cluster is the one failure this port must never produce.

### Step 5b — point the analyst tool at your real tables

`DEFAULT_ALLOWLIST` in `deploy/cdk/lambda-rds/main.py` is a **placeholder guess**. Your table
names are yours, so they are supplied at deploy time and never committed:

```bash
./deploy/connect-rds.sh --allow-tables <your_main_table>,<another>
```

It merges rather than replaces, so adding a table later does not revoke the earlier ones. Then
ask the interactive agent for `get_schema` — it reads `information_schema` live and reports which
allowlisted tables actually exist, so a name typo shows up as "not present in this database"
rather than as an empty result.

A read-only console over the same validated path is available on the **local** UI
(`http://127.0.0.1:8080`, chat tab). It is deliberately not on the CloudFront build: that
distribution is anonymously reachable and its Gateway authorizer is `NONE`, so a SQL surface
there would let anyone on the internet query your database. Run the demo locally when you want to
explore, and use the deployed agent for the analyst questions it was built for.

**A note on what the signals need.** If your table is the raw application history rather than
precomputed signals, `SIGNAL_MODE=aurora` has nothing to read — it expects the ~60 registry
signals to already exist. `signal_layer/derive/contract.py` reports exactly what is computable
from a raw application table and what is not:

```
53 of 60 registry signals derivable by SQL aggregation over an application history
 7 must be supplied  (2 dealer model scores, 2 reference-file lookups,
                      credit-bureau inquiries, 2 performance-dependent dealer rates)
14 columns required, 3 optional  -- see COLUMN_CONTRACT for the list and what each unblocks
```

The required columns are described by ROLE, not by name, so answering is a short list rather
than a schema dump. Six of the 60 have more than one defensible window definition and are named
in `NEEDS_CONFIRMATION` — a wrong window produces a plausible number and moves a fraud band
silently, so those are asked rather than assumed.

### Step 6 — the two websites

```bash
PYTHON=$(which python3) ./deploy/deploy-sites.sh \
  -c vpcId=<VpcId> -c subnetIds=<SubnetIds> -c securityGroupIds=<ClientSecurityGroupId>
```

Take those three values from the step 1 stack outputs. They attach the stream proxy —
the calling layer behind CloudFront — to the same subnets, which is what lets it reach
the private database. Its *ingress* is unchanged: a Lambda function URL fronted by
CloudFront OAC, unaffected by VPC attachment.

**The trap:** attaching a Lambda to a VPC **removes its default internet access**.
Without the `bedrock-agentcore` interface endpoint in those subnets, every invocation
hangs to the timeout and reads as a broken runtime. Step 1 creates that endpoint;
that is why these values come from its outputs rather than being typed in.

### Confidential material the deploy needs

`prompts/verbatim/` holds the customer's nine agent prompts and their semantic model,
is **gitignored**, and ships as a zip. Restore it before deploying:

```bash
unzip prompts/verbatim.zip -d prompts/          # or copy the files in
shasum -a 256 prompts/verbatim/FRAUDBOT_SEMANTIC_VIEW.yaml
MOCK_MODE=1 python3 -m pytest tests/test_semantic_model.py -q   # 19 tests
```

Without it the loaders raise `SemanticModelUnavailable`, `tools/usecases` is empty and
says why, and the affected tests **skip** rather than fail. Nothing crashes — but the
use-case tools will not exist, so this is not optional for a real demo.

### Order summary

```
0  prerequisites          agentcore --version, pip toolkit absent, account matches
1  ./deploy/one-shot.sh   VPC + endpoints + Aurora; writes networkMode VPC
2  ./deploy/reference-tools.sh   Gateway Lambda ARN into agentcore.json
3  ./deploy/vendor.sh && agentcore deploy --target default --yes
4  ./deploy/verify.py     confirm live, not mock
5  ./deploy/connect-rds.sh --probe, then --enable
6  ./deploy/deploy-sites.sh with the step-1 VPC outputs
```

Deeper detail, including the one-time CloudWatch Transaction Search setup for GenAI
observability, is in [`deploy/README.md`](deploy/README.md) and
[`deploy/deploy.md`](deploy/deploy.md). The RDS transports and what is *not* verified
are in [`docs/rds-connection.md`](docs/rds-connection.md).

## Status and open questions

Current landed state, remaining work, and the questions we owe the customer
(including a real enum conflict between their prompt file and their process guide)
are tracked in **[CLAUDE.md](CLAUDE.md)**. Read it before changing anything — it
documents several measured traps that look like obvious improvements.

## Documents

| | |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | every box, what is deployed vs designed, and the network decision |
| [`CLAUDE.md`](CLAUDE.md) | the engineering contract; measured traps that look like improvements |
| [`docs/kiro-brief.md`](docs/kiro-brief.md) | paste-ready instructions for a coding agent doing the deploy |
| [`docs/rds-connection.md`](docs/rds-connection.md) | the two database transports, and what is not verified |
| [`docs/semantic-model-reconciliation.md`](docs/semantic-model-reconciliation.md) | the customer's 25 authoritative rules vs. our implementation |
| [`deploy/README.md`](deploy/README.md) | the deploy detail behind the runbook above |

## Reference

- AgentCore getting started: https://catalog.workshops.aws/agentcore-getting-started/en-US
- AgentCore deep dive: https://catalog.workshops.aws/agentcore-deep-dive/en-US
- Strands Agents: https://strandsagents.com
- Prototype to production samples: https://github.com/aws-samples/sample-amazon-bedrock-agentcore-prototype-to-production
