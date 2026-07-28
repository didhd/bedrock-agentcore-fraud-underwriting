"""
Shared plumbing for the two AgentCore runtimes: payload parsing, the signal-layer
seam, per-agent telemetry, and the 21-key adjudication contract.

Both ``app/underwriter/main.py`` and ``app/francis/main.py`` import this after
putting the repo root on ``sys.path`` (see each file's bootstrap and the note in
``app/__init__.py`` about the CLI treating the agent directory as the import
root). Keeping it in one module means the payload tolerance, the metric names and
the key order cannot drift between the batch path and the interactive path.

THREE RULES THIS MODULE ENFORCES, each because breaking it is a live liability:

1. **A number is emitted only if it was measured.** ``None`` means unknown and
   travels as ``None`` all the way to the browser and to CloudWatch, where the
   datapoint is *omitted* rather than sent as 0. A fabricated 0 in a spend metric
   is worse than a gap, because a gap is visible and a 0 averages in.

2. **Token counts come from ``result.metrics.accumulated_usage`` or nowhere.**
   ``inputTokens``/``outputTokens`` are required by Bedrock's ``Usage`` TypedDict;
   ``cacheReadInputTokens``/``cacheWriteInputTokens`` are optional and are read
   with ``.get()``. ``agents.pricing.cost_usd_or_none`` turns that dict into
   dollars, or returns ``None``. Nothing here estimates a token count.

3. **The adjudication key order is derived, never retyped.** It is built from
   ``prompts.loader.DOMAINS`` (the master prompt's own input order: identity,
   dealer, straw, employment, income, synthetic, bustout, rings) so the order
   cannot drift from the prompt the customer runs. There is no
   ``application_id`` key -- the customer's format does not have one.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from prompts.loader import AGENT_NAMES, DOMAINS

log = logging.getLogger("pp.runtime")

# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------

#: Offline by default. Every runtime in this repo must be runnable with zero AWS
#: calls, because the demo is rehearsed offline and CI has no Bedrock access.
#: Set MOCK_MODE=0 to invoke Bedrock for real -- which is also the only mode in
#: which token counts and therefore cost exist at all.
def mock_mode() -> bool:
    """Whether this process runs offline. Read at call time, not at import."""
    return os.environ.get("MOCK_MODE", "1") == "1"


#: Test seam: a comma-separated list of domains whose specialist call is forced
#: to fail, so graceful degradation (7-of-8) can be exercised without waiting for
#: a real Bedrock error. Not a data fixture -- it injects an exception, it does
#: not invent an analysis.
def forced_failure_domains() -> frozenset[str]:
    raw = os.environ.get("MOCK_FAIL_DOMAINS", "")
    return frozenset(d.strip().lower() for d in raw.split(",") if d.strip())


# ---------------------------------------------------------------------------
# The customer's output contracts
# ---------------------------------------------------------------------------

#: The exact ``- Title:`` line each verbatim specialist prompt defines. bustout
#: and rings define NO title ("Return only the final analysis." / "Return only
#: final analysis."), so they map to None and nothing may invent one for them.
ANALYSIS_TITLES: dict[str, str | None] = {
    "identity": "Identity Fraud Risk Analysis",
    "dealer": "Dealer Fraud Risk Analysis",
    "straw": "Straw Borrower Fraud Risk Analysis",
    "employment": "Employment Fraud Risk Analysis",
    "income": "Income Fraud Risk Analysis",
    "synthetic": "Synthetic Identity Fraud Risk Analysis",
    "bustout": None,
    "rings": None,
}

#: Specialist calibration bands, verbatim. Ordered longest-first for matching so
#: "POSSIBLE RISK" is never truncated to a "RISK" substring hit.
RISK_BANDS: tuple[str, ...] = ("POSSIBLE RISK", "HIGH RISK", "LOW RISK")

#: The master adjudication's 21 keys in the customer's required order, derived
#: from DOMAINS so it cannot drift from master_agent_prompt.txt.
ADJUDICATION_KEYS: tuple[str, ...] = (
    "master_summary",
    "overall_risk_decision",
    "recommendation_decision",
    "recommended_stipulations",
    "top_risk_factors",
    *[key for domain in DOMAINS for key in (f"{domain}_summary", f"{domain}_flag")],
)

OVERALL_RISK_VALUES: tuple[str, ...] = ("LOW RISK", "MEDIUM RISK", "HIGH RISK")
RECOMMENDATION_VALUES: tuple[str, ...] = (
    "APPROVE",
    "REVIEW AND APPLY STIPULATIONS",
    "DECLINE",
)

_BAND_RE = re.compile("|".join(re.escape(band) for band in RISK_BANDS))


def parse_risk_band(text: str | None) -> str | None:
    """First calibration band named in a specialist's prose, or None.

    Returns None rather than guessing a band: an unparseable analysis is a
    fidelity signal the UI should surface, not a value to default.
    """
    if not text:
        return None
    match = _BAND_RE.search(text.upper())
    return match.group(0) if match else None


def validate_adjudication(adjudication: dict[str, Any]) -> dict[str, Any]:
    """Report the 21-key contract: exact set, exact order, no extra keys.

    Reports rather than raises. A synthesis that returned 20 keys still has to
    reach the operator with the defect attached; swallowing the frame would lose
    both the finding and the analysis behind it.
    """
    keys = list(adjudication)
    return {
        "expected_count": len(ADJUDICATION_KEYS),
        "actual_count": len(keys),
        "order_matches": keys == list(ADJUDICATION_KEYS),
        "missing": [k for k in ADJUDICATION_KEYS if k not in adjudication],
        "unexpected": [k for k in keys if k not in ADJUDICATION_KEYS],
        "valid": keys == list(ADJUDICATION_KEYS),
    }


def order_adjudication(adjudication: dict[str, Any]) -> dict[str, Any]:
    """Reorder a complete adjudication into the customer's key order.

    Only reorders. Missing keys stay missing (``validate_adjudication`` reports
    them) and unexpected keys are preserved at the end so a contract break is
    visible in the emitted frame instead of being silently dropped.
    """
    ordered = {k: adjudication[k] for k in ADJUDICATION_KEYS if k in adjudication}
    ordered.update({k: v for k, v in adjudication.items() if k not in ordered})
    return ordered


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------

#: Application ids in this repo's fixtures are ``APP-1004``. Matched
#: case-insensitively with an optional space or underscore separator, because a
#: human typing into `agentcore invoke` writes "app 1004" as often as "APP-1004".
_APP_ID_RE = re.compile(r"\bAPP[\s_-]?(\d{3,6})\b", re.IGNORECASE)


def extract_prompt(payload: Any) -> Any:
    """Pull the user input out of an AgentCore invocation payload.

    Mirrors the tolerance of the CLI-generated ``_extract_prompt``: harness-style
    ``messages[]`` and ``tool_results[]`` pass straight through to Strands (which
    accepts a message list), everything else falls back to ``prompt``. Kept
    because ``agentcore invoke "text"`` sends ``{"prompt": "text"}`` while the
    evaluation harness sends ``{"messages": [...]}``, and a runtime that only
    reads one of them fails in exactly the demo path nobody rehearses.
    """
    if not isinstance(payload, dict):
        return "" if payload is None else str(payload)
    if "messages" in payload:
        return payload["messages"]
    if "tool_results" in payload:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": tr["toolUseId"],
                            "status": tr.get("status", "success"),
                            "content": tr.get("content", []),
                        }
                    }
                    for tr in payload["tool_results"]
                ],
            }
        ]
    return payload.get("prompt", "")


def _text_of(value: Any) -> str:
    """Flatten a prompt / messages structure to plain text for id extraction."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_text_of(v) for v in value.values())
    if isinstance(value, Iterable):
        return " ".join(_text_of(v) for v in value)
    return str(value)


def normalize_application_id(raw: str | None, *, explicit: bool = False) -> str | None:
    """Canonicalize an application id to ``APP-NNNN``, or None if absent.

    ``explicit=True`` means the caller passed a dedicated ``application_id``
    field, so an id that does not look like ``APP-NNNN`` is still honoured
    (uppercased and stripped) -- the customer's own ids will not match this
    repo's fixture pattern, and rejecting them here would be a hard-coded
    assumption about their key format.

    ``explicit=False`` (free text) requires the pattern to match. Otherwise a
    prompt like "what can you do" would be read as the application id "WHAT CAN
    YOU DO" and produce a confusing not-found error instead of the correct
    "no application id in this request".
    """
    if not raw:
        return None
    match = _APP_ID_RE.search(raw)
    if match:
        return f"APP-{match.group(1)}"
    if not explicit:
        return None
    return raw.strip().upper() or None


def extract_application_id(payload: Any) -> str | None:
    """Find the application id in a payload.

    Checks the explicit ``application_id`` field first, then scans free text --
    ``agentcore invoke`` only ever sends ``{"prompt": "..."}``, so an
    application-id-only contract would make the deployed runtime unusable from
    the CLI the customer will actually type into.
    """
    if isinstance(payload, dict):
        for key in ("application_id", "applicationId", "app_id"):
            if payload.get(key):
                normalized = normalize_application_id(str(payload[key]), explicit=True)
                if normalized:
                    return normalized
    return normalize_application_id(_text_of(extract_prompt(payload)))


def new_session_id() -> str:
    """A runtimeSessionId AgentCore will accept.

    The API floor is 33 characters. ``uuid4().hex`` is 32 and is rejected;
    ``str(uuid.uuid4())`` is 36 with the hyphens. This exists so nobody
    rediscovers that by reading a validation error in front of the customer.
    """
    return str(uuid.uuid4())


#: The documented AgentCore minimum for ``runtimeSessionId``.
SESSION_ID_MIN_LENGTH = 33


def session_id_of(context: Any, fallback: str | None = None) -> str:
    """Session id from a RequestContext, falling back to a fresh uuid4.

    ``RequestContext.session_id`` is Optional[str] and is None whenever the
    caller omitted the session header, so this never subscripts it.
    """
    session_id = getattr(context, "session_id", None)
    return session_id or fallback or new_session_id()


# ---------------------------------------------------------------------------
# The signal-layer seam
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Directory that holds ``prompts/``, resolved from this file's location."""
    return Path(__file__).resolve().parent.parent


def build_signal_payload(application_id: str) -> tuple[Any | None, dict[str, Any] | None, str]:
    """Load one application's signals. **This is the production swap point.**

    Returns ``(signal_payload, raw_record, source)``. In this port the data comes
    from ``fixtures/`` (or, until that lands, ``data/applications.py``). In the
    customer's deployment it comes from their governed signal layer -- the
    precomputed per-domain signals their own architecture document describes
    (Agent_Underwriting_Process_Guide 2.2: "These signals are customized to each
    of the subagent fraud areas ... to optimize processing time by preventing the
    agents from making the same SQL queries for each app").

    Replacing the body of this function with a Cortex Analyst / Aurora / Athena
    read is the entire data-layer migration. Nothing above it changes: the
    fan-out, the prompts and the adjudication contract are all downstream of the
    ``SignalPayload`` this returns.
    """
    try:
        from fixtures.loader import build_payload, get_application  # noqa: PLC0415

        return build_payload(application_id), get_application(application_id), "fixtures.loader"
    except Exception as exc:  # fixtures/ is landing from another workstream
        log.debug("fixtures.loader unavailable (%s); falling back", exc)

    try:
        from data.applications import get_application  # noqa: PLC0415

        record = get_application(application_id)
        return None, record, "data.applications"
    except Exception as exc:
        log.warning("no application dataset available: %s", exc)
        return None, None, "unavailable"


class SignalProjectionUnavailable(RuntimeError):
    """The per-domain projection is unavailable and a live call was requested."""


def make_renderer(record: dict[str, Any] | None):
    """Build the ``renderer`` callable ``agents.fanout`` projects each domain with.

    Delegates to ``signal_layer.schema.render_for_agent``, which owns the
    projection contract: a specialist sees ONLY its own domain's signals.

    THE FALLBACK DELIBERATELY DOES NOT WIDEN THE VIEW. The obvious shortcut --
    hand the specialist the whole application record when the projection is
    unavailable -- is a signal-boundary violation, and it is the one thing
    ``agents.fanout.render_domain_input`` refuses to do (it raises ``FanOutError``
    rather than pass an unprojected payload). This mirrors that refusal:

      * live (``MOCK_MODE=0``) -> raise ``SignalProjectionUnavailable``. A live
        adjudication computed off cross-domain signals would be a fidelity defect
        invisible in the output, so it must fail loudly instead.
      * offline (``MOCK_MODE=1``) -> return a placeholder that names the domain
        and states the projection is unavailable. No model reads it (the offline
        analyses are deterministic), so it carries no signals at all rather than
        the wrong ones.
    """

    def render(payload: Any, domain: str) -> str:
        if isinstance(payload, str):
            return payload
        if payload is not None:
            try:
                from signal_layer.schema import render_for_agent  # noqa: PLC0415

                return render_for_agent(payload, domain)
            except ImportError as exc:
                log.debug("signal_layer.schema unavailable (%s)", exc)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                # The module is present but this payload carries no view for the domain
                # (wrong shape, or a partially-built payload). Same policy as a missing
                # module: refuse live, placeholder offline. Never widen the view.
                log.debug("no projection for domain %s (%s)", domain, exc)

        if not mock_mode():
            raise SignalProjectionUnavailable(
                "signal_layer.schema.render_for_agent is unavailable, so the per-domain "
                f"projection for {domain!r} cannot be built. Refusing to run a live "
                "specialist on an unprojected payload: an agent must never see another "
                "domain's signals."
            )
        available = sorted((record or {}).get("signals", {}).keys())
        return (
            f"(MOCK_MODE placeholder domain view for {domain}. "
            "signal_layer.schema.render_for_agent has not landed, so no per-domain "
            "projection was built and NO signal values are included here -- widening "
            f"the view would breach the domain boundary. Signals the fixture carries: "
            f"{len(available)}.)"
        )

    return render


def render_domain_view(signal_payload: Any, record: dict[str, Any] | None, domain: str) -> str:
    """One domain's projected view. Thin wrapper over :func:`make_renderer`."""
    return make_renderer(record)(signal_payload, domain)


# ---------------------------------------------------------------------------
# Usage / cost extraction
# ---------------------------------------------------------------------------


def usage_of(result: Any) -> dict[str, Any] | None:
    """Real Bedrock token counts from a Strands result, or None.

    ``result.metrics.accumulated_usage`` is the only source. The two cache keys
    are optional in Bedrock's ``Usage`` TypedDict, hence ``.get()`` -- and note
    that ``inputTokens`` counts only NON-cached tokens, so total input is
    ``inputTokens + cacheReadInputTokens + cacheWriteInputTokens``
    (``agents.pricing`` does that sum; do not do it here).

    Returns None when the metric is absent. Never returns zeros: a zero token
    count is a $0.00 cost claim.
    """
    usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None)
    if not isinstance(usage, dict):
        return None
    if usage.get("inputTokens") is None or usage.get("outputTokens") is None:
        return None
    return {
        "inputTokens": int(usage["inputTokens"]),
        "outputTokens": int(usage["outputTokens"]),
        "cacheReadInputTokens": int(usage.get("cacheReadInputTokens") or 0),
        "cacheWriteInputTokens": int(usage.get("cacheWriteInputTokens") or 0),
    }


def model_latency_ms_of(result: Any) -> float | None:
    """Bedrock-reported model latency in ms from a Strands result, or None.

    ``accumulated_metrics['latencyMs']`` is the model's own figure and is not the
    same as our wall clock (which includes the event loop and tool turns). Both
    are emitted, separately labelled, because conflating them is how a
    "measured" latency stops being measurable.
    """
    metrics = getattr(getattr(result, "metrics", None), "accumulated_metrics", None)
    if not isinstance(metrics, dict):
        return None
    value = metrics.get("latencyMs")
    return float(value) if isinstance(value, (int, float)) else None


def cost_of(model_id: str | None, usage: dict[str, Any] | None) -> float | None:
    """Dollar cost for one call, or None when the token counts are unknown."""
    if not model_id or usage is None:
        return None
    try:
        from agents.pricing import cost_usd_or_none  # noqa: PLC0415

        return cost_usd_or_none(model_id, usage)
    except Exception as exc:
        log.debug("agents.pricing unavailable (%s); cost reported as unknown", exc)
        return None


def usage_frame_fields(usage: dict[str, Any] | None) -> dict[str, Any]:
    """The four token fields for an SSE frame, all None when usage is absent."""
    if usage is None:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
        }
    return {
        "input_tokens": usage["inputTokens"],
        "output_tokens": usage["outputTokens"],
        "cache_read_tokens": usage["cacheReadInputTokens"],
        "cache_write_tokens": usage["cacheWriteInputTokens"],
    }


def tier_of(model_id: str | None) -> str | None:
    if not model_id:
        return None
    try:
        from agents.models import tier_label  # noqa: PLC0415

        return tier_label(model_id)
    except Exception:
        return None


def agent_name_of(domain: str) -> str:
    """The customer's RESULT_TABLE.AGENT_NAME value for a domain."""
    return AGENT_NAMES.get(domain, domain)


# ---------------------------------------------------------------------------
# Per-agent CloudWatch telemetry
# ---------------------------------------------------------------------------

#: Namespace and metric names match observability/cloudwatch_dashboard.json so
#: the existing spend dashboard keeps resolving.
METRICS_NAMESPACE = os.environ.get("METRICS_NAMESPACE", "AgentCoreFraudDemo")

#: CloudWatch caps PutMetricData at 20 datapoints per call.
_CW_BATCH = 20


def metrics_enabled() -> bool:
    """Whether to publish to CloudWatch. Off unless explicitly switched on.

    Emission is opt-in so an offline rehearsal never makes an AWS write call.
    """
    return os.environ.get("EMIT_CLOUDWATCH_METRICS", "0") == "1"


def _datapoint(name: str, value: Any, unit: str, dims: list[dict[str, str]] | None, ts: Any) -> dict | None:
    """One CloudWatch datapoint, or None when the value was never measured.

    Returning None -- and the caller dropping it -- is the whole point.
    ``observability/metrics.py`` is deliberately NOT reused here: its
    ``emit_adjudication`` coerces absent tokens with ``info.get("input_tokens", 0)``
    and reads a ``timing['nominal_parallel_per_app_s']`` field that no longer
    exists now that the fabricated nominal-latency table is gone. Publishing
    ``InputTokens=0`` for a call whose usage was unavailable would put a false
    zero into the same dashboard the customer is shown.
    """
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    point: dict[str, Any] = {
        "MetricName": name,
        "Timestamp": ts,
        "Value": float(value),
        "Unit": unit,
    }
    if dims:
        point["Dimensions"] = dims
    return point


def emit_agent_metrics(
    per_agent: dict[str, dict[str, Any]],
    *,
    total_cost_usd: float | None,
    end_to_end_ms: float | None,
    applications: int = 1,
) -> dict[str, Any]:
    """Publish per-agent spend/tokens/latency plus pipeline totals.

    ``per_agent`` values are the ``agent_done`` frames this runtime already
    emitted, so CloudWatch and the SSE stream cannot disagree. Any field that is
    None is omitted from the batch rather than sent as 0.

    Returns a report -- ``{"enabled", "sent", "datapoints", "skipped", "error"}``
    -- so the ``done`` frame can state whether telemetry actually left the
    process instead of implying it.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    ts = datetime.now(timezone.utc)
    data: list[dict] = []
    skipped: list[str] = []

    for domain, info in per_agent.items():
        dims = [{"Name": "Agent", "Value": agent_name_of(domain)}]
        tier = info.get("tier")
        if tier:
            dims.append({"Name": "Tier", "Value": str(tier)})
        candidates = (
            ("CostUSD", info.get("cost_usd"), "None"),
            ("InputTokens", info.get("input_tokens"), "Count"),
            ("OutputTokens", info.get("output_tokens"), "Count"),
            ("CacheReadTokens", info.get("cache_read_tokens"), "Count"),
            (
                "LatencySeconds",
                (info["latency_ms"] / 1000.0) if isinstance(info.get("latency_ms"), (int, float)) else None,
                "Seconds",
            ),
        )
        for name, value, unit in candidates:
            point = _datapoint(name, value, unit, dims, ts)
            if point is None:
                skipped.append(f"{domain}.{name}")
            else:
                data.append(point)

    for name, value, unit in (
        ("PerAppCostUSD", total_cost_usd, "None"),
        (
            "ParallelLatencySeconds",
            (end_to_end_ms / 1000.0) if isinstance(end_to_end_ms, (int, float)) else None,
            "Seconds",
        ),
        ("Applications", float(applications), "Count"),
    ):
        point = _datapoint(name, value, unit, None, ts)
        if point is None:
            skipped.append(name)
        else:
            data.append(point)

    report: dict[str, Any] = {
        "enabled": metrics_enabled(),
        "sent": False,
        "namespace": METRICS_NAMESPACE,
        "datapoints": len(data),
        "skipped_unmeasured": skipped,
        "error": None,
    }
    if not report["enabled"] or not data:
        return report

    try:
        import boto3  # noqa: PLC0415

        client = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        for start in range(0, len(data), _CW_BATCH):
            client.put_metric_data(Namespace=METRICS_NAMESPACE, MetricData=data[start : start + _CW_BATCH])
        report["sent"] = True
    except Exception as exc:  # telemetry must never fail an adjudication
        report["error"] = f"{type(exc).__name__}: {exc}"
        log.warning("CloudWatch emission failed: %s", report["error"])
    return report


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------


def error_frame(message: str, *, kind: str, **extra: Any) -> dict[str, Any]:
    """A terminal error frame.

    The key is literally ``error`` because that is what the SDK's own mid-stream
    exception handler emits (``{"error", "error_type", "message"}``), and because
    an exception raised after the response has started arrives as a data frame
    *behind HTTP 200*. Clients must therefore check EVERY frame for ``error``;
    using the same key for our deliberate errors and the SDK's accidental ones
    means one check covers both.
    """
    return {"event": "error", "error": message, "error_type": kind, **extra}


def elapsed_ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000.0, 1)
