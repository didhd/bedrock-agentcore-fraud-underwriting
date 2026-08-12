# Preserving the Snowflake semantic-layer guardrails via MCP

> **CORRECTED 2026-08-12.** Three claims below were wrong and are fixed in place; the
> migration posture they argue for is unchanged and still right. What was wrong:
>
> 1. **`cortex_analyst_query(semantic_view=..., question=...)` does not exist.** Snowflake's
>    managed MCP server has user-defined tool names (the fixed part is
>    `type: "CORTEX_ANALYST_MESSAGE"`), takes a single `message` argument, and binds the
>    semantic view server-side.
> 2. **SigV4 cannot authenticate to Snowflake.** SigV4 is verified by AWS services --
>    AgentCore Gateway and Runtime, API Gateway, Lambda function URLs -- and Snowflake is
>    not one of them. Snowflake's own AgentCore guide uses a **programmatic access token
>    (PAT)** as a bearer header. That is what `signal_layer/sources/snowflake.py` sends.
> 3. **Cortex Analyst returns generated SQL, not rows.** `/api/v2/cortex/analyst/message`
>    answers with a SQL statement; a second call to `/api/v2/statements` executes it. A
>    one-call design is the specific error that made the previous integration fictional.
>
> Implemented instead: `SIGNAL_MODE` (`fixtures` | `cortex` | `aurora`) on
> `app.runtime_support.build_signal_payload`, with the PAT in one Secrets Manager secret.
> `./deploy/connect-snowflake.sh` writes it, grants the runtimes read access, and PROBES it
> before offering to switch modes -- so the customer supplies a key and finds out in seconds
> whether it works. `cortex` mode RAISES rather than falling back to fixtures: an
> adjudication computed from demo data while everyone believed it came from the consortium
> is the one failure this port must not be able to produce.

## The challenge

The biggest risk in moving this fraud system off Snowflake is not porting the
agents. It is replicating the **SQL guardrails built into the Cortex semantic
layer**. Those guardrails encode a large amount of institutional knowledge, for
example:

- Never surface raw SSN values; only hashed SSN references (Rule 23).
- Join `all_records` / consortium tables on `application_id` AND `hash_lender_id`.
- Exclude fake and test records (fake emails, common-consortium VINs and phones,
  any `TEST` markers, 10 identical same-day apps).
- Normalize lender names (uppercase, strip spaces, resolve nicknames) before matching.
- Standardize address keys (building number + first chars of street + ZIP) to
  avoid false-positive address matches.
- Only consider active dealers (`consortium_number_of_apps_last_1_year > 0`).
- Treat invalid credit-report values (`<= 0`, `9999`) as null.
- The alert groupings (identity, income, employment, straw, dealer, default,
  collateral, bust-out) and product-code decoding.

Rebuilding all of that in application code would be error-prone and would drift
from the source of truth. Bill and Darren are likely to stress keeping the
existing orchestration and guardrails intact.

## The approach: keep the semantic view in Snowflake, call it over MCP

Do not rebuild the semantic layer. Expose the **Snowflake Cortex Analyst
semantic view as an external tool over the Model Context Protocol (MCP)**, and
front it with **Amazon Bedrock AgentCore Gateway**. The governed SQL and its
guardrails stay in Snowflake; the agents on AgentCore call it as a tool.

```
Agents (Bedrock AgentCore Runtime, Strands)
      |  SIGNAL_MODE=cortex, through build_signal_payload
      v
signal_layer/sources/snowflake.py   (PAT from Secrets Manager)
      |  1. POST /api/v2/cortex/analyst/message   -> GENERATED SQL
      |  2. POST /api/v2/statements               -> rows
      v
Snowflake Cortex Analyst  ->  FRAUD_CONSORTIUM_SEMANTIC_VIEW
      (natural language -> governed SQL -> guardrails -> rows)

A Gateway target is the right shape when the tool is called BY a model. This is called by
the pipeline before any agent runs, so it is a direct read rather than a tool -- which also
keeps "agents interpret; they never query" intact. The Gateway is already deployed and
serves reference data (the alert dictionary); a Cortex tool for ad-hoc analyst questions
would be added there, not here.
```

AgentCore Gateway turns an existing API or MCP server into a managed set of MCP tools with
auth. It is the right shape for a tool a MODEL calls, and the deployed gateway
(`ppfraud-reference`) does exactly that for reference data. It is the wrong shape for the
per-application signal read, which happens BEFORE any agent runs -- routing it through a
model-facing tool would put a tool call in the hot path and hand an agent the ability to
query, which the signal/analysis split exists to prevent.

`signal_layer/sources/snowflake.py` implements the boundary instead, selected by
`SIGNAL_MODE`. The PAT lives in one Secrets Manager secret; no credential reaches the agent
side, and no SQL logic is re-implemented. The analysis layer does not change -- the
specialists receive the same `SignalPayload` either way, which is the property that makes
this a config change rather than a migration.

(`tools/cortex_analyst_mcp.py` is the previous, fictional attempt at this boundary and is
superseded. It still calls a tool signature that does not exist; see CLAUDE.md's TODO.)

## Separate the signal layer from the analysis layer

A second recommendation from the source environment: the best results came from
**generating the SQL query results first (the signal layer), then handing them
to the agents for interpretation (the analysis layer)**. The same agent should
not both query and investigate. This is especially true for general fraud-agent
use (ad hoc investigation) as opposed to the fixed underwriting process.

This demo enforces that split:

| Phase | Layer    | Responsibility                                             | Writes SQL? |
|-------|----------|------------------------------------------------------------|-------------|
| 1     | Signal   | Cortex Analyst semantic view retrieves governed signals (2 calls) | Yes (in Snowflake) |
| 2     | Analysis | Specialists + the orchestrator interpret signals + application data | No |

See it end to end:

```bash
MOCK_MODE=1 python app.py --signal-demo APP-1004
```

Phase 1 prints the semantic view queried, the natural-language question, the
generated SQL, the guardrails the semantic layer enforced, and the retrieved
signal count. Phase 2 runs the eight specialists and the orchestrator over those signals
and returns the adjudication.

## Why this is the right migration posture

- **No guardrail rewrite.** The governed SQL stays in Snowflake, so nothing is
  lost in translation and there is a single source of truth.
- **Orchestration preserved.** the orchestrator and the eight specialists are unchanged;
  only their data source becomes an MCP tool.
- **Clean trust boundary.** The Gateway handles auth and turns the semantic view
  into a governed tool; no Snowflake credentials live on the agent side.
- **Layer separation improves quality.** Retrieval and interpretation are
  decoupled, matching what worked best in the source environment.
- **Incremental.** A team can start with Cortex Analyst as the MCP signal
  source and later move individual signal queries to AWS-native stores if they
  choose, without touching the analysis layer.

## Live wiring: what actually runs

```bash
# One command. Prompts for four values, one of them the PAT.
./deploy/connect-snowflake.sh

#   1. writes {account_url, pat, semantic_view, warehouse} to one Secrets Manager secret
#   2. grants every agent runtime secretsmanager:GetSecretValue on it
#   3. PROBES: authenticates, runs `select current_account()`, and asks Cortex Analyst
#      about the semantic view -- so a wrong warehouse or an expired PAT is one clear
#      error, seconds after the key is pasted
#   4. only then offers to switch modes

./deploy/connect-snowflake.sh --enable      # SIGNAL_MODE=cortex on every runtime
./deploy/vendor.sh && agentcore deploy --target default --yes
./deploy/connect-snowflake.sh --probe       # re-test the stored key any time
./deploy/connect-snowflake.sh --disable     # back to the committed fixtures
```

```python
# signal_layer/sources/snowflake.py, the two calls that matter
analyst = ask_analyst(f"Return every fraud signal and its paired _context field for {app_id}")
rows = run_sql(analyst["sql"])        # a SECOND call; call one returned SQL, not rows
# `rows` carries the guardrails the semantic view applied. The specialists interpret them
# and never query.
```

**Untested against a live account.** There is no Snowflake on the AWS side of this, so the
first real round trip happens on the customer's key. That is precisely what `--probe` is
for, and why the projection in `build_cortex_payload` tolerates several plausible column
names rather than assuming one: the semantic view decides its own, and being wrong there
should surface as a shape mismatch on the first probe rather than as a wrong adjudication.

## Reference resources

- AgentCore getting started workshop: https://catalog.workshops.aws/agentcore-getting-started/en-US
- AgentCore deep dive workshop: https://catalog.workshops.aws/agentcore-deep-dive/en-US
- Prototype to production samples: https://github.com/aws-samples/sample-amazon-bedrock-agentcore-prototype-to-production
- MCP proxy for AWS (SigV4 bridge to AgentCore Gateway): https://github.com/aws/mcp-proxy-for-aws
