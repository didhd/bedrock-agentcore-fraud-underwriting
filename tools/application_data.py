"""
Data-access tool for the fraud demo.

In production this is the fraud platform's Snowflake Cortex Analyst + semantic
view executing governed SQL over the consortium tables. For a self-contained,
runnable demo we back it with the local synthetic dataset in data/applications.py.

The public surface is deliberately narrow (fetch-by-id + list) so that swapping
the backend for a real Cortex Analyst / Athena / RDS call is a one-function change
and the agent code above it does not have to change.
"""

from __future__ import annotations
import json
from typing import Any

from data.applications import get_application, list_application_ids


def fetch_application_record(application_id: str) -> dict[str, Any] | None:
    """
    Retrieve a full application record (core fields + signals + context).

    Swap the body of this function to call Snowflake Cortex Analyst, Amazon
    Athena, or an RDS/Aurora query in a real deployment. Everything above this
    layer (the specialist and orchestrator agents) stays unchanged.
    """
    return get_application(application_id)


def available_application_ids() -> list[str]:
    """List the application IDs available in the demo dataset."""
    return list_application_ids()


def render_application_for_agent(record: dict[str, Any], domain: str | None = None) -> str:
    """
    Format an application record into the compact text block a specialist agent
    receives. Includes core fields, all precomputed signals, and the *_context
    fields the agent must validate against.

    `domain` is informational only — every specialist sees the full signal set
    plus context, mirroring the production behavior where each agent validates
    against the same underlying application data.
    """
    core = {k: v for k, v in record.items() if k not in ("signals", "context")}
    block = [
        "=== APPLICATION CORE FIELDS ===",
        json.dumps(core, indent=2),
        "",
        "=== PRECOMPUTED FRAUD SIGNALS ===",
        json.dumps(record.get("signals", {}), indent=2),
        "",
        "=== CONTEXT FIELDS (validate signals against these) ===",
        json.dumps(record.get("context", {}), indent=2),
    ]
    if domain:
        block.insert(0, f"(Requested analysis domain: {domain})\n")
    return "\n".join(block)
