"""One fraud specialist, as its own AgentCore runtime.

    agentcore invoke --runtime identity --prompt '{"application_id": "APP-1004"}'

ONE SOURCE TREE, EIGHT RUNTIMES

`agentcore/agentcore.json` declares eight runtimes -- identity, dealer, straw,
employment, income, synthetic, bustout, rings -- that all point at THIS directory as
their `codeLocation` and differ only in their name and one environment variable,
`SPECIALIST_DOMAIN`. The schema permits a shared `codeLocation` (verified with
`agentcore validate`), so eight independently deployable, independently scalable,
independently IAM-scoped runtimes cost one file to maintain rather than eight copies
that drift.

The domain is read at REQUEST time, not import time, so `agentcore dev` can serve any
specialist from one process by changing the variable, and a misconfigured runtime says
which variable is wrong instead of failing obscurely.

WHY IT REUSES fan_out_streaming FOR A SINGLE DOMAIN

`agents.fanout.fan_out_streaming(payload, domains=("identity",))` already does
everything a specialist needs: it projects the domain view, builds a fresh Strands
Agent on that domain's configured model, captures real usage, prices it, and reports
the prompt-cache verdict. Writing a separate single-agent path here would be a second
implementation of the thing whose fidelity is the entire product. The distributed
topology changes WHERE the work runs, not WHAT runs.

WHY THIS RETURNS JSON AND NOT SSE

A specialist is one model call. There is no intermediate progress to stream, and the
two transports have very different limits: a synchronous response may take up to 15
minutes, while a streaming response is capped at 60 seconds. The slowest specialist
measured so far took 29.8s -- comfortable synchronously, uncomfortably close to the
streaming ceiling. The orchestrator streams; the leaves do not.

OBSERVABILITY

Nothing is done here to make traces join up. The caller passes `traceParent` and
reuses its `runtimeSessionId`, and ADOT -- already in the dependency closure because
`instrumentation.enableOtel` is true -- continues the parent trace automatically. See
the orchestrator's `_invoke_specialist_runtime` for the propagation, and
app/underwriter/main.py's module docstring for why the headers are load-bearing.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import bootstrap -- identical to app/underwriter/main.py, and identical for the
# same reason: the CLI treats this directory as the import root, so the shared
# packages are either an ancestor (dev) or vendored next to this file (packaged).
# Anchoring on prompts/loader.py means neither layout needs a different code path.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent


def _find_shared_root() -> Path:
    for candidate in (_HERE, *_HERE.parents):
        if (candidate / "prompts" / "loader.py").is_file():
            return candidate
    return _HERE.parent.parent


_SHARED_ROOT = _find_shared_root()

for _path in (str(_SHARED_ROOT), str(_HERE)):
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from bedrock_agentcore.runtime.models import PingStatus  # noqa: E402

from app.runtime_support import (  # noqa: E402
    agent_name_of,
    build_signal_payload,
    extract_application_id,
    make_renderer,
    mock_mode,
    parse_risk_band,
    risk_band_form,
    session_id_of,
)
from prompts.loader import DOMAINS  # noqa: E402

# debug=False for the same reason as every other runtime here: with debug=True the SDK
# routes any payload carrying `_agent_core_app_action` to `_handle_task_action`, which
# lets an unauthenticated caller force the ping status and enumerate running jobs.
app = BedrockAgentCoreApp(debug=False)
log = app.logger
logging.getLogger("pp.specialist").setLevel(logging.INFO)


def specialist_domain() -> str:
    """This runtime's domain, from ``SPECIALIST_DOMAIN``.

    Read per call rather than at import so one `agentcore dev` process can serve any
    specialist, and so a deployment mistake surfaces as this message rather than as a
    KeyError deep in the projection layer.
    """
    raw = (os.environ.get("SPECIALIST_DOMAIN") or "").strip().lower()
    if not raw:
        raise ValueError(
            "SPECIALIST_DOMAIN is not set. Every specialist runtime shares this "
            f"codeLocation and is distinguished only by that variable. Expected one of: "
            f"{', '.join(DOMAINS)}."
        )
    if raw not in DOMAINS:
        raise ValueError(
            f"SPECIALIST_DOMAIN={raw!r} is not a known domain. Expected one of: "
            f"{', '.join(DOMAINS)}."
        )
    return raw


@app.ping
def ping_status() -> PingStatus:
    """Health for the control plane.

    Reports unhealthy on a misconfigured domain rather than accepting work it cannot
    do: eight runtimes sharing one image makes a wrong environment variable the most
    likely deployment error, and a specialist that answers pings while being unable to
    serve a request keeps receiving traffic.
    """
    try:
        specialist_domain()
    except ValueError:
        return PingStatus.UNHEALTHY
    if app.get_async_task_info()["active_count"] > 0:
        return PingStatus.HEALTHY_BUSY
    return PingStatus.HEALTHY


@app.entrypoint
async def invoke(payload: Any, context: Any) -> dict[str, Any]:
    """Run this runtime's specialist over one application and return its analysis.

    The second parameter must be named literally ``context`` or the SDK does not
    inject it and the call raises TypeError.

    A failure is RETURNED, not raised. The orchestrator degrades gracefully on a
    missing specialist (7-of-8 still adjudicates), and it can only do that if it can
    tell "this specialist failed" from "the transport failed" -- so the error travels
    as data with a 200, and the orchestrator's own error handling covers the rest.
    """
    started = time.perf_counter()
    session_id = session_id_of(context)

    try:
        domain = specialist_domain()
    except ValueError as exc:
        return {
            "ok": False,
            "domain": None,
            "error": str(exc),
            "error_type": "ConfigurationError",
            "session_id": session_id,
        }

    application_id = extract_application_id(payload)
    if not application_id:
        return {
            "ok": False,
            "domain": domain,
            "error": (
                "no application id in this request. Send "
                '{"application_id": "APP-1004"} or a prompt containing APP-NNNN.'
            ),
            "error_type": "ValueError",
            "session_id": session_id,
        }

    log.info(
        "specialist %s starting application=%s session=%s",
        domain,
        application_id,
        session_id,
    )

    try:
        signal_payload, record, signal_source = build_signal_payload(application_id)
        if signal_payload is None:
            return {
                "ok": False,
                "domain": domain,
                "application_id": application_id,
                "error": f"no signals for {application_id}",
                "error_type": "LookupError",
                "session_id": session_id,
            }

        from agents.fanout import fan_out_streaming  # noqa: PLC0415

        frame: dict[str, Any] | None = None
        async for event in fan_out_streaming(
            signal_payload,
            domains=(domain,),
            mock=mock_mode(),
            # `record` is required, not optional: make_renderer uses it for the
            # fallback path, and passing nothing raised
            # `TypeError: make_renderer() missing 1 required positional argument`
            # on the first real invocation of a deployed specialist.
            renderer=make_renderer(record),
        ):
            # `agent_completed` / `agent_failed` carry the analysis and the measured
            # usage. `agent_started` and `fanout_completed` are progress framing that
            # only means something for a real fan-out, and this is a leaf.
            if event.get("event") in ("agent_completed", "agent_failed"):
                frame = event

        if frame is None:
            return {
                "ok": False,
                "domain": domain,
                "application_id": application_id,
                "error": "fan_out_streaming yielded no terminal frame for this domain",
                "error_type": "RuntimeError",
                "session_id": session_id,
            }

        if frame.get("event") == "agent_failed":
            return {
                "ok": False,
                "domain": domain,
                "application_id": application_id,
                "error": frame.get("error"),
                "error_type": "SpecialistError",
                "session_id": session_id,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }

        analysis = frame.get("analysis") or ""
        return {
            "ok": True,
            "domain": domain,
            "agent_name": agent_name_of(domain),
            "application_id": application_id,
            "session_id": session_id,
            "signal_source": signal_source,
            "mock_mode": mock_mode(),
            "analysis": analysis,
            "analysis_title": frame.get("analysis_title"),
            "risk_band": parse_risk_band(analysis),
            "risk_band_form": risk_band_form(analysis),
            "char_count": len(analysis),
            "model": frame.get("model"),
            "tier": frame.get("tier"),
            "measured": frame.get("measured"),
            "source": frame.get("source"),
            "model_latency_ms": frame.get("model_latency_ms"),
            "input_tokens": frame.get("input_tokens"),
            "output_tokens": frame.get("output_tokens"),
            "cache_read_tokens": frame.get("cache_read_tokens"),
            "cache_write_tokens": frame.get("cache_write_tokens"),
            "cost_usd": frame.get("cost_usd"),
            "stop_reason": frame.get("stop_reason"),
            "prompt_cache": frame.get("prompt_cache"),
            # Wall clock INSIDE this runtime. The orchestrator measures its own view
            # separately, and the difference between the two is the AgentCore
            # invocation overhead this topology adds -- the number worth watching.
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001 -- returned as data, see the docstring
        log.exception("specialist %s failed", domain)
        return {
            "ok": False,
            "domain": domain,
            "application_id": application_id,
            "error": f"{type(exc).__name__}: {exc}",
            "error_type": type(exc).__name__,
            "session_id": session_id,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }


if __name__ == "__main__":
    app.run()
