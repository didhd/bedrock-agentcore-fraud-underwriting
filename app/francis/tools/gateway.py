"""The AgentCore Gateway as an MCP tool source for Francis.

WHAT IT ADDS AND WHY IT IS NOT AN @tool

The customer named this gap in their own words, describing what an underwriter faces:

    "we send back a score, 3 reason codes ... and then we have about 150 alerts. And so
     imagine the average underwriter saying I don't -- what is alert number 123? And what
     is alert number 124? He knows it by the back of his hand, but they are not gonna know
     that."

So an alert dictionary. It sits behind a Gateway rather than in `tools/specialists.py`
because reference data is a different concern from fraud reasoning: the Gateway MCP-ifies a
Lambda another team could own, any agent can discover it, and none of them own the code.
The same gateway is where an Aurora or Cortex signal target goes during migration.

IT DOES NOT BREACH "AGENTS INTERPRET; THEY NEVER QUERY"

That rule is about APPLICATION signals -- a specialist must not widen its own domain view,
and no agent may put a text-to-SQL round trip in the per-application path. These tools
serve alert DEFINITIONS: identical for every application, no borrower data, answering "what
does 164 mean" and never "what fired on APP-1004". The eight specialists still receive only
their own projected signals and still have no data tool.

AND IT IS NOT SUBJECT TO THE 2-TOOL CAP

`ToolCapHook` counts only the eight specialist tools, deliberately: the customer's
"MAXIMUM 2 tools per query" is a rule about specialist FAN-OUT, written for latency. A
dictionary lookup is not fan-out, and capping it would make Francis choose between
answering "what is alert 164" and consulting a specialist.

ENV-GATED, LIKE MEMORY, FOR THE SAME REASON

`AGENTCORE_GATEWAY_PPFRAUD_REFERENCE_URL` is injected by AgentCore Runtime after deploy and
is absent under `agentcore dev`. Returning None there keeps local runs working; attaching a
client to a URL that does not exist would fail every local invocation.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

log = logging.getLogger("pp.francis.gateway")

#: The variable name the CDK derives from the gateway's own name: the gateway is
#: `ppfraud-reference`, upper-cased with hyphens replaced by underscores. Getting this
#: wrong is silent -- the client is simply never built and Francis loses two tools with no
#: error anywhere -- so the name is asserted by a test rather than trusted.
GATEWAY_URL_ENV: Final[str] = "AGENTCORE_GATEWAY_PPFRAUD_REFERENCE_URL"
GATEWAY_AUTH_ENV: Final[str] = "AGENTCORE_GATEWAY_PPFRAUD_REFERENCE_AUTH_TYPE"


def gateway_url() -> str | None:
    """The deployed gateway's MCP endpoint, or None when it is not wired."""
    return os.environ.get(GATEWAY_URL_ENV) or None


def gateway_auth_type() -> str:
    """``NONE`` / ``AWS_IAM`` / ``CUSTOM_JWT``, as the CDK reports it.

    Read rather than assumed because it changes what a client must do: under ``AWS_IAM``
    the request has to be SigV4-signed, which a plain streamable-HTTP MCP client does not
    do. The gateway is declared ``NONE`` today, and that choice is deliberate and narrow --
    it serves alert definitions with no borrower information in them. A target carrying
    application data would need real authorization first.
    """
    return (os.environ.get(GATEWAY_AUTH_ENV) or "NONE").upper()


def build_gateway_client() -> Any | None:
    """An MCPClient for the reference gateway, or None when it is not available.

    Never raises. Losing two reference tools is a degraded feature; an analyst's question
    is not worth failing over, and the same reasoning governs the memory session manager.
    """
    url = gateway_url()
    if not url:
        log.info(
            "%s is not set, so the reference gateway is unavailable. This is normal under "
            "`agentcore dev`: the CLI injects gateway URLs from deployed state.",
            GATEWAY_URL_ENV,
        )
        return None

    auth = gateway_auth_type()
    if auth != "NONE":
        # Stated rather than attempted. A streamable-HTTP MCP client sends an unsigned
        # request, so under AWS_IAM the gateway would reject every call -- and the failure
        # would look like the tools not existing.
        log.warning(
            "gateway auth is %s, but this client sends unsigned requests. Tools will be "
            "rejected until the client signs SigV4 or the authorizer is NONE.",
            auth,
        )

    try:
        from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415
        from strands.tools.mcp.mcp_client import MCPClient  # noqa: PLC0415

        return MCPClient(lambda: streamablehttp_client(url))
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "reference gateway at %s could not be wired: %s: %s. Continuing without its "
            "tools.",
            url,
            type(exc).__name__,
            exc,
        )
        return None
