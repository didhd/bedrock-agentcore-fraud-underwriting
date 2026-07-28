"""
The eight-way specialist fan-out: real asyncio concurrency, measured telemetry.

WHY THIS FILE REPLACES THE THREAD POOL. The previous revision ran the eight
specialists through ``concurrent.futures.ThreadPoolExecutor`` over synchronous
``Agent.__call__``. Three things are wrong with that on Strands 1.26.0:

1. ``BedrockModel.stream`` already offloads its blocking boto3 call through
   ``asyncio.to_thread``, so a thread pool wrapping a coroutine runner adds a
   layer of threads that buys nothing. ``asyncio.gather`` over eight distinct
   agents is the primitive that matches the runtime.
2. A single ``Agent`` instance cannot be invoked concurrently - ``stream_async``
   takes a ``threading.Lock`` and raises ``ConcurrencyException``. So an agent is
   built **per specialist per invocation**. Nothing here is module-level, and the
   fresh instance also keeps ``metrics.accumulated_usage`` per-request rather
   than cumulative.
3. Two ceilings sit under the fan-out and both are invisible until you hit them.
   ``BedrockModel``'s botocore default is ``max_pool_connections=10``, so eight
   specialists x N concurrent applications queue on the HTTP pool
   (``agents.models.shared_bedrock_client`` raises it and shares one client). And
   ``asyncio.to_thread`` dispatches onto the loop's *default executor*, sized
   ``min(32, cpu_count + 4)`` - on an 11-core box that is 15 threads, which caps
   throughput at roughly two applications in flight regardless of the pool. This
   module widens that executor once per loop (:func:`widen_default_executor`) and
   gates admission with an ``asyncio.Semaphore`` so the queue forms in Python
   where it is observable, not inside botocore where it is not.

WHAT AN AGENT IS ALLOWED TO SEE. Each specialist receives only its own
``DomainView``, rendered by ``signal_layer.schema.render_for_agent``, and is given
**no tools at all**. That is the customer's own architecture
(Agent_Underwriting_Process_Guide 2.2: signals exist "to allow for ease of review
and optimize processing time by preventing the agents from making the same SQL
queries for each app"), and it is also what every specialist prompt demands - "Do
NOT recompute signals", "Do NOT use application_id for matching / querying the
database". An agent with a data tool can violate those rules; an agent without one
cannot.

WHAT IS NOT PARSED. The customer's specialists return prose - six with an exact
title, bustout and rings with none ("Return only the final analysis." /
"Return only final analysis.") and no length cap. This module stores that prose
verbatim in ``SpecialistResult.analysis`` and does **not** extract a risk band
from it. The old code scanned for a line starting ``RISK:`` and defaulted to
``"LOW RISK"`` when it found none, which turns every real specialist response into
a clean verdict. Band extraction, where a UI genuinely needs one, is a separate
concern with a separate cost; it is not a string split with a silent default.

WHAT DEGRADES. ``asyncio.gather(..., return_exceptions=True)`` plus a per-agent
try/except means one failed dimension does not lose the other seven: the failure
is recorded on the result as ``error`` and the adjudication proceeds with a
documented gap. Seven of eight still adjudicates.

Every number on a :class:`SpecialistResult` is measured. ``usage`` is Bedrock's
own ``accumulated_usage`` (cache keys read with ``.get()``, because they are
optional in the TypedDict), ``latency_ms`` is
``accumulated_metrics['latencyMs']``, ``wall_ms`` is this process's clock, and
``cost_usd`` comes from ``agents.pricing`` applied to those tokens. In MOCK_MODE
``usage`` is ``None`` and ``cost_usd`` is ``None`` - never a plausible-looking
guess.
"""

from __future__ import annotations

import asyncio
import os
import time
import weakref
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Final

from agents.models import (
    MAX_TOKENS_BY_TIER,
    SPECIALIST_MODELS,
    build_bedrock_model,
    build_retry_strategy,
    cache_prefix_report,
    cached_system_prompt,
    model_for,
    tier_label,
)
from agents.pricing import cost_usd_or_none
from prompts.loader import AGENT_NAMES, DOMAINS, SPECIALIST_PROMPTS

__all__ = [
    "ANALYSIS_TITLES",
    "DEFAULT_MAX_CONCURRENCY",
    "EXECUTOR_MAX_WORKERS",
    "FanOutError",
    "MOCK_MODE",
    "SpecialistResult",
    "fan_out",
    "fan_out_streaming",
    "mock_analysis",
    "render_domain_input",
    "widen_default_executor",
]

#: Offline by default: nothing in this repo may require AWS to run its tests.
MOCK_MODE: Final[bool] = os.environ.get("MOCK_MODE", "1") == "1"

#: Exact ``- Title:`` line from each specialist prompt's OUTPUT block. bustout and
#: rings define none - their prompts say "Return only the final analysis." /
#: "Return only final analysis." - so they map to ``None`` and no title is
#: invented for them.
ANALYSIS_TITLES: Final[dict[str, str | None]] = {
    "identity": "Identity Fraud Risk Analysis",
    "dealer": "Dealer Fraud Risk Analysis",
    "straw": "Straw Borrower Fraud Risk Analysis",
    "employment": "Employment Fraud Risk Analysis",
    "income": "Income Fraud Risk Analysis",
    "synthetic": "Synthetic Identity Fraud Risk Analysis",
    "bustout": None,
    "rings": None,
}

#: Admission gate for the fan-out. Eight is one full application in flight; a
#: caller running batches raises it and must raise the botocore pool with it.
DEFAULT_MAX_CONCURRENCY: Final[int] = int(os.environ.get("FANOUT_MAX_CONCURRENCY", "8"))

#: Replacement size for the event loop's default executor. ``asyncio.to_thread``
#: - which is how ``BedrockModel.stream`` reaches boto3 - runs there, and its
#: default ``min(32, cpu_count + 4)`` is a second ceiling behind
#: ``max_pool_connections``. Sized above the pool so the pool stays the binding
#: constraint and queueing is visible in the semaphore.
EXECUTOR_MAX_WORKERS: Final[int] = int(os.environ.get("FANOUT_EXECUTOR_WORKERS", "64"))

#: The prompt line each specialist gets above its domain view. Deliberately not a
#: rewrite of any instruction: the prompts already say what to do, and this only
#: names what follows.
_INPUT_HEADER: Final[str] = "APPLICATION DATA:"


class FanOutError(RuntimeError):
    """Raised when the fan-out cannot run at all (as opposed to one agent failing)."""


@dataclass(slots=True)
class SpecialistResult:
    """One specialist's output plus everything measured about producing it.

    ``analysis`` is the customer's prose, unparsed. ``usage`` is ``None`` whenever
    no model was called (MOCK_MODE, or a failure before the first token), and
    ``cost_usd`` is ``None`` whenever ``usage`` is - a missing measurement is
    reported as missing.
    """

    domain: str
    agent_name: str
    analysis: str
    model_id: str
    tier: str
    wall_ms: float
    latency_ms: float | None = None
    usage: dict[str, int] | None = None
    cost_usd: float | None = None
    stop_reason: str | None = None
    title: str | None = None
    error: str | None = None
    source: str = "bedrock"
    prompt_cache: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether this dimension produced an analysis."""
        return self.error is None

    def to_event(self) -> dict[str, Any]:
        """Flatten into the ``agent_completed``/``agent_failed`` streaming frame."""
        event: dict[str, Any] = {
            "event": "agent_failed" if self.error else "agent_completed",
            "domain": self.domain,
            "agent_name": self.agent_name,
            "analysis": self.analysis,
            "analysis_title": self.title,
            "model": self.model_id,
            "tier": self.tier,
            "wall_ms": round(self.wall_ms, 1),
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms, 1),
            "usage": self.usage,
            "cost_usd": self.cost_usd,
            "stop_reason": self.stop_reason,
            "source": self.source,
            "prompt_cache": self.prompt_cache,
        }
        if self.error:
            event["error"] = self.error
        return event


# ---------------------------------------------------------------------------
# Executor widening
# ---------------------------------------------------------------------------

#: One widened executor per loop, so repeated ``fan_out`` calls on the same loop
#: do not leak thread pools. Keyed weakly: a closed loop's executor becomes
#: collectable with it.
_widened: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, ThreadPoolExecutor]" = (
    weakref.WeakKeyDictionary()
)


def widen_default_executor(max_workers: int = EXECUTOR_MAX_WORKERS) -> ThreadPoolExecutor:
    """Install a wider default executor on the running loop, once.

    ``asyncio.to_thread`` uses the loop's default executor, which Python sizes at
    ``min(32, cpu_count + 4)``. That is the hidden second ceiling on an 8-way
    fan-out: on an 11-core host it is 15 threads, so two concurrent applications
    saturate it while the botocore pool still looks idle. Idempotent per loop.
    """
    loop = asyncio.get_running_loop()
    existing = _widened.get(loop)
    if existing is not None:
        return existing
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pp-fanout")
    loop.set_default_executor(executor)
    _widened[loop] = executor
    return executor


# ---------------------------------------------------------------------------
# Domain input projection
# ---------------------------------------------------------------------------


def render_domain_input(payload: Any, domain: str) -> str:
    """Project ``payload`` down to the one domain view this specialist may see.

    Delegates to ``signal_layer.schema.render_for_agent``, which owns the
    projection contract (SIGBOUND-01/02: an agent sees only its own domain's
    signals). Import is local so this module stays importable while the signal
    layer is still landing; the fallback is a plain ``str`` of whatever was
    passed, never a widened view of another domain's signals.
    """
    try:
        from signal_layer.schema import render_for_agent
    except Exception:
        if isinstance(payload, str):
            return payload
        raise FanOutError(
            "signal_layer.schema.render_for_agent is unavailable and payload is not "
            "already-rendered text. Refusing to hand a specialist an unprojected "
            "payload: an agent must never see another domain's signals."
        ) from None
    return render_for_agent(payload, domain)


# ---------------------------------------------------------------------------
# MOCK_MODE analyses
# ---------------------------------------------------------------------------

#: Deterministic offline analyses in the customer's REAL output shape: a title
#: line for the six agents whose prompts define one, no title for bustout and
#: rings, prose sentences, no invented "RISK:" header, no emoji. They exist so an
#: offline run exercises the true contract (a title-bearing narrative that the
#: master prompt must synthesize) rather than a shape the customer never emits.
#: The sentences state plainly that no model ran.
_MOCK_BODIES: Final[dict[str, str]] = {
    "identity": (
        "No model was called for this analysis; this is deterministic offline text "
        "standing in for a specialist narrative. The identity signals in the domain "
        "view are reproduced without recomputation and each one is read against its "
        "paired context field. Nothing in the projected view is treated as "
        "corroborated in the absence of a model judgement. The clearing conclusion "
        "and its key mitigating factor are what a live run would state here."
    ),
    "dealer": (
        "No model was called for this analysis; this is deterministic offline text "
        "standing in for a specialist narrative. Dealer portfolio metrics in the "
        "domain view are reported factually as credit-risk context and are not "
        "treated as evidence of dealer-orchestrated fraud. Counts are left "
        "unnormalized because normalization is a model judgement. A live run would "
        "state the clearing conclusion here."
    ),
    "straw": (
        "No model was called for this analysis; this is deterministic offline text "
        "standing in for a specialist narrative. Co-borrower presence is read from "
        "the domain view without recomputing clustering or identity linkage. A live "
        "run would state the clearing conclusion here."
    ),
    "employment": (
        "No model was called for this analysis; this is deterministic offline text "
        "standing in for a specialist narrative. Missing employer address, phone, "
        "city, state or ZIP values in the domain view are treated as underwriting "
        "data gaps rather than deception. No escalation is derived from incomplete "
        "employer records. A live run would state the clearing conclusion and the "
        "key factor here."
    ),
    "income": (
        "No model was called for this analysis; this is deterministic offline text "
        "standing in for a specialist narrative. Stated and verified income figures "
        "are read from the domain view without recomputing ratios or trends. "
        "Same-behaviour income signals are left collapsed into a single concern. A "
        "live run would state the clearing conclusion and the key mitigating factor "
        "here."
    ),
    "synthetic": (
        "No model was called for this analysis; this is deterministic offline text "
        "standing in for a specialist narrative. File-age and credit-construction "
        "signals in the domain view are read against their context fields without "
        "recomputing clustering, velocity or reuse. A live run would state the "
        "clearing conclusion and the key mitigating factor here."
    ),
    "bustout": (
        "No model was called for this analysis; this is deterministic offline text "
        "standing in for a specialist narrative. Application velocity and loan "
        "exposure values in the domain view are reproduced as provided and validated "
        "only against the context fields present. Velocity, income trends and "
        "exposure patterns are not recomputed and no absent signal is introduced. "
        "Alert activity in the view is treated as supporting context requiring "
        "validation rather than independent proof."
    ),
    "rings": (
        "No model was called for this analysis; this is deterministic offline text "
        "standing in for a specialist narrative. Shared-identifier counts in the "
        "domain view are read against their context fields and network "
        "relationships are not recomputed. Broad overlap alone is not treated as "
        "coordinated activity. Alert activity in the view is treated as supporting "
        "context requiring validation rather than independent proof."
    ),
}


def mock_analysis(domain: str) -> str:
    """Deterministic offline analysis for ``domain``, in the customer's shape."""
    body = _MOCK_BODIES[domain]
    title = ANALYSIS_TITLES[domain]
    return f"{title}\n\n{body}" if title else body


# ---------------------------------------------------------------------------
# One specialist
# ---------------------------------------------------------------------------


def _usage_from(result: Any) -> dict[str, int] | None:
    """Read ``accumulated_usage`` off an AgentResult, cache keys via ``.get()``.

    ``inputTokens``/``outputTokens``/``totalTokens`` are required in Bedrock's
    ``Usage`` TypedDict; ``cacheReadInputTokens``/``cacheWriteInputTokens`` are
    optional and absent whenever caching did not engage - which is the normal case
    below a model's minimum cacheable prefix.
    """
    metrics = getattr(result, "metrics", None)
    usage = getattr(metrics, "accumulated_usage", None) if metrics is not None else None
    if not isinstance(usage, Mapping):
        return None
    if "inputTokens" not in usage or "outputTokens" not in usage:
        return None
    return {
        "inputTokens": int(usage["inputTokens"]),
        "outputTokens": int(usage["outputTokens"]),
        "totalTokens": int(usage.get("totalTokens", 0)),
        "cacheReadInputTokens": int(usage.get("cacheReadInputTokens", 0)),
        "cacheWriteInputTokens": int(usage.get("cacheWriteInputTokens", 0)),
    }


def _latency_from(result: Any) -> float | None:
    """Read ``accumulated_metrics['latencyMs']`` off an AgentResult."""
    metrics = getattr(result, "metrics", None)
    accumulated = getattr(metrics, "accumulated_metrics", None) if metrics is not None else None
    if isinstance(accumulated, Mapping):
        value = accumulated.get("latencyMs")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _analysis_text(result: Any) -> str:
    """Concatenate the text blocks of the agent's final message."""
    message = getattr(result, "message", None) or {}
    parts = [
        block["text"]
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "\n".join(parts).strip() if parts else str(result).strip()


def _build_agent(domain: str, model_id: str) -> Any:
    """Construct a single-use ``Agent`` for one specialist on one invocation.

    Deliberate choices, each one a defect in the previous revision:
      * fresh instance - a shared ``Agent`` raises ``ConcurrencyException`` under
        ``stream_async``, and a reused one reports cumulative token usage;
      * ``tools=[]`` - the specialist must not query (SIGBOUND-01, and every
        prompt's "Do NOT recompute" / "Do NOT use application_id" rules);
      * ``callback_handler=None`` - the default ``PrintingCallbackHandler`` writes
        every token to stdout, which floods CloudWatch;
      * ``NullConversationManager`` - single-shot, no history to manage;
      * cached system prompt - a ``SystemContentBlock`` list with a ``cachePoint``,
        which is a no-op below the model's minimum prefix but never a penalty.
    """
    from strands import Agent
    from strands.agent import NullConversationManager

    return Agent(
        model=build_bedrock_model(
            model_id, max_tokens=MAX_TOKENS_BY_TIER.get(tier_label(model_id), 2048)
        ),
        system_prompt=cached_system_prompt(SPECIALIST_PROMPTS[domain]),
        tools=[],
        callback_handler=None,
        conversation_manager=NullConversationManager(),
        retry_strategy=build_retry_strategy(),
        name=AGENT_NAMES[domain],
        agent_id=AGENT_NAMES[domain],
    )


async def _run_specialist(
    domain: str,
    domain_input: str,
    *,
    semaphore: asyncio.Semaphore,
    mock: bool,
) -> SpecialistResult:
    """Run one specialist, converting any failure into a recorded result.

    Never raises: the caller needs seven-of-eight to still adjudicate, so a
    failure here is data (``error`` set, ``usage`` None), not control flow.
    """
    model_id = model_for(domain)
    tier = tier_label(model_id)
    title = ANALYSIS_TITLES[domain]
    cache_report = cache_prefix_report(model_id, SPECIALIST_PROMPTS[domain])
    started = time.perf_counter()

    if mock:
        async with semaphore:
            return SpecialistResult(
                domain=domain,
                agent_name=AGENT_NAMES[domain],
                analysis=mock_analysis(domain),
                model_id=model_id,
                tier=tier,
                wall_ms=(time.perf_counter() - started) * 1000.0,
                latency_ms=None,
                usage=None,
                cost_usd=None,
                stop_reason=None,
                title=title,
                source="mock",
                prompt_cache=cache_report,
            )

    try:
        async with semaphore:
            agent = _build_agent(domain, model_id)
            result = await agent.invoke_async(f"{_INPUT_HEADER}\n{domain_input}")
        usage = _usage_from(result)
        return SpecialistResult(
            domain=domain,
            agent_name=AGENT_NAMES[domain],
            analysis=_analysis_text(result),
            model_id=model_id,
            tier=tier,
            wall_ms=(time.perf_counter() - started) * 1000.0,
            latency_ms=_latency_from(result),
            usage=usage,
            cost_usd=cost_usd_or_none(model_id, usage),
            stop_reason=getattr(result, "stop_reason", None),
            title=title,
            source="bedrock",
            prompt_cache=cache_report,
        )
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 - one dimension must not lose seven
        return SpecialistResult(
            domain=domain,
            agent_name=AGENT_NAMES[domain],
            analysis="",
            model_id=model_id,
            tier=tier,
            wall_ms=(time.perf_counter() - started) * 1000.0,
            latency_ms=None,
            usage=None,
            cost_usd=None,
            stop_reason=None,
            title=title,
            error=f"{type(exc).__name__}: {exc}",
            source="bedrock",
            prompt_cache=cache_report,
        )


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------


def _resolve_domains(domains: Sequence[str] | None) -> tuple[str, ...]:
    if domains is None:
        return DOMAINS
    unknown = [d for d in domains if d not in SPECIALIST_MODELS]
    if unknown:
        raise FanOutError(f"unknown fraud domain(s): {unknown}; known: {list(DOMAINS)}")
    # Preserve the customer's master-prompt order regardless of argument order.
    return tuple(d for d in DOMAINS if d in set(domains))


def _project(
    payload: Any, resolved: tuple[str, ...], renderer: Callable[[Any, str], str]
) -> dict[str, str]:
    """Render every domain view up front, so a projection bug fails before any call."""
    return {domain: renderer(payload, domain) for domain in resolved}


async def fan_out(
    payload: Any,
    *,
    domains: Sequence[str] | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    mock: bool | None = None,
    renderer: Callable[[Any, str], str] = render_domain_input,
) -> dict[str, SpecialistResult]:
    """Run all eight specialists concurrently and return every result.

    Returns one :class:`SpecialistResult` per requested domain, keyed by domain,
    in the customer's master-prompt order. A specialist that failed is present
    with ``ok is False`` and its ``error`` set, so the caller can adjudicate on
    seven and report the gap. This function raises only when the fan-out itself
    cannot run (unknown domain, unprojectable payload).
    """
    resolved = _resolve_domains(domains)
    use_mock = MOCK_MODE if mock is None else mock
    domain_inputs = _project(payload, resolved, renderer)

    if not use_mock:
        widen_default_executor()
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    results = await asyncio.gather(
        *(
            _run_specialist(
                domain, domain_inputs[domain], semaphore=semaphore, mock=use_mock
            )
            for domain in resolved
        ),
        return_exceptions=True,
    )

    out: dict[str, SpecialistResult] = {}
    for domain, result in zip(resolved, results, strict=True):
        if isinstance(result, SpecialistResult):
            out[domain] = result
            continue
        # _run_specialist swallows its own failures, so reaching here means the
        # coroutine itself broke. Record it the same way rather than dropping the
        # key: callers index by domain.
        out[domain] = SpecialistResult(
            domain=domain,
            agent_name=AGENT_NAMES[domain],
            analysis="",
            model_id=model_for(domain),
            tier=tier_label(model_for(domain)),
            wall_ms=0.0,
            error=f"{type(result).__name__}: {result}",
            title=ANALYSIS_TITLES[domain],
            source="mock" if use_mock else "bedrock",
        )
    return out


async def fan_out_streaming(
    payload: Any,
    *,
    domains: Sequence[str] | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    mock: bool | None = None,
    renderer: Callable[[Any, str], str] = render_domain_input,
) -> AsyncIterator[dict[str, Any]]:
    """Yield fan-out events as each specialist finishes.

    Frame sequence: one ``agent_started`` per domain up front, then one
    ``agent_completed`` or ``agent_failed`` per domain **in completion order**,
    then a single ``fanout_completed`` carrying the wall clock and the slowest
    dimension. Shaped for an async-generator AgentCore entrypoint, where each
    yielded object is serialized to ``data: <json>\\n\\n`` automatically.

    Note the AgentCore streaming caveat this shape is designed for: an exception
    raised mid-stream is swallowed into a terminal ``data: {"error": ...}`` frame
    *after* HTTP 200. A client must inspect every frame for ``error``, which is
    exactly what ``agent_failed`` makes routine rather than exceptional.
    """
    resolved = _resolve_domains(domains)
    use_mock = MOCK_MODE if mock is None else mock
    domain_inputs = _project(payload, resolved, renderer)

    if not use_mock:
        widen_default_executor()
    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    started = time.perf_counter()

    for domain in resolved:
        model_id = model_for(domain)
        yield {
            "event": "agent_started",
            "domain": domain,
            "agent_name": AGENT_NAMES[domain],
            "model": model_id,
            "tier": tier_label(model_id),
            "at_ms": round((time.perf_counter() - started) * 1000.0, 1),
        }

    tasks = [
        asyncio.create_task(
            _run_specialist(domain, domain_inputs[domain], semaphore=semaphore, mock=use_mock),
            name=f"specialist-{domain}",
        )
        for domain in resolved
    ]

    completed: dict[str, SpecialistResult] = {}
    try:
        for future in asyncio.as_completed(tasks):
            result = await future
            completed[result.domain] = result
            event = result.to_event()
            event["finished_at_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
            yield event
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

    wall_ms = (time.perf_counter() - started) * 1000.0
    latencies = {d: r.wall_ms for d, r in completed.items()}
    slowest = max(latencies, key=lambda k: latencies[k]) if latencies else None
    costs = [r.cost_usd for r in completed.values()]
    yield {
        "event": "fanout_completed",
        "wall_ms": round(wall_ms, 1),
        "slowest_domain": slowest,
        "slowest_domain_wall_ms": round(latencies[slowest], 1) if slowest else None,
        "sum_of_agent_wall_ms": round(sum(latencies.values()), 1) if latencies else None,
        "succeeded": [d for d, r in completed.items() if r.ok],
        "failed": [d for d, r in completed.items() if not r.ok],
        # None, not 0.0, when any dimension has no measured usage: a partial sum
        # presented as a total is the failure mode this whole module exists to fix.
        "agent_cost_usd": sum(costs) if costs and all(c is not None for c in costs) else None,
        "source": "mock" if use_mock else "bedrock",
    }
