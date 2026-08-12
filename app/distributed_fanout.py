"""Fan out to eight INDEPENDENT specialist runtimes instead of eight in-process agents.

    FANOUT_MODE=distributed   -> this module: 8 InvokeAgentRuntime calls
    FANOUT_MODE=inprocess     -> agents.fanout.fan_out_streaming (the default)

BOTH PATHS ARE KEPT ON PURPOSE

The topologies are not equivalent and the difference is measurable, so the switch stays
rather than one replacing the other. Parallelism in this pipeline comes from
``asyncio.gather``, not from process separation -- so splitting the eight specialists
into eight runtimes adds eight AgentCore invocations and eight cold-start surfaces
while adding no concurrency. What it buys is real but different: each specialist
deploys, scales, and is IAM-scoped on its own, and each gets its own log group, its own
metrics and its own span tree.

Which one is right depends on a number, not an opinion, and with both paths present
that number is one environment variable away. `evals/bench.py` can measure the same
application through each.

TRACE PROPAGATION: ADOT DOES IT, AND DOING IT TWICE BREAKS SIGV4

Eight independent runtimes produce eight unrelated traces unless the parent context
travels with the request. ``InvokeAgentRuntime`` accepts it (botocore's
bedrock-agentcore/2024-02-28 model):

    traceParent  -> header `traceparent`                       W3C trace context
    traceState   -> header `tracestate`
    baggage      -> header `baggage`
    traceId      -> header `X-Amzn-Trace-Id`                   X-Ray format
    runtimeSessionId -> `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`

ADOT does the trace propagation, and this module deliberately does NOT also do it.
Passing ``traceParent`` as a request parameter from inside an instrumented runtime makes
the call fail with "The request signature we calculated does not match" -- ADOT's
botocore patch is already adding context to a request botocore has signed. Measured both
ways; see :func:`_trace_headers`. The instrumentation emits a
``Bedrock AgentCore.InvokeAgentRuntime`` CLIENT span with a ``parentSpanId``, so each
child call is already a child span.

``runtimeSessionId`` IS passed explicitly, because it is an ordinary API parameter rather
than a header added after signing, and reusing the orchestrator's own value is what
groups all nine runtimes into one session in the CloudWatch GenAI Observability view. A
fresh session id per child would scatter one application's work across nine sessions.

Result: one trace, one session, nine runtime spans, rooted at the orchestrator.

FAILURE IS DATA, NOT AN EXCEPTION

A specialist runtime returns its own failure with HTTP 200 (see app/specialist/main.py),
and a transport failure raises here. Both are converted to a ``("failed", ...)`` frame
so the orchestrator's existing 7-of-8 degradation applies unchanged and the synthesis
still runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator

#: Read at call time so a test or a bench run can flip it without reimporting.
DEFAULT_MODE = "inprocess"


def fanout_mode() -> str:
    """``distributed`` or ``inprocess``. Defaults to in-process.

    In-process is the default deliberately: it is the topology whose end-to-end latency
    and cost were measured, and it is the one that does not depend on eight sibling
    runtimes existing. A deployment that has not created them must not silently fail
    every application.
    """
    mode = (os.environ.get("FANOUT_MODE") or DEFAULT_MODE).strip().lower()
    return mode if mode in ("distributed", "inprocess") else DEFAULT_MODE


def specialist_arn(domain: str) -> str | None:
    """This specialist's runtime ARN, injected by CDK as ``SPECIALIST_ARN_<DOMAIN>``.

    Wired in agentcore/cdk/lib/cdk-stack.ts from the sibling runtimes' own
    ``runtimeArn`` attributes, so nothing is copied by hand and a renamed runtime cannot
    leave a stale ARN behind. Returns None when the variable is absent, which the caller
    reports as a failed specialist rather than crashing the run.
    """
    return os.environ.get(f"SPECIALIST_ARN_{domain.upper()}") or None


def _trace_headers() -> dict[str, str]:
    """Explicit W3C trace parameters -- OFF by default, and that is a measured decision.

    DO NOT TURN THIS ON INSIDE AN AGENTCORE RUNTIME. Passing `traceParent` /
    `traceState` / `baggage` to `InvokeAgentRuntime` from a runtime whose entrypoint is
    wrapped by `opentelemetry-instrument` produces:

        AccessDeniedException: The request signature we calculated does not match the
        signature you provided.

    Measured both sides on 2026-08-12. From a laptop with no ADOT, all four variants --
    plain, traceParent, traceParent+traceState, baggage -- succeed and the specialist
    returns a valid analysis. From inside the orchestrator, the same call 403s, and the
    span records the failure under
    `amazon/opentelemetry/distro/patches/_botocore_patches.py:patched_api_call`. ADOT's
    botocore patch is already propagating context on this call; supplying it as a
    request parameter as well changes the request after botocore signed it.

    So propagation is ADOT's job here, not ours, and it does it: the emitted CLIENT span
    is `Bedrock AgentCore.InvokeAgentRuntime` from scope
    `opentelemetry.instrumentation.botocore.bedrock-agentcore`, carrying `parentSpanId`
    and `aws.bedrock.agentcore.runtime.arn` -- i.e. the child call is already a child
    span. `runtimeSessionId` is a normal API parameter, not a header we add, so it is
    still passed explicitly and is what groups all nine runtimes into one session.

    The escape hatch stays for a caller OUTSIDE a runtime -- deploy/verify-tracing.py
    starts a trace from a laptop and must state the parent explicitly, because there is
    no ADOT there to do it.
    """
    if os.environ.get("PROPAGATE_TRACE_EXPLICITLY") != "1":
        return {}

    try:
        from opentelemetry.propagate import inject  # noqa: PLC0415
    except Exception:
        return {}

    carrier: dict[str, str] = {}
    try:
        inject(carrier)
    except Exception:
        return {}

    out: dict[str, str] = {}
    for param, header in (
        ("traceParent", "traceparent"),
        ("traceState", "tracestate"),
        ("baggage", "baggage"),
    ):
        if carrier.get(header):
            out[param] = carrier[header]
    return out


_CLIENT: Any = None


def _client() -> Any:
    """One bedrock-agentcore data-plane client for the container's lifetime.

    ``max_pool_connections`` is set explicitly and is not incidental: botocore's default
    is 10, and this module opens EIGHT concurrent connections. Eight of ten leaves no
    headroom for the retry below, and the failure mode of an exhausted pool is a queue
    that looks like latency rather than an error. The same ceiling bit the in-process
    path through BedrockModel's own pool.

    Retries are botocore's ``standard`` mode with ``max_attempts=2`` -- one retry, on
    throttling and 5xx only. Zero retries makes a single transient 429 cost a whole
    specialist; more than one retry risks doubling the fan-out's tail past the 60s
    budget, since the retry is serial with the call it repeats.
    """
    global _CLIENT
    if _CLIENT is None:
        import boto3  # noqa: PLC0415
        from botocore.config import Config  # noqa: PLC0415

        _CLIENT = boto3.client(
            "bedrock-agentcore",
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
            config=Config(
                max_pool_connections=16,
                read_timeout=int(os.environ.get("SPECIALIST_READ_TIMEOUT", "300")),
                connect_timeout=15,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _CLIENT


def _invoke_sync(arn: str, application_id: str, session_id: str) -> dict[str, Any]:
    """One blocking InvokeAgentRuntime. Called via ``asyncio.to_thread``.

    boto3 is synchronous, and wrapping it in a thread is what lets eight run
    concurrently under the event loop. ``asyncio.gather`` over ``to_thread`` is the
    right primitive here for the same reason it was in-process; what changed is that the
    blocking work is now a signed HTTP call instead of a Bedrock stream.
    """
    kwargs: dict[str, Any] = {
        "agentRuntimeArn": arn,
        # The SAME session id as the orchestrator's, so all nine runtimes appear as one
        # session. AgentCore's floor is 33 characters; the orchestrator's is a uuid4.
        "runtimeSessionId": session_id,
        "qualifier": os.environ.get("SPECIALIST_QUALIFIER", "DEFAULT"),
        "contentType": "application/json",
        "accept": "application/json",
        "payload": json.dumps({"application_id": application_id}).encode(),
        **_trace_headers(),
    }
    response = _client().invoke_agent_runtime(**kwargs)
    body = response["response"]
    raw = body.read() if hasattr(body, "read") else b"".join(body)
    text = raw.decode("utf-8", "replace").strip()

    # The specialist entrypoint returns a dict, so the SDK sends JSON. A non-JSON body
    # means the runtime failed before the entrypoint, and the raw text is the only
    # diagnostic there is -- so it is surfaced rather than swallowed.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"specialist returned non-JSON ({len(text)} bytes): {text[:400]}",
            "error_type": "InvalidResponse",
        }
    if isinstance(parsed, str):
        # Some SDK paths double-encode a string body.
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": f"specialist returned a JSON string, not an object: {parsed[:400]}",
                "error_type": "InvalidResponse",
            }
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "error": f"specialist returned {type(parsed).__name__}, expected an object",
            "error_type": "InvalidResponse",
        }
    return parsed


def _to_frame(domain: str, result: dict[str, Any], invoke_ms: float, started: float) -> tuple[str, dict[str, Any]]:
    """One specialist result as the orchestrator's own ``("done"|"failed", frame)``.

    Deliberately produces the SAME frame shape the in-process path produces, so
    switching topology changes nothing downstream: not the SSE contract, not the UI, not
    the CloudWatch emitter, not the synthesis input.
    """
    from app.runtime_support import agent_name_of  # noqa: PLC0415

    if not result.get("ok"):
        return "failed", {
            "domain": domain,
            "agent_name": result.get("agent_name") or agent_name_of(domain),
            "error": str(result.get("error") or "unknown specialist error"),
        }

    return "done", {
        "domain": domain,
        "agent_name": result.get("agent_name") or agent_name_of(domain),
        "analysis": result.get("analysis"),
        "analysis_title": result.get("analysis_title"),
        "risk_band": result.get("risk_band"),
        "risk_band_form": result.get("risk_band_form"),
        "char_count": result.get("char_count"),
        "model": result.get("model"),
        "tier": result.get("tier"),
        "measured": result.get("measured"),
        "source": result.get("source"),
        "model_latency_ms": result.get("model_latency_ms"),
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "cache_read_tokens": result.get("cache_read_tokens"),
        "cache_write_tokens": result.get("cache_write_tokens"),
        "cost_usd": result.get("cost_usd"),
        "stop_reason": result.get("stop_reason"),
        "prompt_cache": result.get("prompt_cache"),
        # `latency_ms` stays the orchestrator's view so it is comparable with the
        # in-process path's wall clock for the same field.
        "latency_ms": round(invoke_ms, 1),
        "finished_at_ms": round((time.perf_counter() - started) * 1000, 1),
        # The two numbers that only exist in this topology, and the reason to keep it
        # measurable: `remote_elapsed_ms` is the wall clock INSIDE the specialist
        # runtime, so `latency_ms - remote_elapsed_ms` is the AgentCore invocation
        # overhead this split adds per specialist.
        "remote_elapsed_ms": result.get("elapsed_ms"),
        "invocation_overhead_ms": (
            round(invoke_ms - result["elapsed_ms"], 1)
            if isinstance(result.get("elapsed_ms"), (int, float))
            else None
        ),
        "transport": "agentcore-runtime",
    }


async def fan_out_runtimes(
    application_id: str,
    session_id: str,
    domains: list[str],
    started: float,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Invoke each domain's own runtime concurrently; yield in completion order.

    Yields ``("done", frame)`` / ``("failed", frame)`` per domain, then a single
    ``("fanout_completed", frame)`` carrying the wall clock -- the same three kinds the
    in-process generator yields, so ``app/underwriter/main.py`` consumes either without
    branching.
    """
    missing = [d for d in domains if not specialist_arn(d)]
    ready = [d for d in domains if specialist_arn(d)]

    for domain in missing:
        # Not an exception: a partially deployed project should degrade to the
        # specialists that DO exist and say which are absent, exactly as it would for a
        # specialist that failed.
        yield "failed", {
            "domain": domain,
            "error": (
                f"SPECIALIST_ARN_{domain.upper()} is not set on this runtime, so the "
                f"{domain} specialist cannot be reached. Deploy the sibling runtimes, or "
                "set FANOUT_MODE=inprocess to run all eight in this container."
            ),
        }

    async def one(domain: str) -> tuple[str, dict[str, Any]]:
        arn = specialist_arn(domain)
        assert arn is not None  # `ready` filtered these
        call_started = time.perf_counter()
        try:
            result = await asyncio.to_thread(_invoke_sync, arn, application_id, session_id)
        except Exception as exc:  # noqa: BLE001 -- transport failure becomes a frame
            result = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "error_type": type(exc).__name__,
            }
        invoke_ms = (time.perf_counter() - call_started) * 1000
        return _to_frame(domain, result, invoke_ms, started)

    fanout_started = time.perf_counter()
    latencies: dict[str, float] = {}

    tasks = [asyncio.create_task(one(domain), name=f"specialist:{domain}") for domain in ready]
    for finished in asyncio.as_completed(tasks):
        kind, frame = await finished
        if kind == "done" and isinstance(frame.get("latency_ms"), (int, float)):
            latencies[str(frame["domain"])] = float(frame["latency_ms"])
        yield kind, frame

    wall_ms = (time.perf_counter() - fanout_started) * 1000
    slowest = max(latencies, key=lambda d: latencies[d]) if latencies else None
    yield "fanout_completed", {
        "wall_ms": round(wall_ms, 1),
        "slowest": slowest,
        "slowest_ms": round(latencies[slowest], 1) if slowest else None,
        "sum_of_agent_ms": round(sum(latencies.values()), 1) if latencies else None,
        "domains_invoked": ready,
        "domains_unreachable": missing,
        "transport": "agentcore-runtime",
    }
