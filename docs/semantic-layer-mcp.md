# Preserving the Snowflake semantic-layer guardrails via MCP

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
      |  MCP: tools/list, tools/call
      v
Amazon Bedrock AgentCore Gateway
      |  target: Cortex Analyst MCP server  (auth: SigV4 / OAuth)
      v
Snowflake Cortex Analyst  ->  FRAUD_CONSORTIUM_SEMANTIC_VIEW
      (natural language -> governed SQL -> guardrails -> rows)
```

AgentCore Gateway turns an existing API or MCP server into a managed set of MCP
tools with auth, so the agents get a governed `cortex_analyst_query` tool
without any credentials on the client and without re-implementing SQL logic.

`tools/cortex_analyst_mcp.py` implements this boundary. In `MOCK_MODE` it
returns a signal envelope built from the synthetic dataset (framed exactly like
a Cortex Analyst MCP response: semantic view, question, generated SQL,
guardrails applied, and the returned signal rows). Set `CORTEX_ANALYST_MCP_URL`
to a Gateway endpoint to switch to the live path; the analysis layer does not
change.

## Separate the signal layer from the analysis layer

A second recommendation from the source environment: the best results came from
**generating the SQL query results first (the signal layer), then handing them
to the agents for interpretation (the analysis layer)**. The same agent should
not both query and investigate. This is especially true for general fraud-agent
use (ad hoc investigation) as opposed to the fixed underwriting process.

This demo enforces that split:

| Phase | Layer    | Responsibility                                             | Writes SQL? |
|-------|----------|------------------------------------------------------------|-------------|
| 1     | Signal   | Cortex Analyst semantic view via MCP retrieves governed signals | Yes (in Snowflake) |
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

## Live wiring sketch (Strands MCP client)

```python
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

cortex = MCPClient(lambda: streamablehttp_client(CORTEX_ANALYST_MCP_URL))
with cortex:
    tools = cortex.list_tools_sync()          # includes cortex_analyst_query
    signals = cortex.call_tool_sync(
        tool_use_id="signal-APP-1004",
        name="cortex_analyst_query",
        arguments={"semantic_view": "FRAUD_CONSORTIUM_SEMANTIC_VIEW",
                   "question": "signals + _context for application APP-1004"},
    )
# hand `signals` to the specialists; they interpret, they do not query
```

## Reference resources

- AgentCore getting started workshop: https://catalog.workshops.aws/agentcore-getting-started/en-US
- AgentCore deep dive workshop: https://catalog.workshops.aws/agentcore-deep-dive/en-US
- Prototype to production samples: https://github.com/aws-samples/sample-amazon-bedrock-agentcore-prototype-to-production
- MCP proxy for AWS (SigV4 bridge to AgentCore Gateway): https://github.com/aws/mcp-proxy-for-aws
