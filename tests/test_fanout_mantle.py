"""
Contract tests for a GPT-5.x specialist running inside the real fan-out.

WHY THIS FILE EXISTS. The GPT-vs-Claude specialist comparison was measured by a
standalone probe script (``/tmp/bench/setcmp.py``), which built its own Responses call
and never went near ``agents.fanout``. So a "move rings to Luna" recommendation was
unshippable for a plumbing reason: ``fan_out`` constructed every specialist through
Strands Converse, and ``openai.gpt-5.*`` is not served on Converse at all. These tests
pin the capability that closes that gap, and specifically the four ways it can be built
wrong while still looking like it works:

  1. ROUTING BY FLAG INSTEAD OF BY MODEL ID. The transport is a property of the
     assignment, so ``agents.models.SPECIALIST_MODELS`` must be the only place it is
     expressed - the same rule ``agents/synthesize.py`` already follows for the
     synthesizer. A GPT id must never reach ``build_bedrock_model``, and a Claude id
     must never reach ``mantle.complete``.
  2. A SERIALISED FAN-OUT. ``mantle.complete`` is synchronous. Calling it on the event
     loop blocks every other specialist, which turns one ``asyncio.gather`` into a
     serial loop - eight ~4s calls become ~32s and the customer's 60s target goes with
     it. The concurrency assertions here are the only thing that catches that, because
     the results themselves are identical either way.
  3. A LOST FRAUD DIMENSION. A ``MantleUnavailable`` (no SDK, no bearer token, no
     ``bedrock-mantle:CreateInference``, an unpriced GPT id) must degrade to a recorded
     ``error`` like a Bedrock throttle does. A live run has already relied on
     seven-of-eight adjudicating.
  4. A CACHE CLAIM THAT IS NOT TRUE. ``cache_prefix_report`` answers a Converse question
     and returns ``will_cache: False, reason: '... cache points are dropped'`` for any
     non-Claude id. Carrying that verdict on a Responses call would be a false statement
     twice over: no ``cachePoint`` is sent on that path at all, and GPT-5.6 caches
     implicitly, so a measured Luna call reported 6740 cacheWrite tokens against it.

Every test is offline: ``mantle.complete`` is monkeypatched and no test here mints a
token, resolves a region or touches the endpoint. The ONE live test is skipped unless
``AGENTCORE_BENCH_ALLOW_LIVE=1``, matching the gate ``evals/bench.py`` uses.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import pytest

from agents import fanout, mantle
from agents.fanout import (
    SOURCE_BEDROCK,
    SOURCE_MANTLE,
    SOURCE_MOCK,
    SpecialistResult,
    fan_out,
    fan_out_streaming,
    transport_for,
)
from prompts.loader import AGENT_NAMES, DOMAINS, SPECIALIST_PROMPTS

LUNA = "openai.gpt-5.6-luna"
TERRA = "openai.gpt-5.6-terra"
CLAUDE_MID = "global.anthropic.claude-sonnet-5"

#: A mantle usage dict in the shape ``agents.mantle._usage`` really returns: normalised to
#: the Converse convention, so ``inputTokens`` EXCLUDES the cached and written tokens. The
#: cacheWrite figure is the one measured on a live Luna call, because it is the term whose
#: omission under-reported that call by 62%.
MANTLE_USAGE = {
    "inputTokens": 1200,
    "outputTokens": 425,
    "cacheReadInputTokens": 300,
    "cacheWriteInputTokens": 6740,
    "totalTokens": 8665,
}


def _mantle_response(**overrides: Any) -> dict[str, Any]:
    """One ``mantle.complete`` return value, priced the way mantle prices it."""
    usage = overrides.pop("usage", dict(MANTLE_USAGE))
    model_id = overrides.pop("model_id", LUNA)
    response = {
        "text": "GPT specialist analysis prose.",
        "usage": usage,
        "latency_ms": 3680.0,
        "model_id": model_id,
        "region": "us-east-1",
        "effort": "low",
        "cost_usd": mantle.cost_usd(model_id, usage),
    }
    response.update(overrides)
    return response


@pytest.fixture
def gpt_calls(monkeypatch) -> list[dict[str, Any]]:
    """Patch ``mantle.complete`` to record its arguments and answer instantly.

    Patched on the ``agents.mantle`` module rather than on a name imported into
    ``agents.fanout``, because the routing must reach the real module - a test that
    patches a local alias would pass against an implementation that never calls mantle.
    """
    calls: list[dict[str, Any]] = []

    def fake_complete(model_id: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"model_id": model_id, "prompt": prompt, **kwargs})
        return _mantle_response(model_id=model_id)

    monkeypatch.setattr(mantle, "complete", fake_complete)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    return calls


@pytest.fixture
def no_converse(monkeypatch) -> None:
    """Make any attempt to build a Strands agent an immediate, loud failure.

    A GPT specialist that quietly fell through to ``build_bedrock_model`` would raise a
    ValidationException only on a LIVE call; offline it would construct fine and the test
    would pass. This turns that silent fallthrough into a visible one.
    """

    def explode(domain: str, model_id: str) -> Any:
        raise AssertionError(
            f"_build_agent was called for {domain} on {model_id!r}: a GPT-5.x id was "
            "routed to Strands Converse, which does not serve it"
        )

    monkeypatch.setattr(fanout, "_build_agent", explode)


def _assign(monkeypatch, **assignment: str) -> None:
    """Point specific domains at specific model ids for one test.

    Patches ``agents.models.SPECIALIST_MODELS`` in place because ``agents.fanout`` reads
    it through ``model_for`` on every call. ``monkeypatch.setitem`` restores each key, so
    the shipped assignment is intact for the next test.
    """
    from agents import models

    for domain, model_id in assignment.items():
        monkeypatch.setitem(models.SPECIALIST_MODELS, domain, model_id)


# ---------------------------------------------------------------------------
# 1. Routing is decided by the model id, exactly as the synthesizer's is
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id,expected",
    [
        (LUNA, SOURCE_MANTLE),
        (TERRA, SOURCE_MANTLE),
        ("openai.gpt-5.6-sol", SOURCE_MANTLE),
        ("openai.gpt-5.5", SOURCE_MANTLE),
        (CLAUDE_MID, SOURCE_BEDROCK),
        ("global.anthropic.claude-haiku-4-5-20251001-v1:0", SOURCE_BEDROCK),
        ("us.anthropic.claude-opus-5", SOURCE_BEDROCK),
        # gpt-oss IS on Converse. Routing it to mantle would break a working model.
        ("openai.gpt-oss-120b-1:0", SOURCE_BEDROCK),
    ],
)
def test_transport_is_chosen_from_the_model_id(model_id, expected):
    assert transport_for(model_id) == expected


def test_the_routing_predicate_is_mantles_own_not_a_second_copy(monkeypatch):
    """One predicate, in ``agents.mantle``. A local re-implementation would drift."""
    monkeypatch.setattr(mantle, "is_mantle_model", lambda model_id: model_id == "SENTINEL")
    assert transport_for("SENTINEL") == SOURCE_MANTLE
    assert transport_for(LUNA) == SOURCE_BEDROCK


def test_a_gpt_specialist_goes_through_mantle_and_never_through_converse(
    gpt_calls, no_converse, monkeypatch
):
    _assign(monkeypatch, rings=LUNA)

    result = asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))["rings"]

    assert len(gpt_calls) == 1
    assert gpt_calls[0]["model_id"] == LUNA
    assert result.source == SOURCE_MANTLE
    assert result.model_id == LUNA
    assert result.analysis == "GPT specialist analysis prose."
    assert result.error is None


def test_a_claude_specialist_still_goes_through_converse_and_never_through_mantle(
    monkeypatch,
):
    """The other half of the routing claim, which the GPT tests cannot make."""

    def explode(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("mantle.complete was called for a Claude model id")

    monkeypatch.setattr(mantle, "complete", explode)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(
        fanout, "_build_agent", lambda domain, model_id: _FakeAgent(text=f"{domain} converse")
    )

    result = asyncio.run(fan_out("VIEW", domains=["identity"], mock=False))["identity"]
    assert result.source == SOURCE_BEDROCK
    assert result.analysis == "identity converse"


def test_the_specialist_prompt_reaches_mantle_verbatim(gpt_calls, no_converse, monkeypatch):
    """The Responses API takes one input string, so the customer's prompt and the domain
    view are joined - but the prompt text itself must survive byte-for-byte, and the
    agent must still be given no tools."""
    _assign(monkeypatch, rings=LUNA)

    asyncio.run(fan_out("RENDERED RINGS VIEW", domains=["rings"], mock=False))

    prompt = gpt_calls[0]["prompt"]
    assert SPECIALIST_PROMPTS["rings"] in prompt
    assert prompt.startswith(SPECIALIST_PROMPTS["rings"])
    assert "RENDERED RINGS VIEW" in prompt
    assert fanout._INPUT_HEADER in prompt
    # No tool spec is passed at all: SIGBOUND-01 holds on both transports.
    assert "tools" not in gpt_calls[0]


def test_the_mantle_call_carries_the_same_output_cap_as_the_converse_path(
    gpt_calls, no_converse, monkeypatch
):
    """A cap resolved differently per transport is a silent truncation difference."""
    from agents.models import MAX_TOKENS_BY_TIER, tier_label

    _assign(monkeypatch, rings=LUNA)
    asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))

    expected = MAX_TOKENS_BY_TIER[tier_label(LUNA)]
    assert gpt_calls[0]["max_output_tokens"] == expected
    assert fanout._max_output_tokens(LUNA) == expected


def test_only_the_gpt_domains_reach_mantle_in_a_mixed_assignment(gpt_calls, monkeypatch):
    _assign(monkeypatch, rings=LUNA, synthetic=TERRA)
    monkeypatch.setattr(
        fanout, "_build_agent", lambda domain, model_id: _FakeAgent(text=f"{domain} converse")
    )

    results = asyncio.run(fan_out("VIEW", mock=False))

    assert sorted(call["model_id"] for call in gpt_calls) == sorted([LUNA, TERRA])
    assert results["rings"].source == SOURCE_MANTLE
    assert results["synthetic"].source == SOURCE_MANTLE
    for domain in DOMAINS:
        if domain not in ("rings", "synthetic"):
            assert results[domain].source == SOURCE_BEDROCK, domain


# ---------------------------------------------------------------------------
# 2. Every measured field survives the second transport
# ---------------------------------------------------------------------------


def test_a_mantle_specialist_populates_every_measured_field(gpt_calls, no_converse, monkeypatch):
    """The whole SpecialistResult surface, with the same meaning per field."""
    _assign(monkeypatch, rings=LUNA)

    result = asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))["rings"]

    assert isinstance(result, SpecialistResult)
    assert result.domain == "rings"
    assert result.agent_name == AGENT_NAMES["rings"]
    assert result.model_id == LUNA
    # A GPT id is in no Claude tier, so 'custom' - not a borrowed 'mid'.
    assert result.tier == "custom"
    assert result.analysis == "GPT specialist analysis prose."
    # rings' prompt defines no title, and the mantle path must not invent one either.
    assert result.title is None
    assert result.usage == MANTLE_USAGE
    assert result.latency_ms == 3680.0
    assert result.wall_ms > 0
    assert result.stop_reason is None
    assert result.source == SOURCE_MANTLE
    assert result.ok is True
    assert result.cost_usd == pytest.approx(mantle.cost_usd(LUNA, MANTLE_USAGE))


def test_usage_stays_on_the_converse_convention_so_a_cached_token_is_billed_once(
    gpt_calls, no_converse, monkeypatch
):
    """Responses ``input_tokens`` INCLUDES cached tokens and Converse's does not.
    ``agents.mantle`` already subtracts; the fan-out must not re-add or re-subtract."""
    _assign(monkeypatch, rings=LUNA)

    result = asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))["rings"]

    assert result.usage["inputTokens"] == 1200
    assert result.usage["cacheReadInputTokens"] == 300
    assert result.usage["cacheWriteInputTokens"] == 6740
    # The three input classes are disjoint under this convention, so totalTokens is
    # their sum plus output - if inputTokens had been the Responses total, it would not be.
    assert (
        result.usage["inputTokens"]
        + result.usage["cacheReadInputTokens"]
        + result.usage["cacheWriteInputTokens"]
        + result.usage["outputTokens"]
        == result.usage["totalTokens"]
    )


def test_cost_prices_the_cache_write_term_that_dominates_a_gpt_call(
    gpt_calls, no_converse, monkeypatch
):
    """Cache WRITE is 1.25x input on GPT-5.6. Dropping it under-reported a measured
    Luna call by 62%, so the fan-out's cost must move when only that term moves."""
    _assign(monkeypatch, rings=LUNA)

    with_write = asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))["rings"]

    no_write = dict(MANTLE_USAGE, cacheWriteInputTokens=0, totalTokens=1925)
    gpt_calls.clear()
    original = mantle.complete

    def without_write(model_id: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        return _mantle_response(model_id=model_id, usage=dict(no_write))

    monkeypatch.setattr(mantle, "complete", without_write)
    stripped = asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))["rings"]
    monkeypatch.setattr(mantle, "complete", original)

    assert with_write.cost_usd > stripped.cost_usd
    rate_in, _, _ = mantle.rates_for(LUNA)
    expected_write_cost = 6740 * rate_in * 1.25 / 1_000_000
    assert with_write.cost_usd - stripped.cost_usd == pytest.approx(expected_write_cost)
    # And it really is the majority of the bill, which is why it cannot be dropped.
    assert expected_write_cost / with_write.cost_usd > 0.5


def test_mantle_cost_agrees_with_the_pricing_module():
    """``agents.pricing`` delegates GPT ids to ``agents.mantle``, so both must agree.

    This is the check that makes ``_specialist_cost``'s choice a matter of which call is
    CORRECT rather than which number is right - and it would catch the two rate tables
    drifting apart later.
    """
    from agents.pricing import cost_usd as pricing_cost

    for model_id in (LUNA, TERRA, "openai.gpt-5.6-sol"):
        assert mantle.cost_usd(model_id, MANTLE_USAGE) == pytest.approx(
            pricing_cost(model_id, MANTLE_USAGE)
        ), model_id


def test_a_mantle_specialist_with_no_usage_reports_no_cost_rather_than_zero(
    no_converse, monkeypatch
):
    """$0.00 reads as measured-and-free. Unknown must stay None."""
    _assign(monkeypatch, rings=LUNA)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(
        mantle,
        "complete",
        lambda model_id, prompt, **kwargs: {
            "text": "prose", "usage": None, "latency_ms": 100.0, "cost_usd": None
        },
    )

    result = asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))["rings"]
    assert result.usage is None
    assert result.cost_usd is None
    assert result.analysis == "prose"
    assert result.ok is True


def test_the_streaming_frame_carries_the_mantle_fields_through(gpt_calls, monkeypatch):
    """``to_event`` is what the runtime and the UI actually read."""
    _assign(monkeypatch, rings=LUNA)
    monkeypatch.setattr(fanout, "_build_agent", lambda domain, model_id: _FakeAgent())

    async def collect() -> list[dict[str, Any]]:
        return [event async for event in fan_out_streaming("VIEW", mock=False)]

    events = asyncio.run(collect())
    rings = next(
        e for e in events if e.get("domain") == "rings" and e["event"] == "agent_completed"
    )
    assert rings["source"] == SOURCE_MANTLE
    assert rings["model"] == LUNA
    assert rings["usage"] == MANTLE_USAGE
    assert rings["cost_usd"] == pytest.approx(mantle.cost_usd(LUNA, MANTLE_USAGE))
    assert rings["latency_ms"] == 3680.0
    assert rings["stop_reason"] is None

    summary = events[-1]
    assert summary["event"] == "fanout_completed"
    # Eight priced dimensions, so the total is a real total.
    assert summary["agent_cost_usd"] is not None
    # No single transport produced this run, so no single label claims it did.
    assert summary["source"] == "mixed"
    assert summary["sources"]["rings"] == SOURCE_MANTLE
    assert summary["sources"]["identity"] == SOURCE_BEDROCK


def test_an_all_claude_fanout_still_reports_the_unchanged_single_source_label(monkeypatch):
    """The existing frame contract must not move for the shipped configuration."""
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(fanout, "_build_agent", lambda domain, model_id: _FakeAgent())

    async def last() -> dict[str, Any]:
        events = [event async for event in fan_out_streaming("VIEW", mock=False)]
        return events[-1]

    summary = asyncio.run(last())
    assert summary["source"] == SOURCE_BEDROCK

    async def last_mock() -> dict[str, Any]:
        events = [event async for event in fan_out_streaming("VIEW", mock=True)]
        return events[-1]

    assert asyncio.run(last_mock())["source"] == SOURCE_MOCK


# ---------------------------------------------------------------------------
# 3. The concurrency model is intact
# ---------------------------------------------------------------------------


def test_eight_mixed_specialists_still_run_concurrently(monkeypatch):
    """THE test for this task.

    ``mantle.complete`` is synchronous, so it must be awaited through
    ``asyncio.to_thread``. If it were called on the event loop instead, the four GPT
    specialists would run one after another AND would block the four Claude ones, so the
    wall clock would approach the sum of the stubbed delays rather than the max. Both
    versions return identical results, so this timing assertion is the only detector.
    """
    _assign(monkeypatch, rings=LUNA, synthetic=LUNA, bustout=TERRA, income=TERRA)

    delay_s = 0.20
    gpt_live = _Counter()

    def slow_complete(model_id: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        with gpt_live.enter():
            time.sleep(delay_s)  # blocking, exactly like the real OpenAI SDK call
        return _mantle_response(model_id=model_id)

    monkeypatch.setattr(mantle, "complete", slow_complete)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(
        fanout, "_build_agent", lambda domain, model_id: _FakeAgentWithDelay(delay_s)
    )

    started = time.perf_counter()
    results = asyncio.run(fan_out("VIEW", mock=False))
    wall_s = time.perf_counter() - started

    assert len(results) == 8
    assert all(result.ok for result in results.values())

    serial_s = 8 * delay_s
    assert wall_s < serial_s / 3, (
        f"fan-out took {wall_s:.3f}s against a serial {serial_s:.3f}s: the synchronous "
        "mantle call is blocking the event loop instead of going through asyncio.to_thread"
    )
    # And the four GPT calls really did overlap each other, not just the Claude ones.
    assert gpt_live.peak == 4, f"peak concurrent mantle calls was {gpt_live.peak}, expected 4"


def test_the_semaphore_still_bounds_a_mixed_fanout(monkeypatch):
    """Admission control must not be bypassed by the second transport."""
    _assign(monkeypatch, rings=LUNA, synthetic=LUNA, bustout=LUNA, income=LUNA)

    both_live = _Counter()

    def counting_complete(model_id: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        with both_live.enter():
            time.sleep(0.05)
        return _mantle_response(model_id=model_id)

    monkeypatch.setattr(mantle, "complete", counting_complete)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(
        fanout, "_build_agent", lambda domain, model_id: _FakeAgentWithDelay(0.05, both_live)
    )

    asyncio.run(fan_out("VIEW", mock=False, max_concurrency=2))
    assert both_live.peak <= 2, f"peak concurrency {both_live.peak} exceeded the semaphore's 2"


def test_a_mixed_fanout_returns_all_eight_results_in_the_customers_order(
    gpt_calls, monkeypatch
):
    _assign(monkeypatch, rings=LUNA, dealer=TERRA)
    monkeypatch.setattr(
        fanout, "_build_agent", lambda domain, model_id: _FakeAgent(text=f"{domain} converse")
    )

    results = asyncio.run(fan_out("VIEW", mock=False))

    assert list(results) == list(DOMAINS)
    assert len(results) == 8
    assert all(result.ok for result in results.values())
    assert results["rings"].analysis == "GPT specialist analysis prose."
    assert results["identity"].analysis == "identity converse"


def test_a_slow_mantle_specialist_does_not_delay_the_claude_ones(monkeypatch):
    """Streaming's value is incremental delivery, and a blocking call destroys it:
    the fast Claude dimensions must arrive while the slow GPT one is still running."""
    _assign(monkeypatch, rings=LUNA)

    def slow_complete(model_id: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        time.sleep(0.30)
        return _mantle_response(model_id=model_id)

    monkeypatch.setattr(mantle, "complete", slow_complete)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(
        fanout, "_build_agent", lambda domain, model_id: _FakeAgentWithDelay(0.01)
    )

    async def arrivals() -> list[tuple[str, float]]:
        start = time.perf_counter()
        out: list[tuple[str, float]] = []
        async for event in fan_out_streaming("VIEW", mock=False):
            if event["event"] == "agent_completed":
                out.append((event["domain"], time.perf_counter() - start))
        return out

    order = asyncio.run(arrivals())
    assert order[-1][0] == "rings", "the slow GPT dimension should finish last"
    seven = [t for domain, t in order if domain != "rings"]
    rings_t = next(t for domain, t in order if domain == "rings")
    assert max(seven) < rings_t / 2, (
        "the Claude dimensions were held up by the blocking mantle call: "
        f"slowest Claude arrival {max(seven):.3f}s vs rings {rings_t:.3f}s"
    )


# ---------------------------------------------------------------------------
# 4. Graceful degradation on the mantle path
# ---------------------------------------------------------------------------


def test_a_mantle_failure_degrades_to_seven_of_eight_rather_than_raising(monkeypatch):
    _assign(monkeypatch, rings=LUNA)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(
        fanout, "_build_agent", lambda domain, model_id: _FakeAgent(text=f"{domain} converse")
    )

    def boom(model_id: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        raise mantle.MantleUnavailable(
            "could not mint a Bedrock bearer token for us-east-1: ExpiredToken"
        )

    monkeypatch.setattr(mantle, "complete", boom)

    results = asyncio.run(fan_out("VIEW", mock=False))

    assert len(results) == 8
    rings = results["rings"]
    assert rings.ok is False
    assert "MantleUnavailable" in rings.error
    assert "ExpiredToken" in rings.error
    # A failure must carry no fabricated telemetry.
    assert rings.usage is None
    assert rings.cost_usd is None
    assert rings.latency_ms is None
    assert rings.analysis == ""
    # The transport is still recorded, so a report can attribute the failure.
    assert rings.source == SOURCE_MANTLE
    assert sum(1 for r in results.values() if r.ok) == 7

    # And the synthesis path still adjudicates on the remaining seven, by design.
    from agents.synthesize import synthesize_offline

    assert synthesize_offline(results, "APP-1004").degraded_domains == ["rings"]


@pytest.mark.parametrize(
    "exc",
    [
        mantle.MantleUnavailable("no verified rate card for 'openai.gpt-5.7-x'"),
        RuntimeError("bedrock-mantle call failed: 429 Too Many Requests"),
        ValueError("reasoning effort 'turbo' is not one of [...]"),
    ],
)
def test_every_mantle_failure_mode_is_recorded_not_raised(exc, monkeypatch):
    """Including the unpriced-model case: ``mantle.cost_usd`` raises rather than
    guessing a rate, and that must cost one dimension, never the adjudication."""
    _assign(monkeypatch, rings=LUNA)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)

    def boom(model_id: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        raise exc

    monkeypatch.setattr(mantle, "complete", boom)

    result = asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))["rings"]
    assert result.ok is False
    assert type(exc).__name__ in result.error
    assert result.usage is None


def test_a_mantle_failure_is_a_streaming_frame_not_an_exception(monkeypatch):
    """AgentCore swallows a mid-stream exception into a terminal error frame AFTER
    HTTP 200, so a per-agent failure has to be an ordinary frame on this path too."""
    _assign(monkeypatch, rings=LUNA)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(fanout, "_build_agent", lambda domain, model_id: _FakeAgent())
    monkeypatch.setattr(
        mantle,
        "complete",
        lambda *a, **k: (_ for _ in ()).throw(mantle.MantleUnavailable("no access")),
    )

    async def collect() -> list[dict[str, Any]]:
        return [event async for event in fan_out_streaming("VIEW", mock=False)]

    events = asyncio.run(collect())
    failed = [e for e in events if e["event"] == "agent_failed"]
    assert [e["domain"] for e in failed] == ["rings"]
    assert "no access" in failed[0]["error"]
    summary = events[-1]
    assert summary["failed"] == ["rings"]
    assert len(summary["succeeded"]) == 7
    # One dimension has no measured cost, so the total is None, not a 7/8 sum.
    assert summary["agent_cost_usd"] is None


# ---------------------------------------------------------------------------
# 5. The prompt-caching guard tells the truth
# ---------------------------------------------------------------------------


def test_a_gpt_specialist_reports_no_converse_cache_verdict(gpt_calls, no_converse, monkeypatch):
    """``will_cache`` must be None (unknown), NOT False.

    ``cache_prefix_report(LUNA, ...)`` returns ``will_cache: False`` with the reason
    "model id is not a Claude/Anthropic id; cache points are dropped". That is a true
    statement about a Converse request and a false one here: this path sends no cachePoint
    at all, and GPT-5.6 caches implicitly anyway - a measured Luna call reported 6740
    cacheWrite tokens, which a False verdict flatly contradicts.
    """
    _assign(monkeypatch, rings=LUNA)

    report = asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))["rings"].prompt_cache

    assert report["will_cache"] is None, "a Converse cache verdict was reported for a GPT model"
    assert report["cache_applied"] is False
    assert report["min_cacheable_tokens"] is None
    assert report["transport"] == SOURCE_MANTLE
    assert "NOT applied" in report["reason"]
    assert "cachePoint" in report["reason"]
    # The one honest caching statement available: what the endpoint actually billed.
    assert report["measured_cache_write_tokens"] == 6740
    assert report["measured_cache_read_tokens"] == 300


def test_the_gpt_cache_report_is_not_the_converse_report(monkeypatch):
    """Pinned against the specific wrong answer: the Converse report's own verdict."""
    from agents.models import cache_prefix_report

    converse_view = cache_prefix_report(LUNA, SPECIALIST_PROMPTS["rings"])
    assert converse_view["will_cache"] is False  # what we must NOT carry
    assert "cache points are dropped" in converse_view["reason"]

    ours = fanout._mantle_cache_report(LUNA, SPECIALIST_PROMPTS["rings"])
    assert ours["will_cache"] is not converse_view["will_cache"]
    assert "cache points are dropped" not in ours["reason"]


def test_a_claude_specialist_keeps_the_real_converse_cache_report(monkeypatch):
    """The Converse verdict is still carried where it is meaningful - and it is a bool
    there, which is what ``tests/test_contracts.py`` already pins."""
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(fanout, "_build_agent", lambda domain, model_id: _FakeAgent())

    report = asyncio.run(fan_out("VIEW", domains=["identity"], mock=False))["identity"].prompt_cache
    assert isinstance(report["will_cache"], bool)
    assert report["min_cacheable_tokens"] is not None
    assert "transport" not in report


def test_a_failed_mantle_call_reports_no_measured_cache_tokens(monkeypatch):
    """No call, no measurement: the keys must be absent rather than 0."""
    _assign(monkeypatch, rings=LUNA)
    monkeypatch.setattr(fanout, "widen_default_executor", lambda *a, **k: None)
    monkeypatch.setattr(
        mantle,
        "complete",
        lambda *a, **k: (_ for _ in ()).throw(mantle.MantleUnavailable("no access")),
    )

    report = asyncio.run(fan_out("VIEW", domains=["rings"], mock=False))["rings"].prompt_cache
    assert report["will_cache"] is None
    assert "measured_cache_write_tokens" not in report
    assert "measured_cache_read_tokens" not in report


def test_offline_mode_is_unaffected_by_a_gpt_assignment(monkeypatch):
    """MOCK_MODE must stay free of both transports and of any fabricated number."""
    _assign(monkeypatch, rings=LUNA)

    def explode(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("mantle.complete was called in MOCK_MODE")

    monkeypatch.setattr(mantle, "complete", explode)

    result = asyncio.run(fan_out("VIEW", domains=["rings"], mock=True))["rings"]
    assert result.source == SOURCE_MOCK
    assert result.usage is None
    assert result.cost_usd is None
    assert result.latency_ms is None
    assert result.model_id == LUNA
    # Offline still reports the GPT-honest cache record, because the ASSIGNMENT is GPT.
    assert result.prompt_cache["will_cache"] is None


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Counter:
    """Tracks concurrent entries across threads and the event loop."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self.live = 0
        self.peak = 0

    def enter(self) -> Any:
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            with self._lock:
                self.live += 1
                self.peak = max(self.peak, self.live)
            try:
                yield
            finally:
                with self._lock:
                    self.live -= 1

        return _ctx()


class _FakeMetrics:
    def __init__(self, usage: Any, latency_ms: Any) -> None:
        self.accumulated_usage = usage
        self.accumulated_metrics = {} if latency_ms is None else {"latencyMs": latency_ms}


class _FakeResult:
    def __init__(self, text: str, usage: Any = None, latency_ms: Any = None) -> None:
        self.message = {"role": "assistant", "content": [{"text": text}]}
        self.metrics = _FakeMetrics(usage, latency_ms)
        self.stop_reason = "end_turn"
        self.structured_output = None


CONVERSE_USAGE = {
    "inputTokens": 1600,
    "outputTokens": 1419,
    "totalTokens": 3019,
    "cacheReadInputTokens": 0,
    "cacheWriteInputTokens": 0,
}


class _FakeAgent:
    """Stands in for a Strands ``Agent`` on the Converse half of a mixed fan-out."""

    def __init__(self, *, text: str = "converse analysis") -> None:
        self._text = text

    async def invoke_async(self, prompt: str, **kwargs: Any) -> _FakeResult:
        await asyncio.sleep(0.01)
        return _FakeResult(self._text, dict(CONVERSE_USAGE), 1200.0)


class _FakeAgentWithDelay:
    """A Converse agent with a controllable await, for the timing assertions."""

    def __init__(self, delay_s: float, counter: _Counter | None = None) -> None:
        self._delay = delay_s
        self._counter = counter

    async def invoke_async(self, prompt: str, **kwargs: Any) -> _FakeResult:
        if self._counter is None:
            await asyncio.sleep(self._delay)
        else:
            with self._counter.enter():
                await asyncio.sleep(self._delay)
        return _FakeResult("converse analysis", dict(CONVERSE_USAGE), self._delay * 1000)


# ---------------------------------------------------------------------------
# The one live test. Skipped unless explicitly allowed, because it spends money.
# ---------------------------------------------------------------------------

LIVE_GATE = "AGENTCORE_BENCH_ALLOW_LIVE"


@pytest.mark.skipif(
    os.environ.get(LIVE_GATE) != "1",
    reason=(
        f"live smoke test: set {LIVE_GATE}=1 to run it. ONE real Responses call to "
        "openai.gpt-5.6-luna on the bedrock-mantle endpoint, ~$0.01 at the measured "
        "token counts (1.1/6.6 per MTok, ~2K in / ~450 out). Needs "
        "bedrock-mantle:CreateInference."
    ),
)
def test_live_a_single_gpt_specialist_runs_end_to_end(monkeypatch):
    """One GPT specialist through the SHIPPED code path, not a probe script.

    This is the test the whole task exists to make possible: ``fan_out`` itself, with a
    real fixture payload, a real projection and a real endpoint call. It asserts only
    what a single call can support - a non-empty analysis, real token counts, a priced
    cost, the mantle source - and deliberately makes NO quality or latency claim, because
    n=1 supports neither.
    """
    from fixtures.loader import build_payload

    _assign(monkeypatch, rings=LUNA)

    results = asyncio.run(
        fan_out(build_payload("APP-1004"), domains=["rings"], mock=False)
    )
    result = results["rings"]

    assert result.error is None, f"live mantle specialist failed: {result.error}"
    assert result.source == SOURCE_MANTLE
    assert result.model_id == LUNA
    assert len(result.analysis) > 200, f"suspiciously short analysis: {result.analysis!r}"
    assert result.usage is not None
    assert result.usage["inputTokens"] + result.usage["cacheWriteInputTokens"] > 0
    assert result.usage["outputTokens"] > 0
    assert result.cost_usd is not None and result.cost_usd > 0
    assert result.latency_ms is not None and result.latency_ms > 0
    assert result.wall_ms >= result.latency_ms
    assert result.prompt_cache["will_cache"] is None
    print(
        f"\nLIVE rings on {LUNA}: {result.wall_ms / 1000:.2f}s wall, "
        f"{result.usage['outputTokens']} out tok, ${result.cost_usd:.5f}, "
        f"{len(result.analysis)} chars"
    )
