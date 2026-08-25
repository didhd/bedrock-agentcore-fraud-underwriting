"""End-to-end proof that a transient Bedrock 5xx is actually RETRIED.

Why this file exists separately from the classification tests in ``test_pricing.py``:
asserting ``is_retryable(exc) is True`` proves a predicate, not a behaviour. Between the
predicate and a recovered request sit ``strands/models/bedrock.py``'s ``except
ClientError`` block, the ``AfterModelCallEvent`` hook dispatch, and the ``continue`` in
``strands/event_loop/event_loop.py`` that re-runs the model call. A patch can satisfy the
predicate and still not retry -- e.g. if the exception were wrapped before reaching the
hook. So these tests drive the real machinery and count attempts.

MEASURED, 2026-08-25 (strands-agents 1.50.2, botocore 1.43.80), 503 twice then success:
    patched  -> OK after 3 attempts
    stock    -> ClientError after 1 attempt

The failure being reproduced is the customer's. They hit ThrottlingException, tightened
``max_tokens``, and the same workload then failed with ServiceUnavailableException. The
retry budget collapsed at that moment because stock strands promotes only a 429 to
``ModelThrottledException``; the 503 arrives as a raw ``ClientError`` and the default
``is_retryable`` tests for nothing else.

Offline: the Bedrock client is replaced with a stub. No AWS call, no inference, no spend.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import pytest
from botocore.exceptions import ClientError, EventStreamError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.models import MID_MODEL, build_bedrock_model, build_retry_strategy

# A minimal but structurally real ConverseStream success, in the order strands parses.
_TEXT = "LOW RISK"


def _ok_stream() -> dict[str, Any]:
    return {
        "stream": iter(
            [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockDelta": {"delta": {"text": _TEXT}, "contentBlockIndex": 0}},
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
                {
                    "metadata": {
                        "usage": {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12},
                        "metrics": {"latencyMs": 5},
                    }
                },
            ]
        )
    }


class _FlakyClient:
    """Fails ``fail_times`` with ``code``, then succeeds. Counts every attempt.

    ``mid_stream=True`` raises AFTER the stream opened, as ``EventStreamError`` -- the
    shape botocore's own retry layer cannot see, because the HTTP call succeeded.
    """

    def __init__(self, fail_times: int, code: str, mid_stream: bool = False) -> None:
        self.fail_times = fail_times
        self.code = code
        self.mid_stream = mid_stream
        self.calls = 0
        self.meta = type("_Meta", (), {"region_name": "us-west-2"})()

    def converse_stream(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.calls > self.fail_times:
            return _ok_stream()
        err = {"Error": {"Code": self.code, "Message": "synthetic"}}
        if not self.mid_stream:
            raise ClientError(err, "ConverseStream")

        def _broken():
            yield {"messageStart": {"role": "assistant"}}
            raise EventStreamError(err, "ConverseStream")

        return {"stream": _broken()}


def _run(strategy: Any, fail_times: int, code: str, mid_stream: bool = False) -> dict[str, Any]:
    """Invoke one Agent against the flaky client; report attempts, delays and outcome.

    ``asyncio.sleep`` is stubbed inside ``strands.event_loop._retry`` so the ladder is
    RECORDED rather than waited out -- the test stays fast and additionally asserts the
    real delays the real loop chose, which a plain no-op stub would discard.
    """
    from strands import Agent
    from strands.agent import NullConversationManager
    from strands.event_loop import _retry as retry_module

    client = _FlakyClient(fail_times, code, mid_stream)
    model = build_bedrock_model(MID_MODEL, max_tokens=64, region="us-west-2", client=client)
    model.client = client

    slept: list[float] = []
    real_sleep = retry_module.asyncio.sleep

    async def _record(delay: float) -> None:
        slept.append(delay)
        await real_sleep(0)

    agent = Agent(
        model=model,
        system_prompt="test",
        tools=[],
        callback_handler=None,
        conversation_manager=NullConversationManager(),
        retry_strategy=strategy,
    )

    retry_module.asyncio.sleep = _record
    try:
        try:
            text = str(asyncio.run(agent.invoke_async("go"))).strip()
            outcome, raised = "ok", None
        except Exception as exc:  # noqa: BLE001 - the outcome under test
            text, outcome, raised = "", "raised", type(exc).__name__
    finally:
        retry_module.asyncio.sleep = real_sleep

    return {"attempts": client.calls, "outcome": outcome, "raised": raised, "text": text, "slept": slept}


def _stock() -> Any:
    from strands.agent import ModelRetryStrategy

    from agents import models as m

    return ModelRetryStrategy(
        max_attempts=m.RETRY_MAX_ATTEMPTS,
        initial_delay=m.RETRY_INITIAL_DELAY_S,
        max_delay=m.RETRY_MAX_DELAY_S,
    )


@pytest.mark.parametrize(
    "code",
    ["ServiceUnavailableException", "InternalServerException", "internalServerException"],
)
def test_a_transient_fault_is_retried_and_the_call_recovers(code: str) -> None:
    result = _run(build_retry_strategy(), fail_times=2, code=code)
    assert result["outcome"] == "ok", result
    assert result["attempts"] == 3, result  # 1 original + 2 retries
    assert result["text"] == _TEXT
    # The measured ladder, applied by the real loop rather than restated.
    assert result["slept"] == [1, 2], result


@pytest.mark.parametrize(
    "code",
    ["ServiceUnavailableException", "InternalServerException"],
)
def test_stock_strands_gives_up_on_the_first_attempt(code: str) -> None:
    """The gap, reproduced. If this ever passes 1 attempt, the SDK closed it itself."""
    result = _run(_stock(), fail_times=2, code=code)
    assert result["outcome"] == "raised", result
    assert result["raised"] == "ClientError", result
    assert result["attempts"] == 1, result
    assert result["slept"] == [], result


def test_a_throttle_still_retries_exactly_as_it_did_before() -> None:
    """The 429 path is the one that must NOT change. Same ladder, same attempts.

    ``bedrock.py`` promotes this to ``ModelThrottledException``, so it is the stock
    behaviour and both strategies must agree.
    """
    patched = _run(build_retry_strategy(), fail_times=2, code="ThrottlingException")
    stock = _run(_stock(), fail_times=2, code="ThrottlingException")
    assert patched["outcome"] == stock["outcome"] == "ok"
    assert patched["attempts"] == stock["attempts"] == 3
    assert patched["slept"] == stock["slept"] == [1, 2]


def test_a_mid_stream_failure_is_retried_too() -> None:
    """EventStreamError: raised after the stream opened, invisible to botocore's retries."""
    result = _run(build_retry_strategy(), fail_times=1, code="internalServerException", mid_stream=True)
    assert result["outcome"] == "ok", result
    assert result["attempts"] == 2, result
    assert result["slept"] == [1], result


def test_the_retry_budget_is_still_bounded() -> None:
    """A Region that stays down must not retry forever, or the SLA is gone.

    max_attempts=4 means 1 original + 3 retries and then the real error is re-raised --
    the caller sees Bedrock's failure, not a timeout.
    """
    from agents import models as m

    result = _run(build_retry_strategy(), fail_times=99, code="ServiceUnavailableException")
    assert result["outcome"] == "raised", result
    assert result["raised"] == "ClientError", result
    assert result["attempts"] == m.RETRY_MAX_ATTEMPTS, result
    assert result["slept"] == [1, 2, 4], result
    assert sum(result["slept"]) == 7  # worst-case added wall clock, against a <60s SLA


@pytest.mark.parametrize("code", ["ValidationException", "AccessDeniedException", "ModelTimeoutException"])
def test_a_non_transient_error_is_not_retried(code: str) -> None:
    """Retrying these burns the SLA to reproduce the same failure."""
    result = _run(build_retry_strategy(), fail_times=2, code=code)
    assert result["outcome"] == "raised", result
    assert result["attempts"] == 1, result
    assert result["slept"] == [], result
