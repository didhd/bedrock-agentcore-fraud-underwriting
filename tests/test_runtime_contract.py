"""
Contract gate for the two AgentCore Runtime entrypoints.

These tests exist because every failure they catch is invisible until request
time, in front of the customer:

  * the ``context`` parameter name. ``BedrockAgentCoreApp._takes_context()``
    injects the ``RequestContext`` only when the handler's second positional
    parameter is *literally named* ``context``. Rename it to ``ctx`` and the SDK
    silently calls the handler with one argument -- the runtime loses its
    session_id and there is no error, no warning, and no log line saying so.
  * the async-generator shape. Only an async generator (or sync generator) is
    converted to ``text/event-stream``. A plain ``async def`` returning a dict
    produces a single JSON body, so a streaming client hangs waiting for frames
    that never come.
  * Francis's eight tool names. Her verbatim routing table names
    ``call_identity_agent`` ... ``call_rings_agent``. A renamed tool means the
    model is told to call something that does not exist.
  * fabricated numbers. In MOCK_MODE no Bedrock call happens, so tokens, cost and
    model latency are unknowable. They must be ``None``, never a plausible-looking
    figure. This is the repo's single biggest liability and it is asserted
    field-by-field here.

Run:  MOCK_MODE=1 python -m pytest tests/test_runtime_contract.py -q
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

UNDERWRITER_PATH = REPO_ROOT / "app" / "underwriter" / "main.py"
FRANCIS_PATH = REPO_ROOT / "app" / "francis" / "main.py"

#: Every test in this file runs offline. Set before the runtime modules are
#: imported, because MOCK_MODE is read at call time but the model/prompt layers
#: are imported at module load.
os.environ.setdefault("MOCK_MODE", "1")
os.environ["EMIT_CLOUDWATCH_METRICS"] = "0"

from app.runtime_support import (  # noqa: E402
    ADJUDICATION_KEYS,
    OVERALL_RISK_VALUES,
    RECOMMENDATION_VALUES,
    SESSION_ID_MIN_LENGTH,
    emit_agent_metrics,
    extract_application_id,
    extract_prompt,
    new_session_id,
    order_adjudication,
    validate_adjudication,
)
from prompts.loader import DOMAINS  # noqa: E402


def _load(path: Path, name: str):
    """Import a runtime entrypoint by file path, as the AgentCore CLI does.

    The CLI runs each entrypoint as a top-level module named ``main`` with cwd set
    to its directory, so importing by path (rather than as ``app.underwriter.main``)
    is what actually exercises the module's own sys.path bootstrap.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def underwriter():
    return _load(UNDERWRITER_PATH, "pp_test_underwriter_main")


@pytest.fixture(scope="module")
def francis():
    # Francis's tools live in a `tools` package inside her own directory, which
    # collides with the repo-root `tools` package. Her bootstrap resolves that by
    # putting her directory ahead of the repo root on sys.path; loading her here
    # (after the underwriter, in the same interpreter) proves the ordering holds.
    return _load(FRANCIS_PATH, "pp_test_francis_main")


class FakeContext:
    """Stand-in for ``bedrock_agentcore.runtime.RequestContext``.

    Deliberately a plain object with the same three attributes rather than the
    real pydantic model: the runtime must only depend on the attribute surface it
    documents (``session_id``, ``request_headers``, ``request``).
    """

    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id
        self.request_headers: dict[str, str] = {}
        self.request = None


async def _drain(agen) -> list[dict[str, Any]]:
    return [frame async for frame in agen]


def _run(agen) -> list[dict[str, Any]]:
    return asyncio.run(_drain(agen))


# ---------------------------------------------------------------------------
# The entrypoint contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", ["underwriter", "francis"])
def test_second_parameter_is_literally_named_context(module_name, request):
    """The SDK matches by NAME, not by type or position-with-annotation."""
    module = request.getfixturevalue(module_name)
    params = list(inspect.signature(module.invoke).parameters)
    assert len(params) >= 2, f"{module_name}.invoke takes {params}; it needs (payload, context)"
    assert params[1] == "context", (
        f"{module_name}.invoke's second parameter is named {params[1]!r}. "
        "BedrockAgentCoreApp._takes_context() requires the literal name 'context' or the "
        "RequestContext is never injected -- silently, with no error at request time."
    )


@pytest.mark.parametrize("module_name", ["underwriter", "francis"])
def test_entrypoint_is_an_async_generator(module_name, request):
    """Only a generator entrypoint is converted to text/event-stream."""
    module = request.getfixturevalue(module_name)
    assert inspect.isasyncgenfunction(module.invoke), (
        f"{module_name}.invoke must be an async generator; a plain coroutine returns a single "
        "JSON body and a streaming client waits forever for frames."
    )


@pytest.mark.parametrize("module_name", ["underwriter", "francis"])
def test_sdk_agrees_the_handler_takes_context(module_name, request):
    """Ask the SDK itself, not just the signature -- this is the real gate."""
    module = request.getfixturevalue(module_name)
    handler = module.app.handlers["main"]
    assert module.app._takes_context(handler) is True


@pytest.mark.parametrize("module_name", ["underwriter", "francis"])
def test_debug_mode_is_off(module_name, request):
    """debug=True exposes a health-state backdoor on /invocations.

    With debug enabled, any payload carrying ``_agent_core_app_action`` is routed
    to ``_handle_task_action``, which can force the ping status and enumerate
    running jobs.
    """
    module = request.getfixturevalue(module_name)
    assert module.app.debug is False


@pytest.mark.parametrize("module_name", ["underwriter", "francis"])
def test_custom_ping_handler_is_registered(module_name, request):
    module = request.getfixturevalue(module_name)
    assert module.app._ping_handler is not None
    from bedrock_agentcore.runtime.models import PingStatus

    assert module.app._ping_handler() == PingStatus.HEALTHY


def test_ping_reports_busy_while_a_task_is_open(underwriter):
    """A long fan-out must not advertise itself as idle."""
    from bedrock_agentcore.runtime.models import PingStatus

    task_id = underwriter.app.add_async_task("test_task")
    try:
        assert underwriter.app._ping_handler() == PingStatus.HEALTHY_BUSY
        assert underwriter.app.get_current_ping_status() == PingStatus.HEALTHY_BUSY
    finally:
        underwriter.app.complete_async_task(task_id)
    assert underwriter.app._ping_handler() == PingStatus.HEALTHY


# ---------------------------------------------------------------------------
# Session ids
# ---------------------------------------------------------------------------


def test_generated_session_id_meets_the_33_char_floor():
    """uuid4().hex is 32 chars and is REJECTED; str(uuid4()) is 36."""
    import uuid

    assert len(uuid.uuid4().hex) < SESSION_ID_MIN_LENGTH, (
        "uuid4().hex is the trap this guards against; if it ever reaches 33 chars this "
        "test's premise changed."
    )
    for _ in range(10):
        session_id = new_session_id()
        assert len(session_id) >= SESSION_ID_MIN_LENGTH, (
            f"generated runtimeSessionId {session_id!r} is {len(session_id)} chars; "
            f"AgentCore requires at least {SESSION_ID_MIN_LENGTH}"
        )


def test_missing_session_id_falls_back_to_a_valid_one(underwriter):
    """RequestContext.session_id is Optional and is None without the header."""
    frames = _run(underwriter.invoke({"application_id": "APP-1004"}, FakeContext(session_id=None)))
    start = frames[0]
    assert start["event"] == "start"
    assert len(start["session_id"]) >= SESSION_ID_MIN_LENGTH


# ---------------------------------------------------------------------------
# Payload tolerance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"application_id": "APP-1004"}, "APP-1004"),
        ({"prompt": "Run a full underwriting review of APP-1004"}, "APP-1004"),
        ({"prompt": "check app 1004 please"}, "APP-1004"),
        ({"prompt": "app_1004"}, "APP-1004"),
        ({"messages": [{"role": "user", "content": [{"text": "look at APP-1003"}]}]}, "APP-1003"),
        # Free text with no id must NOT be misread as an id.
        ({"prompt": "what can you do"}, None),
        ({}, None),
    ],
)
def test_application_id_extraction(payload, expected):
    assert extract_application_id(payload) == expected


def test_extract_prompt_mirrors_the_cli_tolerance():
    assert extract_prompt({"prompt": "hello"}) == "hello"
    assert extract_prompt({"messages": [{"role": "user"}]}) == [{"role": "user"}]
    tool_results = extract_prompt({"tool_results": [{"toolUseId": "t1", "content": []}]})
    assert tool_results[0]["content"][0]["toolResult"]["toolUseId"] == "t1"
    assert extract_prompt({}) == ""


def test_no_application_id_yields_a_structured_error_frame(underwriter):
    frames = _run(underwriter.invoke({"prompt": "what can you do"}, FakeContext("s" * 40)))
    assert len(frames) == 1
    assert frames[0]["error"]
    assert frames[0]["error_type"] == "MissingApplicationId"


def test_unknown_application_yields_a_structured_error_frame(underwriter):
    frames = _run(underwriter.invoke({"application_id": "APP-9999"}, FakeContext("s" * 40)))
    assert frames[-1]["error_type"] == "ApplicationNotFound"


# ---------------------------------------------------------------------------
# The happy path: full frame sequence
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def happy_frames(underwriter):
    return _run(underwriter.invoke({"application_id": "APP-1004"}, FakeContext("s" * 40)))


def test_frame_sequence_is_start_agents_synthesis_done(happy_frames):
    events = [frame["event"] for frame in happy_frames]

    assert events[0] == "start"
    assert events[-1] == "done"
    assert events.count("agent_start") == len(DOMAINS) == 8
    assert events.count("agent_done") == 8, f"expected 8 agent_done frames, got {events}"
    assert events.count("synthesis_start") == 1

    # Ordering: every agent_start precedes every agent_done, synthesis follows the
    # fan-out, done is last.
    assert events.index("synthesis_start") > max(
        i for i, e in enumerate(events) if e == "agent_done"
    )
    assert max(i for i, e in enumerate(events) if e == "agent_start") < min(
        i for i, e in enumerate(events) if e == "agent_done"
    )


def test_every_domain_reports_exactly_once(happy_frames):
    done = [f for f in happy_frames if f["event"] == "agent_done"]
    assert sorted(f["domain"] for f in done) == sorted(DOMAINS)


def test_agent_done_frames_carry_the_customer_output_contract(happy_frames):
    """Titles where the prompt defines one; NO title for bustout and rings."""
    titles = {f["domain"]: f["analysis_title"] for f in happy_frames if f["event"] == "agent_done"}
    assert titles["identity"] == "Identity Fraud Risk Analysis"
    assert titles["income"] == "Income Fraud Risk Analysis"
    assert titles["employment"] == "Employment Fraud Risk Analysis"
    assert titles["dealer"] == "Dealer Fraud Risk Analysis"
    assert titles["straw"] == "Straw Borrower Fraud Risk Analysis"
    assert titles["synthetic"] == "Synthetic Identity Fraud Risk Analysis"
    assert titles["bustout"] is None, "bustout's prompt defines no Title; never invent one"
    assert titles["rings"] is None, "rings' prompt defines no Title; never invent one"


def test_start_frame_declares_whether_tokens_will_be_measured(happy_frames):
    start = happy_frames[0]
    assert start["mock_mode"] is True
    assert start["measures_tokens"] is False
    assert start["agents"][0] == "ISAAC_IDENTITY"
    assert start["domains"] == list(DOMAINS)


# ---------------------------------------------------------------------------
# The 21-key adjudication contract
# ---------------------------------------------------------------------------


def test_adjudication_has_exactly_the_21_keys_in_order(happy_frames):
    adjudication = happy_frames[-1]["adjudication"]
    assert list(adjudication) == list(ADJUDICATION_KEYS)
    assert len(adjudication) == 21


def test_adjudication_key_order_is_the_master_prompt_order():
    """Not alphabetical, not the PDF's order: the master prompt's input order."""
    assert ADJUDICATION_KEYS[:5] == (
        "master_summary",
        "overall_risk_decision",
        "recommendation_decision",
        "recommended_stipulations",
        "top_risk_factors",
    )
    assert ADJUDICATION_KEYS[5:9] == ("identity_summary", "identity_flag", "dealer_summary", "dealer_flag")
    assert ADJUDICATION_KEYS[-2:] == ("rings_summary", "rings_flag")


def test_adjudication_has_no_application_id_key(happy_frames):
    """The customer's format has 21 keys and application_id is not one of them."""
    assert "application_id" not in happy_frames[-1]["adjudication"]
    # It belongs on the envelope instead.
    assert happy_frames[-1]["application_id"] == "APP-1004"


def test_key_check_reports_valid(happy_frames):
    assert happy_frames[-1]["key_check"] == {
        "expected_count": 21,
        "actual_count": 21,
        "order_matches": True,
        "missing": [],
        "unexpected": [],
        "valid": True,
    }


def test_adjudication_enums_are_the_prompt_values(happy_frames):
    adjudication = happy_frames[-1]["adjudication"]
    assert adjudication["overall_risk_decision"] in OVERALL_RISK_VALUES
    assert adjudication["recommendation_decision"] in RECOMMENDATION_VALUES
    for domain in DOMAINS:
        assert adjudication[f"{domain}_flag"] in (0, 1)
        assert isinstance(adjudication[f"{domain}_summary"], str)
        assert len(adjudication[f"{domain}_summary"]) < 1200


def test_low_risk_implies_no_top_risk_factors_and_no_stipulations(happy_frames):
    adjudication = happy_frames[-1]["adjudication"]
    if adjudication["overall_risk_decision"] == "LOW RISK":
        assert adjudication["top_risk_factors"] == ""
    if adjudication["recommendation_decision"] != "REVIEW AND APPLY STIPULATIONS":
        assert adjudication["recommended_stipulations"] == ""


def test_validate_adjudication_detects_a_broken_contract():
    """The validator must report defects, not paper over them."""
    good = {key: (0 if key.endswith("_flag") else "x") for key in ADJUDICATION_KEYS}
    assert validate_adjudication(good)["valid"] is True

    missing = dict(good)
    del missing["rings_flag"]
    report = validate_adjudication(missing)
    assert report["valid"] is False
    assert report["missing"] == ["rings_flag"]

    extra = dict(good)
    extra["application_id"] = "APP-1004"
    report = validate_adjudication(extra)
    assert report["valid"] is False
    assert report["unexpected"] == ["application_id"]

    reordered = {key: good[key] for key in reversed(ADJUDICATION_KEYS)}
    report = validate_adjudication(reordered)
    assert report["order_matches"] is False
    assert report["valid"] is False


def test_order_adjudication_restores_the_key_order_without_dropping_extras():
    scrambled = {key: 0 for key in reversed(ADJUDICATION_KEYS)}
    scrambled["surprise"] = 1
    ordered = order_adjudication(scrambled)
    assert list(ordered)[:21] == list(ADJUDICATION_KEYS)
    # An unexpected key is preserved so the contract break stays visible.
    assert ordered["surprise"] == 1


# ---------------------------------------------------------------------------
# No fabricated numbers
# ---------------------------------------------------------------------------

#: Fields that are unknowable without a real Bedrock call. In MOCK_MODE each must
#: be null or absent -- NEVER a plausible-looking number.
_UNMEASURABLE_OFFLINE = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
    "model_latency_ms",
)


def test_offline_run_reports_no_token_counts_or_cost(happy_frames):
    for frame in happy_frames:
        if frame["event"] != "agent_done":
            continue
        for field in _UNMEASURABLE_OFFLINE:
            assert frame.get(field) is None, (
                f"{frame['domain']}.{field} is {frame.get(field)!r} in MOCK_MODE. No Bedrock call "
                "happened, so this value cannot be known and must be null -- a fabricated figure "
                "here is exactly the liability this repo must not carry."
            )
        assert frame["measured"] is False
        assert frame["source"] == "mock"


def test_offline_done_frame_reports_cost_as_unknown_not_zero(happy_frames):
    cost = happy_frames[-1]["cost"]
    assert cost["agents_usd"] is None
    assert cost["synthesis_usd"] is None
    assert cost["total_usd"] is None
    assert cost["measured"] is False
    assert cost["total_usd"] != 0, "0.0 would read as a measured $0 cost; unknown must be null"

    synthesis = happy_frames[-1]["synthesis"]
    for field in _UNMEASURABLE_OFFLINE:
        assert synthesis.get(field) is None
    assert synthesis["measured"] is False


def test_latency_is_measured_even_offline_and_labelled_as_mock(happy_frames):
    """Wall clock IS real offline; it just is not a Bedrock measurement."""
    timing = happy_frames[-1]["timing"]
    assert timing["source"] == "mock"
    for field in ("fanout_wall_ms", "synthesis_ms", "end_to_end_ms"):
        assert isinstance(timing[field], (int, float))
        assert timing[field] >= 0
    for frame in happy_frames:
        if frame["event"] == "agent_done":
            assert isinstance(frame["latency_ms"], (int, float))


def test_cloudwatch_emission_omits_unmeasured_datapoints_rather_than_sending_zero():
    """A false zero in a spend dashboard is worse than a gap: it averages in."""
    per_agent = {
        "identity": {
            "tier": "mid",
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "latency_ms": 12.5,
        },
        "dealer": {
            "tier": "light",
            "cost_usd": 0.0004,
            "input_tokens": 1234,
            "output_tokens": 321,
            "cache_read_tokens": 0,
            "latency_ms": 8.0,
        },
    }
    report = emit_agent_metrics(per_agent, total_cost_usd=None, end_to_end_ms=100.0)
    assert report["enabled"] is False, "metrics must be OFF unless explicitly enabled"
    assert report["sent"] is False
    assert "identity.CostUSD" in report["skipped_unmeasured"]
    assert "identity.InputTokens" in report["skipped_unmeasured"]
    assert "PerAppCostUSD" in report["skipped_unmeasured"]
    # The measured ones are counted: 4 from dealer + latency from both + Applications.
    assert report["datapoints"] == 8


# ---------------------------------------------------------------------------
# Graceful degradation: 7 of 8
# ---------------------------------------------------------------------------


def test_a_failed_specialist_still_yields_a_done_frame(underwriter, monkeypatch):
    """One specialist dying must not lose the other seven."""
    monkeypatch.setenv("MOCK_FAIL_DOMAINS", "income")
    frames = _run(underwriter.invoke({"application_id": "APP-1004"}, FakeContext("s" * 40)))
    events = [f["event"] for f in frames]

    assert events[-1] == "done", f"expected a done frame after a partial fan-out, got {events}"
    assert events.count("agent_done") == 7
    assert events.count("agent_failed") == 1

    failed = next(f for f in frames if f["event"] == "agent_failed")
    assert failed["domain"] == "income"
    assert failed["agent_name"] == "IRIS_INCOME"
    # `failure`, NOT `error`: a client aborts on `error` because a mid-stream
    # exception arrives behind HTTP 200, and 7-of-8 is survivable.
    assert "MOCK_FAIL_DOMAINS" in failed["failure"]
    assert "error" not in failed

    done = frames[-1]
    assert done["specialists_succeeded"] == 7
    assert done["specialists_expected"] == 8
    assert [f["domain"] for f in done["failures"]] == ["income"]
    # The failure is recorded in the adjudication too, not silently dropped.
    assert list(done["adjudication"]) == list(ADJUDICATION_KEYS)
    assert done["adjudication"]["income_flag"] == 0
    assert done["synthesis"]["degraded_domains"] == ["income"]


def test_a_failed_specialist_frame_contains_no_fabricated_metrics(underwriter, monkeypatch):
    monkeypatch.setenv("MOCK_FAIL_DOMAINS", "rings")
    frames = _run(underwriter.invoke({"application_id": "APP-1004"}, FakeContext("s" * 40)))
    failed = next(f for f in frames if f["event"] == "agent_failed")
    for field in ("input_tokens", "output_tokens", "cost_usd", "latency_ms"):
        assert failed.get(field) is None


def test_all_specialists_failing_yields_a_terminal_error_frame(underwriter, monkeypatch):
    """With nothing to synthesize, the run must fail loudly rather than adjudicate."""
    monkeypatch.setenv("MOCK_FAIL_DOMAINS", ",".join(DOMAINS))
    frames = _run(underwriter.invoke({"application_id": "APP-1004"}, FakeContext("s" * 40)))
    assert frames[-1]["error_type"] == "FanOutFailed"
    assert not any(f["event"] == "done" for f in frames)


def test_a_fanout_wide_exception_becomes_a_structured_error_frame(underwriter, monkeypatch):
    """A whole-fan-out failure must arrive with context, not as a bare traceback.

    The SDK would convert an escaping exception into a terminal ``data: {"error":
    ...}`` frame anyway, but stripped of session_id and application_id. Catching it
    here keeps the operator's context attached.
    """
    import app.runtime_support as runtime_support

    def boom(_record):
        def render(_payload, domain):
            raise runtime_support.SignalProjectionUnavailable(f"projection missing for {domain}")

        return render

    monkeypatch.setattr(underwriter, "make_renderer", boom)
    frames = _run(underwriter.invoke({"application_id": "APP-1004"}, FakeContext("s" * 40)))
    terminal = frames[-1]
    assert terminal["event"] == "error"
    assert terminal["error_type"] == "SignalProjectionUnavailable"
    assert terminal["session_id"] == "s" * 40
    assert terminal["application_id"] == "APP-1004"
    assert not any(f["event"] == "done" for f in frames)


def test_live_mode_refuses_to_run_a_specialist_without_a_domain_projection(monkeypatch):
    """The signal boundary must fail loudly rather than widen the view.

    Handing a specialist the whole application record when the per-domain
    projection is unavailable would be a fidelity defect invisible in the output,
    so live mode raises instead. Offline it returns a placeholder that carries no
    signals at all -- not the wrong ones.
    """
    import app.runtime_support as runtime_support

    record = {"signals": {"a": 1, "b": 2}, "context": {}}

    monkeypatch.setenv("MOCK_MODE", "0")
    with pytest.raises(runtime_support.SignalProjectionUnavailable):
        runtime_support.make_renderer(record)({"not": "a SignalPayload"}, "identity")

    monkeypatch.setenv("MOCK_MODE", "1")
    offline = runtime_support.make_renderer(record)({"not": "a SignalPayload"}, "identity")
    assert "identity" in offline
    # The placeholder must not leak any signal name or value.
    assert "a" not in offline.split("Signals the fixture carries")[0].split()
    assert "2" not in offline.replace("Signals the fixture carries: 2", "")


# ---------------------------------------------------------------------------
# Serialization: every frame must survive _convert_to_sse
# ---------------------------------------------------------------------------


def test_every_frame_is_json_serializable(happy_frames, underwriter):
    """The SDK serializes each yielded object with json.dumps.

    A frame carrying a pydantic model or a dataclass would fall through to
    ``convert_complex_objects`` and then to ``str(obj)``, silently turning
    structured data into a repr string in the customer's browser.
    """
    for frame in happy_frames:
        encoded = json.dumps(frame, ensure_ascii=False)
        assert json.loads(encoded) == frame
        # Confirm against the SDK's own converter, not just json.dumps.
        sse = underwriter.app._convert_to_sse(frame)
        assert sse.startswith(b"data: ") and sse.endswith(b"\n\n")


# ---------------------------------------------------------------------------
# Francis
# ---------------------------------------------------------------------------

#: The eight names Francis's verbatim routing table references.
EXPECTED_FRANCIS_TOOLS = frozenset(
    {
        "call_identity_agent",
        "call_synthetic_agent",
        "call_straw_agent",
        "call_bustout_agent",
        "call_dealer_agent",
        "call_income_agent",
        "call_employment_agent",
        "call_rings_agent",
    }
)


def test_francis_tool_set_is_exactly_the_eight_routing_table_names(francis):
    from tools.specialists import SPECIALIST_TOOLS

    names = {tool.tool_name for tool in SPECIALIST_TOOLS}
    assert names == EXPECTED_FRANCIS_TOOLS, (
        "Francis's routing table names these eight tools literally; a rename means the model is "
        f"instructed to call a tool that does not exist. Missing: {EXPECTED_FRANCIS_TOOLS - names}, "
        f"unexpected: {names - EXPECTED_FRANCIS_TOOLS}"
    )
    assert len(SPECIALIST_TOOLS) == 8


def test_francis_tool_names_cover_every_domain(francis):
    from tools.specialists import TOOL_NAME_TO_DOMAIN

    assert sorted(TOOL_NAME_TO_DOMAIN.values()) == sorted(DOMAINS)


def test_francis_system_prompt_is_the_verbatim_customer_text(francis):
    from prompts.loader import FRANCIS_ORCHESTRATION_PROMPT, FRANCIS_RESPONSE_FORMAT

    prompt = francis.SYSTEM_PROMPT
    assert FRANCIS_ORCHESTRATION_PROMPT in prompt
    assert FRANCIS_RESPONSE_FORMAT in prompt
    # Load-bearing sentences, spot-checked so a paraphrase in either half fails here
    # as well as in tests/test_prompt_fidelity.py.
    assert "You are Francis" in prompt
    assert "Never identify yourself as Claude" in prompt
    assert "RETRO_FRANCIS" in prompt
    assert "Call MAXIMUM 2 tools per query" in prompt
    assert "Execute immediately. No announcements." in prompt
    assert "## EXECUTIVE SUMMARY" in prompt
    assert "## ANALYSIS" in prompt
    assert "###RECOMMENDATIONS" in prompt
    # Every routing-table tool name must appear in the prompt the model reads.
    for name in EXPECTED_FRANCIS_TOOLS:
        assert name in prompt, f"routing table does not mention {name}"


def test_francis_never_identifies_as_claude_in_her_own_name(francis):
    assert francis.FRANCIS_NAME == "Francis"
    # NOT upper-case: the only uppercase FRANCIS the customer writes is inside
    # RETRO_FRANCIS, the separate system Francis must deflect to.
    assert francis.FRANCIS_NAME != "FRANCIS"


def test_francis_enforces_the_two_tool_cap_in_code(francis):
    from tools.specialists import MAX_TOOLS_PER_QUERY

    assert MAX_TOOLS_PER_QUERY == 2

    hook = francis.ToolCapHook()
    registered: dict[Any, Any] = {}

    class Registry:
        def add_callback(self, event_type, callback):
            registered[event_type] = callback

    hook.register_hooks(Registry())
    from strands.hooks import BeforeToolCallEvent

    assert BeforeToolCallEvent in registered, "the cap must hook BeforeToolCallEvent"


class _FakeToolEvent:
    """Minimal stand-in for BeforeToolCallEvent: a tool_use dict + cancel_tool."""

    def __init__(self, name: str) -> None:
        self.tool_use = {"name": name, "toolUseId": "t"}
        self.cancel_tool: Any = False


def test_tool_cap_hook_cancels_the_third_specialist_call(francis):
    hook = francis.ToolCapHook()

    first = _FakeToolEvent("call_identity_agent")
    second = _FakeToolEvent("call_rings_agent")
    third = _FakeToolEvent("call_income_agent")
    for event in (first, second, third):
        hook._before_tool_call(event)

    assert first.cancel_tool is False
    assert second.cancel_tool is False
    assert isinstance(third.cancel_tool, str)
    assert "maximum" in third.cancel_tool.lower()
    assert hook.admitted == ["call_identity_agent", "call_rings_agent"]
    assert hook.blocked == ["call_income_agent"]

    # A non-specialist tool is not subject to a rule about specialist fan-out.
    other = _FakeToolEvent("cortex_analyst")
    hook._before_tool_call(other)
    assert other.cancel_tool is False


def test_tool_cap_holds_when_every_hook_fires_before_any_tool_body_runs(francis):
    """The cap must not be scheduling-dependent.

    The default ``ConcurrentToolExecutor`` starts one asyncio task per ``toolUse``
    in a turn, so if the model emits three tool uses in one message, all three
    ``BeforeToolCallEvent`` callbacks can fire before any tool body has executed.
    A hook that counted *completed* calls would read 0 for all three and admit
    every one; counting admissions inside the hook is what makes this correct.
    """
    import tools.specialists as specialists

    specialists.reset_call_log()
    assert specialists.tool_call_count() == 0, "premise: no tool body has run yet"

    hook = francis.ToolCapHook()
    events = [
        _FakeToolEvent(name)
        for name in ("call_identity_agent", "call_rings_agent", "call_income_agent", "call_dealer_agent")
    ]
    for event in events:
        hook._before_tool_call(event)

    cancelled = [bool(event.cancel_tool) for event in events]
    assert cancelled == [False, False, True, True], (
        "with an empty call log the cap still admitted more than 2 tools; the hook is counting "
        "completions rather than admissions and is therefore scheduling-dependent"
    )
    assert len(hook.admitted) == 2


def test_francis_tool_body_refuses_past_the_cap(francis, monkeypatch):
    """Defence in depth: the tool itself refuses if the hook was bypassed."""
    import tools.specialists as specialists

    monkeypatch.setattr(specialists, "_CALL_LOG", [{"domain": "identity"}, {"domain": "rings"}])
    answer = asyncio.run(specialists._run_domain("income", "any income fraud?", "APP-1004"))
    assert "maximum" in answer.lower()
    assert "not run" in answer.lower()


def test_francis_offline_answer_uses_her_response_shape(francis):
    frames = _run(francis.invoke({"prompt": "Is there income fraud on APP-1003?"}, FakeContext("f" * 40)))
    events = [f["event"] for f in frames]
    assert events[0] == "start"
    assert events[-1] == "done"

    done = frames[-1]
    assert "## EXECUTIVE SUMMARY" in done["answer"]
    assert "## ANALYSIS" in done["answer"]
    # Her rule: recommendations ONLY when the analyst asked for them.
    assert "###RECOMMENDATIONS" not in done["answer"]

    start = frames[0]
    assert start["max_tools_per_query"] == 2
    assert set(start["tools"]) == EXPECTED_FRANCIS_TOOLS
    assert start["agent"] == "Francis"


def test_francis_offline_frames_carry_no_fabricated_metrics(francis):
    frames = _run(francis.invoke({"prompt": "any fraud rings?"}, FakeContext("f" * 40)))
    done = frames[-1]
    for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost_usd"):
        assert done.get(field) is None
    assert done["measured"] is False
    assert done["source"] == "mock"


def test_francis_missing_prompt_yields_an_error_frame(francis):
    frames = _run(francis.invoke({}, FakeContext("f" * 40)))
    assert frames[-1]["error_type"] == "MissingPrompt"


def test_francis_memory_is_off_without_the_env_var(francis, monkeypatch):
    """AgentCore Memory does not work under `agentcore dev` -- no deployed store."""
    monkeypatch.delenv("FRANCIS_MEMORY_ID", raising=False)
    monkeypatch.delenv("MEMORY_FRANCISMEMORY_ID", raising=False)
    assert francis._memory_id() is None
    assert francis._session_manager("s" * 40, "analyst-a") is None

    # The CLI generates MEMORY_<NAME>_ID from the francisMemory resource.
    monkeypatch.setenv("MEMORY_FRANCISMEMORY_ID", "mem-123")
    assert francis._memory_id() == "mem-123"


def test_francis_memory_batch_size_avoids_the_create_event_tps_cap(francis):
    """CreateEvent is throttled ~5 TPS per (actor, session); batch_size>1 avoids it."""
    assert francis.MEMORY_BATCH_SIZE > 1
    assert 1 <= francis.MEMORY_BATCH_SIZE <= 100  # the config field's own bounds


# ---------------------------------------------------------------------------
# The batch path must stay stateless
# ---------------------------------------------------------------------------


def _keyword_names_in(path: Path) -> set[str]:
    """Every keyword-argument name used in any call in ``path``.

    Parsed with ``ast`` rather than grepped so prose in a docstring or comment
    that merely *mentions* a kwarg cannot make the check pass or fail.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        keyword.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg
    }


def test_underwriter_attaches_no_session_manager(underwriter):
    """A per-message boto write on the latency-critical path is not acceptable.

    The batch path is stateless: one application in, one adjudication out. A
    session manager would write to AgentCore Memory on every message, adding a
    boto round trip per turn to the latency budget this runtime exists to protect.
    """
    assert "session_manager" not in _keyword_names_in(UNDERWRITER_PATH), (
        "the batch underwriter passes session_manager= somewhere: it must stay stateless, "
        "because a session manager costs a boto round trip per message"
    )
    # Sanity-check the detector against the runtime that DOES use one, so a broken
    # AST walk cannot make this assertion vacuous.
    assert "session_manager" in _keyword_names_in(FRANCIS_PATH)


# ---------------------------------------------------------------------------
# The deprecated shim
# ---------------------------------------------------------------------------


def test_shim_delegates_to_the_real_entrypoint():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import agentcore_runtime

    assert agentcore_runtime.UNDERWRITER_ENTRYPOINT == "app/underwriter/main.py"
    assert agentcore_runtime.FRANCIS_ENTRYPOINT == "app/francis/main.py"
    params = list(inspect.signature(agentcore_runtime.invoke).parameters)
    assert params[1] == "context"
    assert inspect.isasyncgenfunction(agentcore_runtime.invoke)
    # Exactly one entrypoint: BedrockAgentCoreApp overwrites handlers["main"].
    assert list(agentcore_runtime.app.handlers) == ["main"]


def test_francis_memory_is_scoped_per_analyst_not_per_persona(francis, monkeypatch):
    """The actor is the ANALYST, from the allowlisted custom header.

    It used to be the constant FRANCIS_NAME, which made every analyst in the company share
    one memory: facts one person stated came back to everyone and one case's summary leaked
    into the next. Wrong on privacy and wrong on usefulness, and invisible in the output.
    """
    monkeypatch.delenv("FRANCIS_ACTOR_ID", raising=False)

    class Ctx:
        request_headers = {"X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id": "rebecca"}

    assert francis.actor_id_of(Ctx()) == "rebecca"

    # boto3's own `runtimeUserId` arrives on a DIFFERENT header, and reading only the
    # custom one made every boto3 caller -- which is every call the UI makes -- fall back
    # to the shared actor. Measured before this was fixed.
    class FirstClassCtx:
        request_headers = {"X-Amzn-Bedrock-AgentCore-Runtime-User-Id": "sanghwa"}

    assert francis.actor_id_of(FirstClassCtx()) == "sanghwa"

    # Case-insensitive: header names are case-insensitive on the wire and the SDK does not
    # normalise them, so matching the exact casing would work only by luck.
    class LowerCtx:
        request_headers = {"x-amzn-bedrock-agentcore-runtime-custom-user-id": "darren"}

    assert francis.actor_id_of(LowerCtx()) == "darren"


def test_francis_actor_falls_back_to_the_persona_rather_than_failing(francis, monkeypatch):
    """A missing header degrades to shared memory; it must not break the answer.

    Degrading is right -- an analyst's question is more important than memory scoping --
    but the fallback is the failure mode worth seeing, so the code logs a warning.
    """
    monkeypatch.delenv("FRANCIS_ACTOR_ID", raising=False)

    class NoHeaders:
        request_headers = {}

    assert francis.actor_id_of(NoHeaders()) == francis.FRANCIS_NAME
    assert francis.actor_id_of(object()) == francis.FRANCIS_NAME


def test_francis_actor_override_wins(francis, monkeypatch):
    """A single-tenant deployment can pin one actor without sending headers."""
    monkeypatch.setenv("FRANCIS_ACTOR_ID", "pinned-tenant")

    class Ctx:
        request_headers = {"x-amzn-bedrock-agentcore-runtime-custom-user-id": "ignored"}

    assert francis.actor_id_of(Ctx()) == "pinned-tenant"


def test_the_custom_user_id_header_is_allowlisted_on_the_runtime():
    """Without the allowlist AgentCore strips the header before the entrypoint sees it.

    So per-analyst memory silently becomes shared memory -- the code is correct, the
    config is not, and nothing in the response says so.
    """
    import json
    from pathlib import Path

    config = json.loads(
        (Path(__file__).resolve().parent.parent / "agentcore" / "agentcore.json").read_text(
            encoding="utf-8"
        )
    )
    francis_runtime = next(r for r in config["runtimes"] if r["name"] == "francis")
    allowlist = [h.lower() for h in francis_runtime.get("requestHeaderAllowlist", [])]
    assert "x-amzn-bedrock-agentcore-runtime-custom-user-id" in allowlist


def test_the_memory_declares_long_term_strategies_with_matching_namespaces():
    """Extraction and retrieval must name the SAME namespaces.

    A strategy that writes to one path while retrieval reads another looks exactly like
    memory not working, because the write side reports success. So the declared namespace
    templates are pinned against the paths app/francis/main.py retrieves from.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
    memory = next(m for m in config["memories"] if m["name"] == "francisMemory")

    kinds = {s["type"] for s in memory["strategies"]}
    assert {"SEMANTIC", "SUMMARIZATION"} <= kinds, (
        "long-term recall needs both: facts to recognise a returning analyst, summaries to "
        "carry a case forward"
    )
    # 3-365 days is the field's own bound; 2 and 366 both fail validate.
    assert 3 <= memory["eventExpiryDuration"] <= 365

    declared = {t for s in memory["strategies"] for t in s.get("namespaceTemplates", [])}
    source = (root / "app" / "francis" / "main.py").read_text(encoding="utf-8")
    for template in declared:
        # `/analysts/{actorId}/facts` is retrieved as an f-string on actor_id.
        probe = template.replace("{actorId}", "{actor_id}").replace("{sessionId}", "{session_id}")
        assert probe in source, f"{template} is declared but never retrieved from"


# ---------------------------------------------------------------------------
# The reference Gateway
# ---------------------------------------------------------------------------


def test_the_gateway_env_var_name_matches_what_the_cdk_derives():
    """A wrong name is SILENT: the client is never built and two tools vanish.

    `AgentCoreMcp.wireGatewayUrlsToAgents` computes
    `AGENTCORE_GATEWAY_${name.toUpperCase().replace(/-/g,'_')}_URL`, so the constant has to
    be derived from the gateway's declared name, not typed independently.
    """
    import json
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
    gateways = config["agentCoreGateways"]
    assert gateways, "no gateway declared"
    name = gateways[0]["name"]
    expected = f"AGENTCORE_GATEWAY_{name.upper().replace('-', '_')}_URL"

    francis_dir = root / "app" / "francis"
    sys.path.insert(0, str(francis_dir))
    try:
        for module in [m for m in list(sys.modules) if m.startswith("tools")]:
            del sys.modules[module]
        from tools.gateway import GATEWAY_URL_ENV
    finally:
        sys.path.remove(str(francis_dir))
        for module in [m for m in list(sys.modules) if m.startswith("tools")]:
            del sys.modules[module]

    assert GATEWAY_URL_ENV == expected


def test_the_gateway_target_points_at_a_real_arn_not_the_placeholder():
    """agentcore.json ships a placeholder; deploy/reference-tools.sh replaces it.

    Deploying with the placeholder creates a target that fails every tool call at request
    time, which reads as the tools being broken rather than as an unrun setup step.
    """
    import json
    from pathlib import Path

    config = json.loads(
        (Path(__file__).resolve().parent.parent / "agentcore" / "agentcore.json").read_text(
            encoding="utf-8"
        )
    )
    for gateway in config["agentCoreGateways"]:
        for target in gateway["targets"]:
            block = target.get("lambdaFunctionArn")
            if not block:
                continue
            arn = block["lambdaArn"]
            assert "PLACEHOLDER" not in arn, (
                "run ./deploy/reference-tools.sh before deploying: it creates the Lambda "
                "and writes its ARN here"
            )
            assert arn.startswith("arn:aws"), arn
            assert Path(
                Path(__file__).resolve().parent.parent / block["toolSchemaFile"]
            ).is_file(), block["toolSchemaFile"]


def test_the_tool_schema_has_no_nested_json_wrapper():
    """`inputSchema` must be a bare object schema.

    A nested `{"json": {...}}` wrapper is accepted by the file but fails the deployment
    with "Attribute type null is not yet supported", which names nothing useful.
    """
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
    for gateway in config["agentCoreGateways"]:
        for target in gateway["targets"]:
            block = target.get("lambdaFunctionArn")
            if not block:
                continue
            tools = json.loads((root / block["toolSchemaFile"]).read_text(encoding="utf-8"))
            assert tools, "an empty tool schema exposes nothing"
            for tool in tools:
                assert tool["inputSchema"]["type"] == "object", tool["name"]
                assert "json" not in tool["inputSchema"], (
                    f"{tool['name']}: nested json wrapper fails deployment"
                )
                # The description is what the model reads to decide whether to call it.
                assert len(tool["description"]) > 60, f"{tool['name']}: description too thin"


def test_the_alert_table_is_generated_and_agrees_with_the_signal_layer():
    """The Gateway's data must not be able to contradict the specialists.

    If the lookup and the agents disagree about what an alert means, the agent is the one
    that sounds authoritative -- so the table is generated from the same module the
    specialists' entitlements come from, and this asserts they still match.
    """
    import json
    from pathlib import Path

    from signal_layer.alert_categories import CATEGORY_ALERTS

    table = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "deploy"
            / "cdk"
            / "lambda-alerts"
            / "alerts.json"
        ).read_text(encoding="utf-8")
    )
    assert table["generated_from"] == "signal_layer.alert_categories.CATEGORY_ALERTS"
    assert table["category_count"] == len(CATEGORY_ALERTS)
    assert {c: sorted(i) for c, i in CATEGORY_ALERTS.items()} == table["categories"]

    # Every id in every category is reachable by the lookup, and its categories round-trip.
    for category, ids in CATEGORY_ALERTS.items():
        for alert_id in ids:
            entry = table["alerts"][str(alert_id)]
            assert category in entry["categories"]
