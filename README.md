# Multi-Agent Auto-Loan Fraud Detection on Amazon Bedrock AgentCore

A working demo that reproduces a production Snowflake Cortex multi-agent
auto-loan fraud orchestration (a master orchestrator coordinating eight fraud
specialists) on **Amazon Bedrock AgentCore Runtime** using the
open-source **Strands Agents** framework and Claude on Amazon Bedrock.

> It shows a direct, low-friction path from an existing Cortex agent design to
> AWS, preserving prompts, routing, and the JSON decision contract while gaining
> serverless hosting, IAM-scoped inference, Guardrails, and native observability.

## What it does

Given an auto-loan application, the system produces an underwriting
adjudication: **APPROVE**, **REVIEW AND APPLY STIPULATIONS**, or **DECLINE**,
backed by eight specialist fraud analyses (identity, dealer, straw borrower,
employment, income, synthetic identity, bust-out, and fraud rings). It also
answers targeted questions (for example, "is there income fraud on APP-1003?")
by routing to the right specialist(s).

The design principle carried over from the source prompts: **LOW risk is the
most common outcome**. Specialists validate precomputed signals against benign
`_context` (household sharing, employer scale, population density,
self-employment, data quality) before escalating. Alert volume alone never
drives risk.

## The 8 specialists (agents-as-tools)

| Specialist | Detects |
|-----------|---------|
| Identity  | Misuse/manipulation of real identities (SSN sharing, PII mismatch, volatility) |
| Dealer    | Dealer-*orchestrated* fraud, separated from portfolio/credit risk |
| Straw     | Proxy/nominee co-borrowers, role-switching |
| Employment| Fabricated/shell employers, separated from data gaps |
| Income    | Income overstatement, inconsistent reporting |
| Synthetic | Fabricated identity plus manufactured credit |
| Bust-out  | Rapid velocity, unsustainable exposure |
| Rings     | Shared identifiers, network overlap, exact-unit reuse |

The orchestrator routes and synthesizes. See `architecture.md` for the diagram
and the full mapping from the Snowflake Cortex implementation.

## Parallel synthesizer (the latency win)

In the **underwriting pipeline** the orchestrator is not a sequential router. The eight
specialists run **in parallel**, each returns its fraud-dimension verdict, and
the orchestrator acts as the **synthesizer**, combining the eight analyses into one
strict-JSON adjudication. Outside underwriting (interactive questions) the orchestrator
is the orchestrator that routes a question to one or two specialists as tools.

Parallel fan-out is the core performance story: the source Snowflake system
processed ~4,500 applications in over 48 hours. Running the eight specialists
concurrently and synthesizing brings a single forensic underwriting to
**under a minute** (illustrative nominal figures):

```
sequential (one model, one agent at a time): ~26s per application
parallel on AgentCore (slowest agent + synthesis): ~8.5s per application
```

```bash
MOCK_MODE=1 python app.py --review APP-1004   # per-agent latency view + adjudication
MOCK_MODE=1 python app.py --batch 12          # throughput / daily-capacity estimate
```

## Per-agent model configuration (the Bedrock differentiator)

Snowflake Intelligence locks every agent to one LLM (the source system runs
Claude Sonnet 4.6 across all agents). AgentCore lets each agent use a right-sized
model, which drives both the latency and the cost win (the per-token rate for a
given Claude model is identical to Snowflake's, so savings come from
right-sizing and removing the platform premium, not from cheaper tokens).

| Tier  | Model        | Agents |
|-------|--------------|--------|
| Light | Claude Haiku | dealer, straw |
| Mid   | Claude Sonnet| identity, employment, income, bustout, the orchestrator (synthesizer) |
| Heavy | Claude Opus  | synthetic, rings |

Every assignment is environment-overridable (`BEDROCK_MODEL_LIGHT/MID/HEAVY`,
`BEDROCK_MODEL_SYNTHESIZER`). Print the current map:

```bash
python app.py --models
```

## Two-layer design: signal retrieval separate from fraud analysis

The demo separates the **signal layer** from the **analysis layer**, matching
the pattern that worked best in the source Snowflake environment: relevant SQL
query results are generated first, then handed to the agents for interpretation.
The same agents do not both query and investigate.

- **Signal layer:** `tools/cortex_analyst_mcp.py` represents the Snowflake Cortex
  Analyst semantic view called as an **external MCP tool** through AgentCore
  Gateway. It returns governed, precomputed signals. This is where the SQL
  guardrails baked into the semantic layer are preserved.
- **Analysis layer:** the eight specialists and the orchestrator interpret the retrieved
  signals plus application data. They never write SQL.

See `docs/semantic-layer-mcp.md` for how this addresses the guardrail-migration
question and keeps the Snowflake orchestration intact.

## Repo layout

```
.
├── agentcore_runtime.py     # AgentCore Runtime entrypoint (@app.entrypoint)
├── app.py                   # local CLI runner
├── agents/
│   ├── prompts.py           # specialist prompts + router + synthesizer prompts
│   ├── models.py            # per-agent model tier configuration (Haiku/Sonnet/Opus)
│   ├── pricing.py           # per-model rates + per-agent cost estimation
│   ├── specialists.py       # 8 specialists as Strands @tool functions
│   └── orchestrator.py      # parallel synthesizer + streaming + interactive router
├── ui/
│   ├── server.py            # FastAPI + SSE server for the live UI
│   └── index.html           # live agent grid, verdict, spend, AgentCore-vs-Snowflake
├── observability/
│   ├── metrics.py           # per-agent CloudWatch metrics emitter
│   ├── cloudwatch_dashboard.json  # per-agent spend/latency/tokens dashboard
│   └── create_dashboard.py  # PutDashboard helper
├── tools/
│   ├── application_data.py  # data-access tool (application record retrieval)
│   └── cortex_analyst_mcp.py# signal layer: Cortex Analyst semantic view via MCP
├── data/
│   └── applications.py      # 5 synthetic applications (no real PII)
├── deploy/
│   ├── deploy.md            # agentcore configure/launch guide
│   └── iam_policy.json      # execution-role permissions
├── docs/
│   └── semantic-layer-mcp.md# guardrail migration + MCP signal-layer design
├── tests/test_local.py      # offline smoke tests
├── architecture.md
└── requirements.txt
```

## Quick start (offline, no AWS needed)

Runs the full orchestration with a deterministic, signal-driven mock. Zero
dependencies to install, zero Bedrock cost, ideal for a first walkthrough.

```bash
MOCK_MODE=1 python app.py --list
MOCK_MODE=1 python app.py --models              # per-agent model configuration
MOCK_MODE=1 python app.py --review APP-1004     # parallel pipeline + latency view (DECLINE)
MOCK_MODE=1 python app.py --review APP-1001     # LOW RISK / APPROVE
MOCK_MODE=1 python app.py --batch 12            # throughput / daily-capacity estimate
MOCK_MODE=1 python app.py "Is there income fraud on APP-1003?"
MOCK_MODE=1 python app.py --signal-demo APP-1004 # signal layer (MCP) then parallel analysis
MOCK_MODE=1 python app.py --demo                # pipeline on all 5 applications
MOCK_MODE=1 python tests/test_local.py          # smoke tests
```

## Live against Amazon Bedrock

```bash
pip install -r requirements.txt
export AWS_REGION=us-east-1
export AWS_PROFILE=your-bedrock-profile
python app.py --review APP-1004
```

Then deploy to AgentCore Runtime. See `deploy/deploy.md`.

## Live UI (see the agents fire in parallel)

```bash
pip install -r requirements.txt
MOCK_MODE=1 python app.py --serve      # offline, or drop MOCK_MODE for live Bedrock
# open http://127.0.0.1:8080
```

Pick a scenario and hit **Run underwriting**. Eight agent cards light up as each
specialist finishes (staggered so the parallel fan-out is visible), each showing
its model tier, latency, and cost. Then the orchestrator's verdict appears
alongside an **AgentCore-vs-Snowflake** latency bar and a **live spend** meter
with a monthly projection at 100,000 applications/day.

## Cost per agent

```bash
MOCK_MODE=1 python app.py --costs 100000
```

Prints the per-agent cost of one adjudication and a monthly projection. Costs
come from `agents/pricing.py` (illustrative token footprints offline; real
Bedrock usage live). The savings vs. a single-LLM setup come from right-sizing
each agent's model, not from cheaper tokens (rates are identical).

## Spend dashboard on CloudWatch

```bash
python observability/create_dashboard.py --region us-east-1
export EMIT_CLOUDWATCH_METRICS=1 AWS_REGION=us-east-1
python app.py --batch 20               # populate per-agent metrics
```

Creates a dashboard that breaks down **spend, tokens, and latency by agent**.
See `observability/README.md`.

## Demo applications

| App       | Scenario                              | Expected outcome |
|-----------|---------------------------------------|------------------|
| APP-1001  | Clean first-time buyer                | LOW / APPROVE |
| APP-1002  | Spousal co-borrower, thin file        | LOW / APPROVE |
| APP-1003  | Income overstatement (~31%), ambiguous| MEDIUM / REVIEW + STIPULATE |
| APP-1004  | Synthetic identity plus fraud ring    | HIGH / DECLINE |
| APP-1005  | Bust-out velocity pattern             | MEDIUM to HIGH / REVIEW |

All data is synthetic. SSNs are fake hashes; no raw SSN is ever surfaced
(preserving the source Rule 23).

## Notes and honest framing

- This is a **reference demo**, not production code. It uses a small synthetic
  dataset and a mock path for offline runs.
- Live adjudication quality depends on the model and the real signal data; the
  mock path is deterministic and intentionally conservative for repeatable demos.
- Pin `BEDROCK_MODEL_ID` to a specific model version for reproducible decisions.

## Reference resources

- AgentCore getting started workshop: https://catalog.workshops.aws/agentcore-getting-started/en-US
- AgentCore deep dive workshop: https://catalog.workshops.aws/agentcore-deep-dive/en-US
- Prototype to production samples: https://github.com/aws-samples/sample-amazon-bedrock-agentcore-prototype-to-production
