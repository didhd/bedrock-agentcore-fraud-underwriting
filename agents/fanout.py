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

WHY THERE ARE TWO TRANSPORTS, AND WHY THE MODEL ID PICKS ONE. Six of the nine
agents' model ids address Claude on Bedrock Converse; ``openai.gpt-5.*`` ids are
NOT on Converse at all (``converse`` rejects them with ``ValidationException: The
provided model identifier is invalid``) and are only reachable through the
``bedrock-mantle`` Responses endpoint. So a specialist assignment alone decides the
transport, exactly as it already does for the synthesizer:
``agents.mantle.is_mantle_model(model_id)`` routes to
:func:`_invoke_specialist_mantle`, everything else to Strands. There is no flag and
no second switch - ``agents.models.SPECIALIST_MODELS`` is the only place the
decision is expressed, and :func:`transport_for` is the one function that reads it.

This is the same pattern ``agents.synthesize._invoke_master_mantle`` uses, including
its ``_MantleResult`` adapter: the mantle response is wrapped in an object carrying
the surface the Converse path already returns (``message``, ``usage``,
``latency_ms``, ``stop_reason``) so ``_usage_from`` / ``_latency_from`` /
``_analysis_text`` and the whole result-assembly path are SHARED between the two
families rather than duplicated per family.

Three things the mantle path cannot honestly report, and does not:

* ``stop_reason``. ``mantle.complete`` returns ``{text, usage, latency_ms, ...}``
  and does not surface the Responses ``status``, so there is no stop reason to
  read. It is ``None`` (unknown), never a plausible ``"end_turn"``.
* a Converse cache verdict. ``cached_system_prompt`` / ``cache_prefix_report`` are
  Converse concepts - a ``cachePoint`` block and a per-model minimum cacheable
  prefix. The Responses API uses explicit breakpoints instead and GPT-5.6 also
  caches implicitly, and this path sends no breakpoint either way. So a mantle
  specialist carries :func:`_mantle_cache_report`, whose ``will_cache`` is ``None``
  (unknown by construction) and which records the MEASURED
  ``cacheRead``/``cacheWrite`` tokens once the call has returned. Reporting
  ``will_cache: False`` with a "cache points are dropped" reason - which is what
  ``cache_prefix_report`` says about a non-Claude id - would be a Converse verdict
  about a request that never contained a cachePoint.
* a free call. Cost comes from ``agents.mantle.cost_usd``, which prices
  cache-WRITE tokens at 1.25x input; ``agents.pricing`` delegates GPT ids to the
  same function, so the two agree, and going straight to mantle keeps one rate
  table per family. An unpriced GPT id raises there, which surfaces here as a
  recorded specialist error rather than as a call reported at $0.

WHAT DEGRADES. ``asyncio.gather(..., return_exceptions=True)`` plus a per-agent
try/except means one failed dimension does not lose the other seven: the failure
is recorded on the result as ``error`` and the adjudication proceeds with a
documented gap. Seven of eight still adjudicates. That holds for both transports:
``mantle.complete`` is synchronous, so it is awaited through
``asyncio.to_thread`` - which keeps the single ``asyncio.gather`` and its semaphore
intact rather than serialising the fan-out behind a blocking call - and a
``MantleUnavailable`` from it is recorded exactly like a Bedrock exception.

Every number on a :class:`SpecialistResult` is measured. ``usage`` is Bedrock's
own ``accumulated_usage`` (cache keys read with ``.get()``, because they are
optional in the TypedDict), ``latency_ms`` is
``accumulated_metrics['latencyMs']``, ``wall_ms`` is this process's clock, and
``cost_usd`` comes from ``agents.pricing`` applied to those tokens. On the mantle
path the same three fields carry the same meanings from ``mantle.complete``'s own
return value: its ``usage`` is already normalised to the Converse convention
(``inputTokens`` EXCLUDES cached tokens, so a cached token is billed once at the
cache rate) and its ``latency_ms`` is the endpoint round trip, which is the closest
available analogue of Converse's model latency and is narrower than ``wall_ms`` for
the same reason. In MOCK_MODE ``usage`` is ``None`` and ``cost_usd`` is ``None`` -
never a plausible-looking guess. ``source`` says which transport produced the row
(``bedrock`` / ``bedrock-mantle`` / ``mock``) so a report can separate them.
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
    estimate_prompt_tokens,
    model_for,
    tier_label,
)
from agents.pricing import cost_usd_or_none
from prompts.loader import AGENT_NAMES, DOMAINS, SPECIALIST_PROMPTS

__all__ = [
    "ANALYSIS_TITLES",
    "DEFAULT_MAX_CONCURRENCY",
    "EXECUTOR_MAX_WORKERS",
    "SOURCE_BEDROCK",
    "SOURCE_MANTLE",
    "SOURCE_MOCK",
    "FanOutError",
    "MOCK_MODE",
    "SpecialistResult",
    "fan_out",
    "fan_out_streaming",
    "mock_analysis",
    "render_domain_input",
    "transport_for",
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

#: ``SpecialistResult.source`` values. Three, not two: a report that pools a Converse
#: call and a Responses call under one "bedrock" label cannot tell which family
#: produced a row, and the two differ in what they can even measure (no
#: ``stop_reason`` and no Converse cache verdict on the mantle path). ``evals`` and the
#: UI already carry this field through to their JSON, so distinguishing it here is
#: enough for a report to separate the sets.
SOURCE_BEDROCK: Final[str] = "bedrock"
SOURCE_MANTLE: Final[str] = "bedrock-mantle"
SOURCE_MOCK: Final[str] = "mock"


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


def _specialist_cost(model_id: str, result: Any, usage: dict[str, int] | None) -> float | None:
    """Cost of one specialist call, from whichever family answered.

    ``agents.pricing`` now DELEGATES a GPT id to ``agents.mantle`` (``pricing._is_mantle``
    -> ``_mantle_rates``), so ``cost_usd_or_none`` and ``mantle.cost_usd`` agree to the
    cent on a mantle usage dict - checked by
    ``test_mantle_cost_agrees_with_the_pricing_module``. That makes the choice here a
    question of which one is CORRECT to call rather than which number is right:

    * ``mantle.complete`` already priced the call and put it in the response, so reading
      that back is one computation per call instead of two that could drift.
    * ``pricing.cost_usd_or_none`` only swallows ``MissingUsageError``. For an unpriced
      GPT id it would propagate ``MantleUnavailable`` from ``mantle.rates_for`` - correct
      behaviour (refusing to guess a rate) but it would turn a priced-analysis question
      into a lost fraud dimension. The adapter's value is already computed before we get
      here, so that failure surfaces at the call, not at costing time.

    Falls back to recomputing from ``usage`` when the adapter carried no cost, so a
    ``None`` here always means "usage unknown" and never "we forgot to price it".
    """
    direct = getattr(result, "cost_usd", None)
    if isinstance(direct, (int, float)):
        return float(direct)
    if transport_for(model_id) == SOURCE_MANTLE:
        from agents import mantle  # noqa: PLC0415

        return mantle.cost_usd(model_id, usage)
    return cost_usd_or_none(model_id, usage)


def _analysis_text(result: Any) -> str:
    """Concatenate the text blocks of the agent's final message."""
    message = getattr(result, "message", None) or {}
    parts = [
        block["text"]
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "\n".join(parts).strip() if parts else str(result).strip()


def _max_output_tokens(model_id: str) -> int:
    """Output cap for one specialist call, resolved identically for both transports.

    Factored out of ``_build_agent`` so the mantle path cannot drift from the Converse
    one. A GPT id is not in ``TIER_OF``, so ``tier_label`` returns ``"custom"`` and the
    cap is ``MAX_TOKENS_BY_TIER['custom']`` - the same 4096 the standalone probe used.
    """
    return MAX_TOKENS_BY_TIER.get(tier_label(model_id), 2048)


def transport_for(model_id: str) -> str:
    """Which transport ``model_id`` must use: :data:`SOURCE_MANTLE` or :data:`SOURCE_BEDROCK`.

    The single decision point. ``agents.mantle.is_mantle_model`` owns the predicate (it
    matches ``openai.gpt-5*`` and deliberately NOT ``openai.gpt-oss-*``, which really is
    on Converse), and this function exists so the routing is one importable thing a test
    and a report can both read rather than an ``if`` buried in the call path.
    """
    from agents import mantle  # noqa: PLC0415 - keeps the openai SDK off the import path

    return SOURCE_MANTLE if mantle.is_mantle_model(model_id) else SOURCE_BEDROCK


def _mantle_cache_report(
    model_id: str, prompt: str, usage: Mapping[str, int] | None = None
) -> dict[str, Any]:
    """Honest prompt-cache record for a mantle specialist. ``will_cache`` is None.

    ``agents.models.cache_prefix_report`` answers a CONVERSE question - "does the
    ``cachePoint`` block in this system prompt clear the model's minimum cacheable
    prefix?" - and for a non-Claude id it answers ``will_cache: False, reason: 'cache
    points are dropped'``. Reporting that for a GPT-5.6 call would be wrong twice over:
    this path sends no ``cachePoint`` at all (the Responses API takes explicit
    breakpoints instead), and GPT-5.6 additionally caches IMPLICITLY, so "False" would
    contradict the ``cacheWriteInputTokens`` the endpoint really reports - a measured
    Luna call returned 6740 of them.

    So ``will_cache`` is ``None`` (unknown, and unknowable before the call) rather than a
    verdict, ``cache_applied`` states plainly that no breakpoint was sent, and once the
    call has returned the MEASURED cache token counts are recorded here from ``usage``.
    They are the only trustworthy statement available about caching on this path.
    """
    report: dict[str, Any] = {
        "model_id": model_id,
        "transport": SOURCE_MANTLE,
        "prompt_tokens": estimate_prompt_tokens(prompt),
        "prompt_tokens_estimated": True,
        # Both None because both are Converse concepts that do not apply, not because
        # they are zero: there is no cachePoint minimum for a Responses call.
        "min_cacheable_tokens": None,
        "will_cache": None,
        "cache_applied": False,
        "cache_mechanism": "responses-api-explicit-breakpoint",
        "reason": (
            "prompt caching is NOT applied on this call. cachePoint blocks and the "
            "minimum cacheable prefix are Bedrock Converse concepts; GPT-5.6 on the "
            "bedrock-mantle Responses endpoint uses explicit cache breakpoints, and this "
            "path sends none. GPT-5.6 also caches implicitly, so any cacheRead/"
            "cacheWrite tokens below are the endpoint's own measurement and are priced "
            "by agents.mantle.cost_usd (cache WRITE at 1.25x input)."
        ),
    }
    if usage is not None:
        report["measured_cache_read_tokens"] = int(usage.get("cacheReadInputTokens", 0) or 0)
        report["measured_cache_write_tokens"] = int(usage.get("cacheWriteInputTokens", 0) or 0)
    return report


def _cache_report(model_id: str, domain: str) -> dict[str, Any]:
    """Pre-flight prompt-cache report for one specialist, per transport."""
    prompt = SPECIALIST_PROMPTS[domain]
    if transport_for(model_id) == SOURCE_MANTLE:
        return _mantle_cache_report(model_id, prompt)
    return cache_prefix_report(model_id, prompt)


class _MantleSpecialistResult:
    """Adapter giving a mantle response the shape the Converse path already returns.

    Same device as ``agents.synthesize._MantleResult``, and for the same reason: the
    result-assembly code below (``_analysis_text``, ``_usage_from``, ``_latency_from``,
    the ``SpecialistResult`` construction) is then SHARED by both families instead of
    written twice. Here the adapter mimics ``AgentResult`` rather than a structured-output
    result, because a specialist returns prose and is not parsed - so it exposes
    ``message`` in Converse's content-block form and ``metrics`` in Strands'
    ``accumulated_usage`` / ``accumulated_metrics`` form, and the extractors need no
    mantle-specific branch at all.

    ``stop_reason`` is ``None`` on purpose. ``mantle.complete`` does not surface the
    Responses ``status``, so there is no stop reason to report and ``"end_turn"`` would be
    a guess about whether the answer is complete - the one thing a truncation check
    actually needs to be true.
    """

    __slots__ = ("message", "metrics", "stop_reason", "usage", "latency_ms", "cost_usd", "region")

    def __init__(self, response: Mapping[str, Any]) -> None:
        text = response.get("text") or ""
        self.message = {"role": "assistant", "content": [{"text": text}]}
        self.usage = response.get("usage")
        self.latency_ms = response.get("latency_ms")
        # Already computed by mantle.complete with agents.mantle.cost_usd - the same
        # function agents.pricing delegates GPT ids to (verified equal), so taking it
        # here is one computation per call rather than two that could disagree. Note
        # pricing.cost_usd_or_none would RAISE MantleUnavailable for an unpriced GPT id
        # rather than return None, because it only swallows MissingUsageError.
        self.cost_usd = response.get("cost_usd")
        self.region = response.get("region")
        self.stop_reason = None
        self.metrics = _MantleMetricsView(
            accumulated_usage=dict(self.usage) if self.usage else None,
            accumulated_metrics=(
                {} if self.latency_ms is None else {"latencyMs": float(self.latency_ms)}
            ),
        )


@dataclass(slots=True)
class _MantleMetricsView:
    """Strands ``AgentResult.metrics`` shape, so the extractors need no second branch."""

    accumulated_usage: dict[str, int] | None
    accumulated_metrics: dict[str, float]


async def _invoke_specialist_mantle(domain: str, model_id: str, domain_input: str) -> Any:
    """One specialist invocation against a GPT-5.x model on the bedrock-mantle endpoint.

    ``mantle.complete`` is SYNCHRONOUS (the OpenAI SDK's ``responses.create`` blocks), so
    it is awaited through ``asyncio.to_thread``. That is load-bearing, not tidiness:
    calling it directly would block the event loop for the whole call and turn the
    ``asyncio.gather`` below into a serial loop - eight specialists at ~4s each would take
    ~32s instead of ~4s, and the semaphore would never queue anything because nothing
    else could run. ``widen_default_executor`` sizes the pool those threads come from.

    The Responses API takes ONE input string where Converse takes a system prompt plus a
    user turn, so the customer's verbatim prompt and the same ``_INPUT_HEADER`` block the
    Converse path sends are joined - identical content, identical order, one field. (The
    standalone probe that produced ``/tmp/bench/setcmp.json`` used a different header
    line, so this path's prompt is not byte-identical to the one those latency figures
    were measured on.) No tools are passed, because there is no tool to pass: the same
    SIGBOUND-01 rule applies and the Responses call here carries no tool spec at all.
    """
    from agents import mantle  # noqa: PLC0415

    response = await asyncio.to_thread(
        mantle.complete,
        model_id,
        f"{SPECIALIST_PROMPTS[domain]}\n\n{_INPUT_HEADER}\n{domain_input}",
        max_output_tokens=_max_output_tokens(model_id),
    )
    return _MantleSpecialistResult(response)


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
        model=build_bedrock_model(model_id, max_tokens=_max_output_tokens(model_id)),
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
    failure here is data (``error`` set, ``usage`` None), not control flow. That
    contract is transport-independent - a ``MantleUnavailable`` (SDK missing, no
    bearer token, no ``bedrock-mantle:CreateInference``, an unpriced GPT id) is
    recorded here exactly like a Bedrock ``ThrottlingException``, so a GPT
    assignment cannot abort the gather that the other seven dimensions are in.
    """
    model_id = model_for(domain)
    tier = tier_label(model_id)
    title = ANALYSIS_TITLES[domain]
    transport = SOURCE_MOCK if mock else transport_for(model_id)
    cache_report = _cache_report(model_id, domain)
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
                source=SOURCE_MOCK,
                prompt_cache=cache_report,
            )

    try:
        async with semaphore:
            if transport == SOURCE_MANTLE:
                result = await _invoke_specialist_mantle(domain, model_id, domain_input)
            else:
                agent = _build_agent(domain, model_id)
                result = await agent.invoke_async(f"{_INPUT_HEADER}\n{domain_input}")
        usage = _usage_from(result)
        if transport == SOURCE_MANTLE:
            # The measured cache token counts, once they exist. Still not a Converse
            # verdict: will_cache stays None because no cachePoint was ever sent.
            cache_report = _mantle_cache_report(model_id, SPECIALIST_PROMPTS[domain], usage)
        return SpecialistResult(
            domain=domain,
            agent_name=AGENT_NAMES[domain],
            analysis=_analysis_text(result),
            model_id=model_id,
            tier=tier,
            wall_ms=(time.perf_counter() - started) * 1000.0,
            latency_ms=_latency_from(result),
            usage=usage,
            cost_usd=_specialist_cost(model_id, result, usage),
            stop_reason=getattr(result, "stop_reason", None),
            title=title,
            source=transport,
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
            source=transport,
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
            source=SOURCE_MOCK if use_mock else transport_for(model_for(domain)),
        )
    return out


def _summary_source(completed: Mapping[str, SpecialistResult], use_mock: bool) -> str:
    """One transport label for the whole fan-out, or ``mixed`` when there isn't one.

    Kept exact rather than convenient: with the eight specialists on Claude this returns
    ``"bedrock"`` and offline ``"mock"``, byte-identical to what the frame carried before
    routing existed, so no downstream consumer changes. A GPT assignment on some but not
    all specialists returns ``"mixed"`` instead of quietly labelling a Responses call as
    Converse. The per-domain ``sources`` map beside it is always authoritative.
    """
    if use_mock:
        return SOURCE_MOCK
    labels = {result.source for result in completed.values()}
    if len(labels) == 1:
        return labels.pop()
    return "mixed" if labels else SOURCE_BEDROCK


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
        # A single label when every dimension used one transport (which is every
        # existing configuration, so ``mock`` and ``bedrock`` are unchanged), and
        # ``mixed`` when the assignment spans Converse and mantle - because there is no
        # honest single answer then, and picking either would misattribute the other's
        # rows. ``sources`` keeps the per-domain truth either way.
        "source": _summary_source(completed, use_mock),
        "sources": {domain: result.source for domain, result in completed.items()},
    }
