"""
Signal layer: the Snowflake Cortex Analyst semantic view, called as an EXTERNAL
MCP tool through Amazon Bedrock AgentCore Gateway.

WHY THIS EXISTS
---------------
The hardest part of moving this system off Snowflake is not the agents, it is
the SQL guardrails baked into the Cortex semantic layer (join rules, lender
name normalization, fake-record filtering, "never surface raw SSN", the alert
groupings, and so on). Rebuilding all of that in application code would be
error-prone and would drift from the source of truth.

The approach demonstrated here keeps that logic where it already lives. The
Cortex Analyst semantic view stays in Snowflake and is exposed to the agents as
an external tool over the Model Context Protocol (MCP):

    Agents (Bedrock AgentCore Runtime)
        |  MCP (tools/list, tools/call)
        v
    AgentCore Gateway  --(SigV4 / OAuth)-->  Snowflake Cortex Analyst MCP server
                                             (semantic view + governed SQL + guardrails)

This also enforces the SEPARATION OF LAYERS that worked best in Snowflake:

    Phase 1 - SIGNAL LAYER (this module): retrieve governed query results /
              precomputed signals from the semantic view. No fraud judgement.
    Phase 2 - ANALYSIS LAYER (the specialists + the orchestrator): interpret the
              retrieved signals plus application data and decide. No SQL.

The same agent never both queries and investigates.

RUNNING MODES
-------------
- MOCK_MODE=1 (default for the demo): returns a signal envelope built from the
  local synthetic dataset, framed exactly as a Cortex Analyst MCP response would
  be (semantic view name, the natural-language question, the generated SQL, the
  guardrails the semantic layer applied, and the returned signal rows).
- Live: set CORTEX_ANALYST_MCP_URL to an AgentCore Gateway MCP endpoint that
  fronts the Cortex Analyst semantic view. build_cortex_analyst_mcp_client()
  shows the wiring with the Strands MCP client. Nothing in the analysis layer
  changes between the two modes.
"""

from __future__ import annotations
import os
from typing import Any

from tools.application_data import fetch_application_record

MOCK_MODE = os.environ.get("MOCK_MODE", "0") == "1"
CORTEX_ANALYST_MCP_URL = os.environ.get("CORTEX_ANALYST_MCP_URL", "")

# The semantic view the fraud agents query in Snowflake. Named here so the
# signal envelope is self-describing regardless of backend.
SEMANTIC_VIEW = "FRAUD_CONSORTIUM_SEMANTIC_VIEW"

# The SQL guardrails that live in the semantic layer and MUST be preserved in
# migration. These are surfaced in the signal envelope so the demo can show
# what the semantic layer enforced on the platform's behalf.
SEMANTIC_LAYER_GUARDRAILS = [
    "Never surface raw SSN values; only hashed SSN references are returned (Rule 23).",
    "Join all_records / consortium tables on application_id AND hash_lender_id.",
    "Exclude fake/test records (fake emails, common-consortium VINs/phones, TEST markers).",
    "Normalize lender names (uppercase, strip spaces, resolve nicknames) before matching.",
    "Only consider active dealers (consortium apps in last year > 0).",
    "Treat invalid credit-report values (<=0, 9999) as null.",
]


def retrieve_signals(application_id: str, question: str | None = None) -> dict[str, Any]:
    """
    Phase 1 - the signal-layer call.

    Ask the Cortex Analyst semantic view (via MCP) for the governed signal set
    for one application. Returns a self-describing "signal envelope":

        {
          "application_id": ...,
          "semantic_view": ...,
          "question": <natural-language question sent to Cortex Analyst>,
          "generated_sql": <SQL Cortex Analyst produced against the view>,
          "guardrails_applied": [...],
          "signals": {...},          # the governed query results
          "context": {...},          # the *_context columns for validation
          "source": "cortex-analyst-mcp" | "cortex-analyst-mcp (mock)"
        }

    This function does NOT make any fraud judgement. It only retrieves data.
    """
    question = question or (
        f"Return the precomputed fraud signals and their _context fields for "
        f"application {application_id} from {SEMANTIC_VIEW}."
    )

    if MOCK_MODE or not CORTEX_ANALYST_MCP_URL:
        return _mock_retrieve(application_id, question)

    return _live_retrieve(application_id, question)


# ---------------------------------------------------------------------------
# Mock signal layer (offline). Builds the envelope from the synthetic dataset.
# ---------------------------------------------------------------------------
def _mock_retrieve(application_id: str, question: str) -> dict[str, Any]:
    record = fetch_application_record(application_id)
    if record is None:
        return {
            "application_id": application_id,
            "semantic_view": SEMANTIC_VIEW,
            "question": question,
            "generated_sql": "",
            "guardrails_applied": SEMANTIC_LAYER_GUARDRAILS,
            "signals": {},
            "context": {},
            "source": "cortex-analyst-mcp (mock)",
            "error": f"No rows returned for {application_id}.",
        }

    generated_sql = (
        "SELECT signals.*, ctx.*\n"
        f"FROM {SEMANTIC_VIEW} v\n"
        "JOIN tbl_afa_alerts_clean a\n"
        "  ON a.application_id = v.application_id\n"
        "  AND a.hash_lender_id = v.hash_lender_id\n"
        f"WHERE v.application_id = '{application_id}'\n"
        "  AND (v.borr_email <> ALL (SELECT borr_email FROM __fake_emails)\n"
        "       OR v.borr_email IS NULL)\n"
        "  AND v.consortium_number_of_apps_last_1_year > 0;"
    )

    return {
        "application_id": application_id,
        "semantic_view": SEMANTIC_VIEW,
        "question": question,
        "generated_sql": generated_sql,
        "guardrails_applied": SEMANTIC_LAYER_GUARDRAILS,
        "signals": record.get("signals", {}),
        "context": record.get("context", {}),
        "source": "cortex-analyst-mcp (mock)",
    }


# ---------------------------------------------------------------------------
# Live signal layer (AgentCore Gateway -> Cortex Analyst MCP server).
# Guarded import so the offline demo needs no MCP/strands dependencies.
# ---------------------------------------------------------------------------
def build_cortex_analyst_mcp_client():
    """
    Construct a Strands MCP client pointed at the AgentCore Gateway endpoint
    that fronts the Snowflake Cortex Analyst semantic view.

    In production the Gateway target is the Cortex Analyst MCP server; the
    Gateway handles auth (SigV4 / OAuth) and exposes it as MCP tools that the
    agents can call. See docs/semantic-layer-mcp.md.
    """
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient

    if not CORTEX_ANALYST_MCP_URL:
        raise RuntimeError("Set CORTEX_ANALYST_MCP_URL to the AgentCore Gateway MCP endpoint.")

    return MCPClient(lambda: streamablehttp_client(CORTEX_ANALYST_MCP_URL))


def _live_retrieve(application_id: str, question: str) -> dict[str, Any]:
    """Call the real Cortex Analyst semantic view via the MCP gateway."""
    client = build_cortex_analyst_mcp_client()
    with client:
        # Tool name as published by the Cortex Analyst MCP server / Gateway target.
        result = client.call_tool_sync(
            tool_use_id=f"signal-{application_id}",
            name="cortex_analyst_query",
            arguments={"semantic_view": SEMANTIC_VIEW, "question": question},
        )
    # The Gateway returns the governed rows; the analysis layer consumes them
    # unchanged. Envelope shape matches the mock so callers are backend-agnostic.
    payload = result.get("structuredContent", result) if isinstance(result, dict) else {}
    return {
        "application_id": application_id,
        "semantic_view": SEMANTIC_VIEW,
        "question": question,
        "generated_sql": payload.get("generated_sql", ""),
        "guardrails_applied": SEMANTIC_LAYER_GUARDRAILS,
        "signals": payload.get("signals", {}),
        "context": payload.get("context", {}),
        "source": "cortex-analyst-mcp",
    }
