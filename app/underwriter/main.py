"""
AgentCore Runtime: the batch underwriting path.

One application in, one 21-key adjudication out, streamed. This is the
performance-critical runtime -- the customer's blocker is that 4,500
applications took over 48 hours on Snowflake Intelligence and they need under a
minute per application -- so every structural decision below exists to keep the
eight specialists genuinely concurrent and to make the resulting latency and
spend *measurable* rather than asserted.

    agentcore dev                                   # local, cwd = this directory
    agentcore invoke --dev '{"application_id": "APP-1004"}'
    agentcore invoke --dev "Run a full underwriting review of APP-1004"

WHY THIS IS AN ASYNC GENERATOR AND NOT ``def invoke(payload) -> dict``

The previous entrypoint was synchronous, took no ``context``, and returned a
dict. Three consequences, all of which this file fixes:

  1. **No session id.** ``BedrockAgentCoreApp._takes_context`` decides whether to
     inject the ``RequestContext`` by checking that the handler's *second
     positional parameter is literally named* ``context``. Not by type, not by
     annotation, by name. A handler with one parameter -- or with a second
     parameter named ``ctx`` -- silently never receives it, and the failure shows
     up at request time as "why is every log line missing a session id".
     ``tests/test_runtime_contract.py`` asserts the parameter name for that
     reason.

  2. **A threadpool slot held for the whole fan-out.** ``_invoke_handler`` routes
     a *sync* handler through ``run_in_threadpool``, so an 8-way fan-out that
     takes tens of seconds occupies a Starlette worker throughout, against the
     synchronous invocation timeout of 15 minutes, which is **not adjustable**.
     The streaming path's ceiling is 60 minutes.

  3. **Nothing to show until the end.** An async-generator entrypoint is
     auto-converted to ``text/event-stream`` with each yielded object serialized
     by ``_convert_to_sse`` as ``data: <json>\\n\\n``. So we yield plain dicts and
     never hand-format SSE. The live UI can then render each specialist as it
     lands instead of after all eight.

THE ERROR CONTRACT CALLERS MUST HONOUR

``_stream_with_error_handling`` catches anything raised mid-generator and yields
a terminal ``{"error", "error_type", "message"}`` frame -- but the response
status was already committed as **HTTP 200** when the first byte went out. A
client that trusts the status code will read a failed run as a successful one.
Every frame must be checked for an ``error`` key. This module therefore:

  * uses the key ``error`` only for terminal failures, matching the SDK's own
    shape so one client-side check covers both ours and the SDK's;
  * uses a different key, ``failure``, for a *non-terminal* single-specialist
    failure, so 7-of-8 graceful degradation does not trip that check;
  * always emits a ``done`` frame when the run completed, even partially.

Both runtimes are offline by default (``MOCK_MODE`` defaults to 1) so the demo
can be rehearsed and CI can run with zero AWS calls. In MOCK_MODE token counts
and cost do not exist and are emitted as ``null`` -- never as a plausible-looking
number.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Import bootstrap
#
# The AgentCore CLI treats THIS directory as the import root: `agentcore dev`
# spawns `.venv/bin/uvicorn main:app` with cwd set to `codeLocation`, and
# `agentcore package` zips the directory containing the nearest pyproject.toml
# with its contents at the archive root. So `main` is a top-level module and the
# repo root is not on sys.path unless we put it there.
#
# Two layouts are supported deliberately:
#   * development / `agentcore dev`  -> the repo root is an ancestor directory.
#   * packaged CodeZip deployment    -> the shared packages must sit NEXT TO this
#     file (vendored into the agent directory), because the zip's root is this
#     directory and nothing above it is included. See pyproject.toml.
# Anchoring on prompts/loader.py rather than on a fixed number of `..` hops means
# neither layout needs a different code path.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent


def _find_shared_root() -> Path:
    for candidate in (_HERE, *_HERE.parents):
        if (candidate / "prompts" / "loader.py").is_file():
            return candidate
    return _HERE.parent.parent


_SHARED_ROOT = _find_shared_root()

# Insert the shared root FIRST and this directory SECOND, leaving this directory
# at sys.path[0]. That ordering is deliberate and matches app/francis/main.py:
# both the repo root and an agent directory may define a package of the same name
# (`tools` already collides that way), and the agent directory has to win --
# which is also the state `agentcore dev` produces, since it runs uvicorn with cwd
# set here.
for _path in (str(_SHARED_ROOT), str(_HERE)):
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from bedrock_agentcore.runtime.models import PingStatus  # noqa: E402

from app.runtime_support import (  # noqa: E402
    ANALYSIS_TITLES,
    agent_name_of,
    build_signal_payload,
    cost_of,
    elapsed_ms,
    emit_agent_metrics,
    error_frame,
    extract_application_id,
    forced_failure_domains,
    make_renderer,
    mock_mode,
    model_latency_ms_of,
    order_adjudication,
    parse_risk_band,
    risk_band_form,
    session_id_of,
    tier_of,
    usage_frame_fields,
    usage_of,
    validate_adjudication,
)
from prompts.loader import DOMAINS  # noqa: E402

# debug=False is a security decision, not a log-level one. With debug=True the
# SDK routes any payload carrying `_agent_core_app_action` to
# `_handle_task_action`, which lets an unauthenticated caller force the ping
# status to Healthy/HealthyBusy and enumerate running jobs. That is a health-state
# backdoor on the /invocations path.
app = BedrockAgentCoreApp(debug=False)
log = app.logger
logging.getLogger("pp.runtime").setLevel(logging.INFO)

#: Set by the entrypoint so the ping handler can describe the pipeline's own view
#: of health, not just the SDK's task counter.
_last_completed_at: float | None = None


@app.ping
def ping_status() -> PingStatus:
    """Health for the AgentCore control plane.

    The SDK's automatic behaviour already reports HealthyBusy while any tracked
    async task is open, and this handler is consulted *before* that fallback
    (forced > custom > automatic), so it must reproduce the task check or a long
    fan-out would advertise itself as idle and invite more work onto a busy
    microVM.
    """
    if app.get_async_task_info()["active_count"] > 0:
        return PingStatus.HEALTHY_BUSY
    return PingStatus.HEALTHY


# ---------------------------------------------------------------------------
# Specialist execution
# ---------------------------------------------------------------------------


def _model_for(domain: str) -> str | None:
    try:
        from agents.models import model_for  # noqa: PLC0415

        return model_for(domain)
    except Exception:
        return None


async def _forced_failures(failing: frozenset[str], started: float):
    """Yield an ``agent_failed`` frame for each domain the test seam knocks out.

    ``MOCK_FAIL_DOMAINS=income,rings`` exercises 7-of-8 graceful degradation
    without needing a live Bedrock error.

    The failure is injected by EXCLUDING those domains from the fan-out and
    reporting them failed, not by raising inside the renderer:
    ``fan_out_streaming`` projects every domain up front (``_project``) precisely
    so a projection bug fails before any model call, so a renderer that raises
    aborts the whole run rather than one dimension. Excluding the domain
    reproduces what the caller actually sees when one specialist dies -- an
    ``agent_failed`` frame, a missing analysis, and a synthesis that proceeds on
    the remaining seven with that domain listed in ``degraded_domains``.
    """
    for domain in failing:
        yield (
            "failed",
            {
                "domain": domain,
                "agent_name": agent_name_of(domain),
                "error": f"RuntimeError: MOCK_FAIL_DOMAINS forced a failure for domain {domain!r}",
            },
        )


def _normalize_agent_event(event: dict[str, Any], started: float) -> tuple[str, dict[str, Any]]:
    """Map one ``agents.fanout`` frame onto this runtime's frame contract.

    ``fan_out_streaming`` emits ``agent_started`` / ``agent_completed`` /
    ``agent_failed`` / ``fanout_completed`` with ``usage`` as a nested dict and
    ``wall_ms``. This flattens usage into the four scalar token fields the UI and
    the CloudWatch emitter read, and renames the timing field, WITHOUT inventing
    anything: a ``None`` usage stays four ``None``s.
    """
    kind = event.get("event")
    domain = event.get("domain")

    if kind == "agent_failed" or event.get("error"):
        return "failed", {
            "domain": domain,
            "agent_name": event.get("agent_name") or agent_name_of(str(domain)),
            "error": str(event.get("error") or "unknown error"),
        }
    if kind != "agent_completed":
        return kind or "other", event

    usage = event.get("usage")
    model_id = event.get("model")
    analysis = event.get("analysis") or ""
    wall_ms = event.get("wall_ms")
    return "done", {
        "domain": domain,
        "agent_name": event.get("agent_name") or agent_name_of(str(domain)),
        "analysis": analysis,
        "analysis_title": event.get("analysis_title", ANALYSIS_TITLES.get(str(domain))),
        "risk_band": parse_risk_band(analysis),
        # "contract" | "labelled" | None. Emitted so a specialist that returned a
        # correct verdict in the wrong SHAPE ("**Risk Level: HIGH**" rather than the
        # contract's "HIGH RISK") is visible as a fidelity deviation instead of being
        # smoothed over by the parser that accepts it.
        "risk_band_form": risk_band_form(analysis),
        "char_count": len(analysis),
        "model": model_id,
        "tier": event.get("tier") or tier_of(model_id),
        # Two distinct latencies, never conflated: our wall clock around the call,
        # and Bedrock's own reported model latency (absent offline, hence None).
        "latency_ms": wall_ms,
        "model_latency_ms": event.get("latency_ms"),
        "measured": usage is not None,
        "source": event.get("source", "mock" if mock_mode() else "bedrock"),
        **usage_frame_fields(usage),
        "cost_usd": event.get("cost_usd"),
        "stop_reason": event.get("stop_reason"),
        "prompt_cache": event.get("prompt_cache"),
    }


async def _fan_out(signal_payload: Any, record: dict[str, Any] | None, started: float):
    """Yield each specialist's outcome as it completes.

    Delegates to ``agents.fanout.fan_out_streaming``, which owns the concurrency
    primitive: ``asyncio.as_completed`` over one task per domain, each with its
    own single-use ``Agent``. That is the correct primitive here and the
    alternatives are actively wrong -- ``ThreadPoolExecutor`` adds a second
    scheduler in front of async Bedbrock calls, and a shared ``Agent`` raises
    ``ConcurrencyException`` because ``stream_async`` holds a non-blocking
    ``threading.Lock`` for the duration of an invocation.

    Yields ``("done", frame)``, ``("failed", {domain, error})``, or
    ``("fanout_completed", frame)``.
    """
    from agents.fanout import fan_out_streaming  # noqa: PLC0415

    failing = forced_failure_domains()
    async for kind_frame in _forced_failures(failing, started):
        yield kind_frame

    domains = [d for d in DOMAINS if d not in failing]
    if not domains:
        return

    async for event in fan_out_streaming(
        signal_payload,
        domains=domains,
        renderer=make_renderer(record),
        mock=mock_mode(),
    ):
        if not isinstance(event, dict):
            continue
        kind, frame = _normalize_agent_event(event, started)
        if kind in ("done", "failed", "fanout_completed"):
            yield kind, frame


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


@app.async_task
async def _synthesize(analyses: dict[str, str], application_id: str) -> Any:
    """Run the master synthesis, returning ``agents.synthesize.SynthesisResult``.

    Decorated with ``@app.async_task`` so the ping status reports HealthyBusy for
    the synthesis leg as well as the fan-out. The decorator only accepts a
    coroutine function -- it raises ``ValueError`` on an async generator, which is
    why the streaming entrypoint itself uses
    ``add_async_task``/``complete_async_task`` by hand instead.

    ``application_id`` is passed through because offline synthesis is
    fixture-pinned by design (``synthesize_offline`` refuses to score an
    application nobody pinned rather than deriving a verdict from finding
    volume, which the master prompt's MSTR-022/023/031 rules forbid).
    """
    from agents.synthesize import synthesize  # noqa: PLC0415

    return await synthesize(analyses, application_id=application_id, mock=mock_mode())


def _adjudication_dict(result: Any) -> dict[str, Any]:
    """Plain dict for the validated ``Adjudication`` on a SynthesisResult.

    ``Adjudication`` is a pydantic model whose field order IS the customer's key
    order, so ``model_dump()`` already emits the 21 keys correctly ordered;
    ``order_adjudication`` downstream is belt-and-braces against a future
    refactor reordering the model.
    """
    adjudication = getattr(result, "adjudication", result)
    dump = getattr(adjudication, "model_dump", None)
    if callable(dump):
        return dict(dump())
    return dict(adjudication) if isinstance(adjudication, dict) else {}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


@app.entrypoint
async def invoke(payload, context):
    """Stream one underwriting adjudication.

    The second parameter MUST be named ``context``: ``_takes_context`` matches by
    name. See the module docstring.

    Accepts ``{"application_id": "APP-1004"}`` and ``{"prompt": "...APP-1004..."}``
    (and ``{"messages": [...]}``), because ``agentcore invoke`` only ever sends a
    prompt.

    Frame sequence:
        start -> agent_start x8 -> (agent_done | failure) x8 -> synthesis_start
        -> done
    A terminal ``error`` frame replaces the tail on an unrecoverable failure.
    """
    global _last_completed_at

    run_started = time.perf_counter()
    session_id = session_id_of(context)
    application_id = extract_application_id(payload)

    # add_async_task/complete_async_task rather than @app.async_task: the
    # decorator rejects async generators (verified: ValueError "@async_task can
    # only be applied to async functions"), and an entrypoint that streams must
    # be one. Registering by hand keeps the ping status honest for the whole run.
    task_id = app.add_async_task("underwriting_fanout", {"application_id": application_id, "session_id": session_id})
    try:
        if not application_id:
            yield error_frame(
                "No application id found in the payload. Send {'application_id': 'APP-1004'} "
                "or a prompt naming one, e.g. 'Run a full underwriting review of APP-1004'.",
                kind="MissingApplicationId",
                session_id=session_id,
            )
            return

        signal_payload, record, signal_source = build_signal_payload(application_id)
        if record is None and signal_payload is None:
            yield error_frame(
                f"Application {application_id} not found in the signal layer ({signal_source}).",
                kind="ApplicationNotFound",
                session_id=session_id,
                application_id=application_id,
            )
            return

        yield {
            "event": "start",
            "session_id": session_id,
            "application_id": application_id,
            "agents": [agent_name_of(domain) for domain in DOMAINS],
            "domains": list(DOMAINS),
            "signal_source": signal_source,
            "mock_mode": mock_mode(),
            # Stated up front so a consumer knows before the first agent lands
            # whether tokens and cost will be present at all in this run.
            "measures_tokens": not mock_mode(),
            "at_ms": 0.0,
        }

        for domain in DOMAINS:
            model_id = _model_for(domain)
            yield {
                "event": "agent_start",
                "domain": domain,
                "agent_name": agent_name_of(domain),
                "model": model_id,
                "tier": tier_of(model_id),
                "at_ms": elapsed_ms(run_started),
            }

        analyses: dict[str, str] = {}
        per_agent: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, Any]] = []

        fanout_ms: float | None = None

        # A fan-out-wide failure (as opposed to one specialist failing) is caught
        # here and turned into a structured error frame. Letting it propagate would
        # still reach the client -- the SDK's _stream_with_error_handling converts a
        # mid-stream exception into a terminal `data: {"error": ...}` frame -- but
        # with a bare exception message and no session_id or application_id, and
        # only after the SDK logs it as an unhandled error. The commonest cause is
        # the signal-projection refusal (SignalProjectionUnavailable), which is a
        # deliberate guard and deserves its own reported reason rather than a
        # stack-trace string.
        try:
            async for kind, event in _fan_out(signal_payload, record, run_started):
                if kind == "fanout_completed":
                    # fan_out_streaming's own measured wall clock for the whole
                    # fan-out. Preferred over ours because it excludes the time this
                    # generator spent blocked on a slow SSE consumer.
                    fanout_ms = event.get("wall_ms")
                    continue

                if kind == "failed":
                    failures.append(event)
                    log.warning("specialist failed: %s", event)
                    # Key is `failure`, NOT `error`: clients abort on `error`
                    # because a mid-stream exception arrives behind HTTP 200. One
                    # specialist dropping out is survivable and must not look
                    # terminal.
                    yield {
                        "event": "agent_failed",
                        "domain": event.get("domain"),
                        "agent_name": event.get("agent_name") or agent_name_of(str(event.get("domain"))),
                        "failure": event.get("error"),
                        "at_ms": elapsed_ms(run_started),
                    }
                    continue

                domain = event.get("domain")
                if not domain:
                    continue
                analyses[domain] = event.get("analysis") or ""
                per_agent[domain] = event
                yield {"event": "agent_done", "finished_at_ms": elapsed_ms(run_started), **event}
        except Exception as exc:
            log.exception("fan-out failed")
            yield error_frame(
                f"Fan-out failed: {type(exc).__name__}: {exc}",
                kind=type(exc).__name__,
                session_id=session_id,
                application_id=application_id,
                failures=failures,
                specialists_succeeded=len(per_agent),
                specialists_expected=len(DOMAINS),
                fanout_wall_ms=elapsed_ms(run_started),
            )
            return

        if fanout_ms is None:
            fanout_ms = elapsed_ms(run_started)

        if not analyses:
            yield error_frame(
                f"All {len(DOMAINS)} specialists failed; there is nothing to synthesize.",
                kind="FanOutFailed",
                session_id=session_id,
                application_id=application_id,
                failures=failures,
                fanout_wall_ms=fanout_ms,
            )
            return

        synth_model = _model_for("synthesizer")
        yield {
            "event": "synthesis_start",
            "model": synth_model,
            "tier": tier_of(synth_model),
            "analyses_received": len(analyses),
            "analyses_missing": [d for d in DOMAINS if d not in analyses],
            "at_ms": fanout_ms,
        }

        synth_started = time.perf_counter()
        try:
            synth_result = await _synthesize(analyses, application_id)
        except Exception as exc:
            # Synthesis has no partial answer: without the master's judgement
            # there is no adjudication, so this IS terminal and uses `error`.
            log.exception("synthesis failed")
            yield error_frame(
                f"Synthesis failed: {type(exc).__name__}: {exc}",
                kind=type(exc).__name__,
                session_id=session_id,
                application_id=application_id,
                failures=failures,
                fanout_wall_ms=fanout_ms,
            )
            return
        synth_ms = elapsed_ms(synth_started)

        adjudication = order_adjudication(_adjudication_dict(synth_result))
        # SynthesisResult exposes a `.metrics` view in AgentResult's shape, so the
        # same extractor reads both a live AgentResult and this envelope.
        synth_usage = usage_of(synth_result)
        synth_cost = getattr(synth_result, "cost_usd", None)
        if synth_cost is None:
            synth_cost = cost_of(synth_model, synth_usage)

        agent_costs = [info.get("cost_usd") for info in per_agent.values()]
        # Sum only when EVERY component is known. A partial sum presented as a
        # total is the same lie as a fabricated number.
        agent_cost_total = (
            sum(c for c in agent_costs if c is not None)
            if agent_costs and all(c is not None for c in agent_costs)
            else None
        )
        total_cost = (
            round(agent_cost_total + synth_cost, 6)
            if agent_cost_total is not None and synth_cost is not None
            else None
        )
        end_to_end_ms = elapsed_ms(run_started)

        latencies = {
            d: info["latency_ms"] for d, info in per_agent.items() if isinstance(info.get("latency_ms"), (int, float))
        }
        slowest = max(latencies, key=lambda k: latencies[k]) if latencies else None

        telemetry = emit_agent_metrics(
            per_agent,
            total_cost_usd=total_cost,
            end_to_end_ms=end_to_end_ms,
        )

        _last_completed_at = time.time()
        yield {
            "event": "done",
            "session_id": session_id,
            "application_id": application_id,
            "adjudication": adjudication,
            "key_check": validate_adjudication(adjudication),
            "specialists_succeeded": len(per_agent),
            "specialists_expected": len(DOMAINS),
            "failures": failures,
            "timing": {
                # Wall clock measured in this process. Real in both modes; the
                # `source` field says which.
                "fanout_wall_ms": fanout_ms,
                "synthesis_ms": synth_ms,
                "end_to_end_ms": end_to_end_ms,
                "sum_of_agent_ms": round(sum(latencies.values()), 1) if latencies else None,
                "slowest_agent": slowest,
                "slowest_agent_ms": latencies.get(slowest) if slowest else None,
                "source": "mock" if mock_mode() else "bedrock",
            },
            "cost": {
                # None means "not measured in this run" and must render as such.
                "agents_usd": agent_cost_total,
                "synthesis_usd": synth_cost,
                "total_usd": total_cost,
                "measured": total_cost is not None,
            },
            "synthesis": {
                "model": synth_model,
                "tier": tier_of(synth_model),
                "latency_ms": synth_ms,
                "model_latency_ms": model_latency_ms_of(synth_result),
                "measured": synth_usage is not None,
                "repair_attempts": getattr(synth_result, "repair_attempts", None),
                "degraded_domains": list(getattr(synth_result, "degraded_domains", []) or []),
                "source": getattr(synth_result, "source", "mock" if mock_mode() else "bedrock"),
                **usage_frame_fields(synth_usage),
                "cost_usd": synth_cost,
            },
            "telemetry": telemetry,
            "mock_mode": mock_mode(),
        }
    finally:
        app.complete_async_task(task_id)


if __name__ == "__main__":
    # Local dev server on 0.0.0.0:8080, identical request contract to the
    # deployed runtime -- including the fact that BOTH always answer
    # text/event-stream, even for a one-line prompt.
    app.run(port=int(os.environ.get("PORT", "8080")))
