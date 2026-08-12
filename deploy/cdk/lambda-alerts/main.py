"""Alert-definition lookup, exposed to agents through AgentCore Gateway.

WHY THIS TOOL AND NOT SOME OTHER ONE

The customer named this gap themselves, describing what an underwriter sees today:

    "we send back a score, 3 reason codes like you normally would in a regular credit
     score, and then we have about 150 alerts. And so imagine the average underwriter
     saying I don't -- what is alert number 123? And what is alert number 124? He knows it
     by the back of his hand, but they are not gonna know that."

So the Gateway target is an alert dictionary. An analyst asks "what is alert 164" and
Francis answers from this lookup instead of the analyst going to find someone who knows.

WHY IT DOES NOT VIOLATE "AGENTS INTERPRET; THEY NEVER QUERY"

That rule is about APPLICATION signals: a specialist must not be able to widen its own
domain view, and no agent may put a text-to-SQL round trip in the per-application path.
This serves REFERENCE data -- what an alert id means, which categories it belongs to --
which is the same for every application and carries no borrower information at all. It
answers "what does 164 mean", never "what fired on APP-1004".

WHY THE DATA IS GENERATED, NOT TYPED

`alerts.json` is produced from `signal_layer.alert_categories.CATEGORY_ALERTS`, the same
module the eight specialists' entitlements come from. A hand-typed copy would let this
lookup and the agents disagree about what an alert means, and the agent would be the one
that sounded authoritative.

THE GATEWAY CALLING CONVENTION, WHICH IS NOT THE USUAL LAMBDA ONE

AgentCore Gateway passes tool parameters **directly in the event**, not inside
`event["body"]`. The tool name arrives as
`context.client_context.custom["bedrockAgentCoreToolName"]` shaped
`<TargetName>___<tool_name>`. This function serves more than one tool, so it reads that
name rather than assuming.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA = json.loads((Path(__file__).parent / "alerts.json").read_text(encoding="utf-8"))
ALERTS: dict[str, Any] = _DATA["alerts"]
CATEGORIES: dict[str, list[int]] = _DATA["categories"]


def _tool_name(context: Any) -> str:
    """The bare tool name, with the Gateway's ``<Target>___`` prefix stripped."""
    try:
        raw = context.client_context.custom["bedrockAgentCoreToolName"]
    except Exception:
        return ""
    return raw.split("___")[-1] if raw else ""


def describe_alert(event: dict[str, Any]) -> dict[str, Any]:
    """One alert id -> its categories, and which specialists are entitled to it."""
    raw = str(event.get("alert_id", "")).strip()
    # Tolerate "164", 164, and "alert 164": a model filling a parameter from prose does all
    # three, and rejecting two of them would look like the alert not existing.
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return {
            "found": False,
            "error": f"no alert id in {raw!r}. Expected a number such as 164.",
        }

    entry = ALERTS.get(digits)
    if entry is None:
        return {
            "found": False,
            "alert_id": int(digits),
            "error": (
                f"alert {digits} is not in the customer's ten General Rule 3 categories. "
                f"{_DATA['alert_count']} ids are categorised; an uncategorised id is "
                "covered by the disregard rule rather than being unknown."
            ),
        }

    return {
        "found": True,
        "alert_id": entry["id"],
        "categories": entry["categories"],
        "multi_category": len(entry["categories"]) > 1,
        "note": (
            "An alert belonging to more than one category is entitled to more than one "
            "specialist, which is why the same id can appear in two domains' views."
            if len(entry["categories"]) > 1
            else "Single-category alert."
        ),
        "source": _DATA["generated_from"],
    }


def list_alert_categories(event: dict[str, Any]) -> dict[str, Any]:
    """The ten categories, with their alert ids. Optionally one category."""
    wanted = str(event.get("category", "")).strip().lower()
    if wanted:
        for name, ids in CATEGORIES.items():
            if name.lower() == wanted:
                return {"found": True, "category": name, "alert_ids": ids, "count": len(ids)}
        return {
            "found": False,
            "error": f"unknown category {wanted!r}. Known: {', '.join(sorted(CATEGORIES))}",
        }
    return {
        "found": True,
        "category_count": len(CATEGORIES),
        "alert_count": _DATA["alert_count"],
        "categories": {name: len(ids) for name, ids in CATEGORIES.items()},
        "note": _DATA["note"],
    }


_TOOLS = {
    "describe_alert": describe_alert,
    "list_alert_categories": list_alert_categories,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Gateway entry point.

    Returns the tool result DIRECTLY, not wrapped in `{"statusCode", "body"}`. The Gateway
    hands the return value to the agent as the tool result, so an API-Gateway-shaped
    envelope would make the model read the HTTP scaffolding as the answer.
    """
    name = _tool_name(context)
    tool = _TOOLS.get(name)
    if tool is None:
        return {
            "found": False,
            "error": (
                f"unknown tool {name!r}. This target serves: {', '.join(sorted(_TOOLS))}."
            ),
        }
    return tool(event or {})
