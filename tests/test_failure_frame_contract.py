"""The one non-terminal failure in the SSE protocol, pinned on both sides.

THE CONTRACT

    `error`   on ANY frame  -> TERMINAL. The client aborts the run.
    `failure` on agent_failed -> NON-TERMINAL. One specialist dropped out; the other
                                seven still produce an adjudication.

The distinction exists because AgentCore turns a mid-stream exception into a terminal
`data: {"error": ...}` frame *after* HTTP 200 has been committed, so a client cannot use
the status code and must treat `error` as fatal wherever it appears. That makes `error`
unusable for a survivable, per-specialist failure -- hence `failure`.

WHY THIS FILE EXISTS

Getting it wrong is invisible until a specialist actually fails, and then it fails in the
worst direction. Two live defects, both found in front of the customer:

  * `ui/server.py` emitted `error` on agent_failed. The UI's own guard runs before the
    switch, so ONE failing specialist marked the entire run failed and no adjudication
    was ever rendered -- the exact opposite of the 7-of-8 degradation the pipeline is
    built for.
  * The CloudFront translation layer read `frame.error` on agent_failed, so every failed
    specialist rendered as the literal string "undefined": eight lines of
    "SAMMY_STRAW: undefined" with no reason anywhere on screen.

Both emitters and both readers are checked here so they cannot drift apart again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------


def _agent_failed_block(source: str) -> str:
    """The dict literal that follows an ``"event": "agent_failed"`` line."""
    index = source.index('"event": "agent_failed"')
    return source[index : index + 700]


def test_the_runtime_emits_failure_not_error():
    source = (REPO_ROOT / "app" / "underwriter" / "main.py").read_text(encoding="utf-8")
    block = _agent_failed_block(source)
    assert '"failure"' in block, "the runtime must name the reason `failure`"
    assert '"error":' not in block, (
        "an `error` key on agent_failed is terminal to every client, so one specialist "
        "failing would abort the whole run"
    )


def test_the_demo_server_emits_failure_not_error():
    source = (REPO_ROOT / "ui" / "server.py").read_text(encoding="utf-8")
    block = _agent_failed_block(source)
    assert '"failure"' in block, "ui/server.py must match the runtime's key"
    assert '"error":' not in block, (
        "ui/server.py emitted `error` here, which tripped the UI's terminal-error guard "
        "and killed the run on a single specialist failure"
    )


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def test_the_ui_reducer_exempts_agent_failed_from_the_terminal_guard():
    """The guard runs before the switch, so the exemption has to be in the guard."""
    source = (REPO_ROOT / "ui" / "src" / "hooks" / "useRun.ts").read_text(encoding="utf-8")
    assert 'frame.event !== "agent_failed"' in source, (
        "applyFrame must skip the terminal-error check for agent_failed, or a "
        "non-terminal specialist failure aborts the run before reaching its own case"
    )


def test_the_ui_reads_failure_first_and_never_renders_undefined():
    source = (REPO_ROOT / "ui" / "src" / "hooks" / "useRun.ts").read_text(encoding="utf-8")
    # Both places the reason is displayed: the error list and the agent's card.
    reads = re.findall(r"frame\.failure \?\? frame\.error \?\? \"no reason reported\"", source)
    assert len(reads) >= 2, (
        "both the errors list and the card must read failure -> error -> a literal "
        f"string; found {len(reads)} such reads"
    )
    assert "${frame.error}`" not in source, (
        "a bare frame.error renders as the string 'undefined' when the runtime sent "
        "`failure`, which is what shipped"
    )


def test_the_cloudfront_translation_layer_forwards_failure():
    source = (REPO_ROOT / "deploy" / "cdk" / "lambda" / "stream-proxy.mjs").read_text(
        encoding="utf-8"
    )
    index = source.index("case 'agent_failed':")
    block = source[index : index + 900]
    assert "failure: frame.failure ?? frame.error" in block, (
        "the edge proxy must forward `failure`; reading only `error` rendered every "
        "failed specialist as 'undefined'"
    )


def test_the_ui_type_declares_failure_as_required():
    source = (REPO_ROOT / "ui" / "src" / "lib" / "types.ts").read_text(encoding="utf-8")
    index = source.index("export interface AgentFailed {")
    block = source[index : index + 900]
    assert "failure: string" in block
    assert "error?: string" in block, (
        "`error` stays optional so an older stream is still readable, but it must not "
        "be the required field"
    )


# ---------------------------------------------------------------------------
# End to end through the frame the distributed fan-out actually produces
# ---------------------------------------------------------------------------


def test_distributed_fanout_failure_frame_carries_a_reason():
    """A transport failure must arrive with a readable reason, not an empty field.

    This is the frame the orchestrator turns into `agent_failed`, so an empty `error`
    here becomes "no reason reported" on screen -- survivable, but it means an operator
    cannot tell a throttle from an AccessDenied.
    """
    from app.distributed_fanout import _to_frame

    kind, frame = _to_frame(
        "dealer",
        {"ok": False, "error": "AccessDeniedException: signature mismatch"},
        invoke_ms=73.2,
        started=0.0,
    )
    assert kind == "failed"
    assert frame["domain"] == "dealer"
    assert frame["agent_name"] == "DIANA_DEALER"
    assert "AccessDenied" in frame["error"]


def test_distributed_fanout_never_reports_a_failure_as_done():
    from app.distributed_fanout import _to_frame

    for result in (
        {"ok": False, "error": "boom"},
        {"ok": False},
        {},
    ):
        kind, _ = _to_frame("identity", result, invoke_ms=1.0, started=0.0)
        assert kind == "failed", f"{result!r} must not be treated as a completed analysis"


@pytest.mark.parametrize("mode", ["inprocess", "distributed", "", "nonsense"])
def test_fanout_mode_defaults_to_inprocess(monkeypatch, mode):
    """An unrecognised or absent value must not silently select the distributed path.

    The distributed path needs eight sibling runtimes to exist. A typo that resolved to
    it would fail every application on a deployment that has only the orchestrator.
    """
    from app import distributed_fanout

    monkeypatch.setenv("FANOUT_MODE", mode)
    resolved = distributed_fanout.fanout_mode()
    assert resolved == ("distributed" if mode == "distributed" else "inprocess")


def test_explicit_trace_propagation_is_off_by_default(monkeypatch):
    """It breaks SigV4 inside a runtime, so the default must be off.

    Passing traceParent to InvokeAgentRuntime from a runtime wrapped by
    opentelemetry-instrument produces "The request signature we calculated does not
    match": ADOT's botocore patch is already propagating context on a request botocore
    has signed. Measured on 2026-08-12 -- identical calls succeed from a laptop.
    """
    from app import distributed_fanout

    monkeypatch.delenv("PROPAGATE_TRACE_EXPLICITLY", raising=False)
    assert distributed_fanout._trace_headers() == {}
