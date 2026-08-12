"""
AgentCore Runtime: Francis, the interactive path.

An analyst asks a question in natural language; Francis routes it to at most two
of the eight specialist sub-agents and answers in the markdown shape the
customer's own document specifies. This is the 1:1 replacement for their
Snowflake Intelligence master agent's conversational surface.

    agentcore dev                                  # local, cwd = this directory
    agentcore invoke --dev "Is there income fraud on APP-1003?"
    agentcore invoke --dev "Any fraud rings last week?"

FIDELITY: THE SYSTEM PROMPT IS THE CUSTOMER'S TEXT, UNMODIFIED

``SYSTEM_PROMPT`` below is ``FRANCIS_ORCHESTRATION_PROMPT`` concatenated with
``FRANCIS_RESPONSE_FORMAT``, both read from disk and sha256-verified against the
customer's ``master_agent_orchestration_062026`` document at import. No sentence
is rewritten, reordered, or summarized here. That single concatenation carries,
in her words:

  * the Francis identity rule ("Never identify yourself as Claude or any other
    name") and the RETRO deflection to RETRO_FRANCIS;
  * most-specific-phrase-first matching (fake identity / synthetic / match
    synthetic before identity);
  * "SPEED OPTIMIZATION: Call MAXIMUM 2 tools per query";
  * the eight-line routing table naming ``call_identity_agent`` ...
    ``call_rings_agent`` -- which is why ``tools/specialists.py`` must use exactly
    those names;
  * the general-query fallback (rings, or identity) and Cortex Analyst as a
    fallback only;
  * "Execute immediately. No announcements.";
  * the sub-agent presentation rules (lead with one to two lines, bullets for
    findings, a table for counts/records, name the agent, prose for caveats only);
  * the telegram_messages logical-source note;
  * the note that disability / retired / social security / pension are NOT fake
    employers;
  * "Do not provide recommendations for loan response unless you are prompted for
    recommendations" -- which is also why ``###RECOMMENDATIONS`` is conditional;
  * and the ``## EXECUTIVE SUMMARY`` / ``## ANALYSIS`` / conditional
    ``###RECOMMENDATIONS`` response shape.

THE 2-TOOL CAP IS ENFORCED TWICE, ON PURPOSE

Once in her prompt (the customer's own sentence) and once in code, via a
``BeforeToolCallEvent`` hook that cancels a third call. A prompt instruction is a
strong prior, not a guarantee, and this particular rule is load-bearing for the
latency target the whole migration is justified by: an ambiguous question that
fans out to five specialists turns a ~3s answer into a ~15s one. The hook logs a
warning when it fires, so a prompt-following regression is visible in CloudWatch
rather than silent.

MEMORY IS OPTIONAL, ENV-GATED, AND SCOPED PER ANALYST

``AgentCoreMemorySessionManager`` is attached ONLY when ``FRANCIS_MEMORY_ID`` (or
``MEMORY_FRANCISMEMORY_ID``, the env var name the CLI generates from the
``francisMemory`` resource in agentcore.json) is set. It does NOT work under
``agentcore dev``: there is no deployed memory store and the CLI injects those
variables from deployed state, which is empty locally. Attaching it
unconditionally would make every local run fail on a boto call to a store that
does not exist.

The store now carries LONG-TERM strategies -- SEMANTIC into
``/analysts/{actorId}/facts`` and SUMMARIZATION into
``/summaries/{actorId}/{sessionId}`` -- so a returning analyst is recognised instead of
restating themselves. Two things are load-bearing and both fail silently if missed:

  * ``retrieval_config`` must name the SAME namespace templates the strategies write to.
    Extraction succeeding while retrieval reads a different path looks exactly like
    memory not working, because the write side reports success.
  * the actor is the ANALYST, from the allowlisted
    ``X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id`` header -- not the persona. It used
    to be the constant ``FRANCIS_NAME``, which made every analyst share one memory: facts
    one person stated came back to everyone. The header must be in the runtime's
    ``requestHeaderAllowlist`` or AgentCore strips it before the entrypoint sees it.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Final

# See app/underwriter/main.py for why this bootstrap exists: the AgentCore CLI
# treats this directory as the import root (uvicorn main:app with cwd here; a zip
# whose root is here), so the shared repo-root packages are found by anchoring on
# prompts/loader.py and searching this directory first, then its ancestors.
_HERE = Path(__file__).resolve().parent


def _find_shared_root() -> Path:
    for candidate in (_HERE, *_HERE.parents):
        if (candidate / "prompts" / "loader.py").is_file():
            return candidate
    return _HERE.parent.parent


_SHARED_ROOT = _find_shared_root()

# ORDER MATTERS AND IS THE OPPOSITE OF THE OBVIOUS ONE. Both the repo root and
# THIS directory contain a package named `tools` -- the repo's
# tools/{application_data,cortex_analyst_mcp}.py, and this runtime's
# tools/specialists.py. Whichever path comes first on sys.path wins, so
# `_SHARED_ROOT` must NOT precede `_HERE`: if it did, `from tools.specialists
# import ...` would resolve against the repo-root package and raise
# ModuleNotFoundError (verified -- that is exactly what happened before this
# comment existed). Inserting _SHARED_ROOT first and _HERE second leaves _HERE at
# sys.path[0], which also matches how the CLI runs this file (uvicorn main:app
# with cwd here, so the agent directory is already sys.path[0]).
for _path in (str(_SHARED_ROOT), str(_HERE)):
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from bedrock_agentcore.runtime.models import PingStatus  # noqa: E402

from app.runtime_support import (  # noqa: E402
    cost_of,
    elapsed_ms,
    error_frame,
    extract_prompt,
    mock_mode,
    model_latency_ms_of,
    session_id_of,
    tier_of,
    usage_frame_fields,
    usage_of,
)
from prompts.loader import (  # noqa: E402
    AGENT_NAMES,
    FRANCIS_ORCHESTRATION_PROMPT,
    FRANCIS_RESPONSE_FORMAT,
)
from tools.gateway import build_gateway_client  # noqa: E402
from tools.specialists import (  # noqa: E402
    MAX_TOOLS_PER_QUERY,
    SPECIALIST_TOOL_NAMES,
    SPECIALIST_TOOLS,
    call_log,
    reset_call_log,
)

# debug=False: with debug=True the SDK routes a payload carrying
# `_agent_core_app_action` to _handle_task_action, letting a caller force the
# ping status and enumerate running jobs over /invocations.
app = BedrockAgentCoreApp(debug=False)
log = app.logger

#: Francis's system prompt: the customer's orchestration document plus her
#: response format, concatenated and otherwise untouched. Both halves are
#: sha256-verified against her source document by prompts/loader.py at import.
SYSTEM_PROMPT = f"{FRANCIS_ORCHESTRATION_PROMPT}\n\n{FRANCIS_RESPONSE_FORMAT}"

#: Her name, exactly as she writes it in master_agent_orchestration_062026 ("You
#: are Francis, ..."). Deliberately not upper-cased: the only uppercase FRANCIS
#: the customer writes is inside RETRO_FRANCIS, the separate retro system Francis
#: is required to deflect to.
FRANCIS_NAME = AGENT_NAMES["francis"]


@app.ping
def ping_status() -> PingStatus:
    """Health for the AgentCore control plane.

    A custom handler is consulted before the SDK's automatic behaviour (forced >
    custom > automatic), so it must reproduce the active-task check or a busy
    runtime would advertise itself idle.
    """
    if app.get_async_task_info()["active_count"] > 0:
        return PingStatus.HEALTHY_BUSY
    return PingStatus.HEALTHY


# ---------------------------------------------------------------------------
# The 2-tool cap, enforced in code
# ---------------------------------------------------------------------------


class ToolCapHook:
    """Cancel any specialist tool call beyond the customer's MAXIMUM of 2.

    ``BeforeToolCallEvent.cancel_tool`` accepts a string, which Strands places
    into the tool result with an error status -- so the model sees a concrete
    reason and can answer from the results it already has, instead of retrying or
    silently losing a call.

    Only the eight specialist tools are counted. A future non-specialist tool
    (Cortex Analyst as the customer's documented fallback, for instance) is not
    subject to a rule she wrote about specialist fan-out.

    IT COUNTS ADMISSIONS, NOT COMPLETIONS, AND THAT DISTINCTION IS THE WHOLE
    CORRECTNESS ARGUMENT. The obvious implementation -- compare
    ``tools.specialists.tool_call_count()`` (calls the tool bodies have logged)
    against the cap -- is scheduling-dependent and silently wrong under the
    default ``ConcurrentToolExecutor``: it starts one asyncio task per ``toolUse``
    in the turn, so if the model emits three tool uses in one message, all three
    ``BeforeToolCallEvent`` callbacks can fire before any tool body has run and
    every one of them would read a count of 0 and be admitted. Verified: with an
    empty call log, three sequential ``_before_tool_call`` invocations produced
    zero cancellations.

    Counting here instead -- incrementing as each call is *admitted*, inside the
    hook -- is correct regardless of interleaving, because the callbacks
    themselves are plain synchronous functions run on the one event loop, so the
    increment cannot be preempted mid-check. The cap therefore holds whether the
    model requests two tools in one turn, or one tool across two turns.
    """

    def __init__(self, max_tools: int = MAX_TOOLS_PER_QUERY) -> None:
        self.max_tools = max_tools
        self.blocked: list[str] = []
        self.admitted: list[str] = []

    def register_hooks(self, registry: Any, **_kwargs: Any) -> None:
        from strands.hooks import BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self._before_tool_call)

    def _before_tool_call(self, event: Any) -> None:
        name = (event.tool_use or {}).get("name")
        if name not in SPECIALIST_TOOL_NAMES:
            return
        if len(self.admitted) < self.max_tools:
            self.admitted.append(str(name))
            return
        self.blocked.append(str(name))
        log.warning(
            "Francis attempted specialist tool %r after admitting %d (%s); the customer's rule is "
            "MAXIMUM %d tools per query, so the call was cancelled. If this recurs, the routing "
            "section of her orchestration prompt is not being followed.",
            name,
            len(self.admitted),
            ", ".join(self.admitted),
            self.max_tools,
        )
        event.cancel_tool = (
            f"Cancelled: this query already used {self.max_tools} specialist tools, which is the "
            "maximum allowed per query. Answer from the results already returned."
        )


# ---------------------------------------------------------------------------
# Session-scoped agent construction
# ---------------------------------------------------------------------------


def _memory_id() -> str | None:
    """AgentCore Memory id from the environment, or None.

    ``MEMORY_FRANCISMEMORY_ID`` is the variable name the CLI derives from the
    ``francisMemory`` resource in agentcore.json (``MEMORY_<NAME>_ID``, uppercased)
    and injects from DEPLOYED state. Under ``agentcore dev`` deployed state is
    empty, so this is None locally and no session manager is attached --
    deliberately, because there is no store to write to.
    """
    return os.environ.get("FRANCIS_MEMORY_ID") or os.environ.get("MEMORY_FRANCISMEMORY_ID")


#: CreateEvent is throttled per (actor, session) at ~5 TPS. batch_size > 1 makes
#: the session manager accumulate messages and write them in one call, so a
#: multi-turn exchange cannot trip that ceiling. Env-overridable because the right
#: value depends on turn length.
MEMORY_BATCH_SIZE = int(os.environ.get("FRANCIS_MEMORY_BATCH_SIZE", "5"))


#: The headers an analyst's identity can arrive on, in order of preference. There are TWO
#: because the two ways of calling a runtime send different headers, and picking one means
#: the other silently shares memory:
#:
#:   X-Amzn-Bedrock-AgentCore-Runtime-User-Id
#:       InvokeAgentRuntime's own `runtimeUserId` parameter (botocore's
#:       bedrock-agentcore/2024-02-28 model). This is what boto3 sends -- so it is what
#:       ui/chat.py and the edge Lambda send -- and being a first-class AgentCore header it
#:       needs no allowlist.
#:
#:   X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id
#:       an arbitrary header, which is what `agentcore invoke -H ...` and the AgentCore
#:       workshop use. It MUST be in the runtime's `requestHeaderAllowlist` in
#:       agentcore.json or AgentCore strips it before the entrypoint sees it.
#:
#: Measured: calling with `runtimeUserId="rebecca"` logged "no
#: x-amzn-...-custom-user-id header on this request" and fell back to the shared actor,
#: because only the custom name was checked. Both are read now.
ACTOR_HEADERS: Final[tuple[str, ...]] = (
    "x-amzn-bedrock-agentcore-runtime-user-id",
    "x-amzn-bedrock-agentcore-runtime-custom-user-id",
)


def actor_id_of(context: Any) -> str:
    """Whose memory this turn reads and writes.

    Per ANALYST, not per persona. The previous value was the constant FRANCIS_NAME, which
    made every analyst in the company share one memory: facts one person stated came back
    to everyone, and a summary of one case leaked into the next. That is wrong on privacy
    and wrong on usefulness.

    Read from the allowlisted custom header, exactly as the AgentCore workshop's Lab 2
    does. `FRANCIS_ACTOR_ID` still overrides for a single-tenant deployment, and the
    persona name remains the last resort so an un-headered call degrades to shared memory
    rather than failing -- but it is logged, because silently sharing memory is the failure
    mode worth seeing.
    """
    override = os.environ.get("FRANCIS_ACTOR_ID")
    if override:
        return override

    headers = getattr(context, "request_headers", None) or {}
    if isinstance(headers, dict):
        # Header names are case-insensitive on the wire and the SDK does not normalise
        # them, so match lower-cased rather than by exact key.
        lowered = {str(k).lower(): v for k, v in headers.items()}
        for name in ACTOR_HEADERS:
            value = lowered.get(name)
            if value:
                return str(value)[:120]

    # Log what DID arrive. "the header is missing" and "the header arrived under a name I
    # do not check" are indistinguishable without this, and the difference decides whether
    # the fix is in the caller or here.
    log.warning(
        "none of %s on this request (headers present: %s), so memory falls back to the "
        "shared actor %r. Pass runtimeUserId (boto3) or -H %s (CLI) to scope memory per "
        "analyst.",
        ", ".join(ACTOR_HEADERS),
        sorted(str(k).lower() for k in headers) if isinstance(headers, dict) else type(headers).__name__,
        FRANCIS_NAME,
        ACTOR_HEADERS[1],
    )
    return FRANCIS_NAME


def _session_manager(session_id: str, actor_id: str) -> Any | None:
    """An AgentCoreMemorySessionManager when memory is configured, else None.

    Strictly optional and env-gated. Failure to construct it is logged and
    swallowed: losing conversation persistence is a degraded feature, not a
    reason to fail the analyst's question.

    IMPORTANT, verified by running it: constructing the session manager makes a
    synchronous AWS ``ListEvents`` call to hydrate prior history. So this is not a
    cheap object -- it is a network round trip on the request path, and with an
    unreachable or non-existent store it fails at construction rather than at
    first use. That is precisely why it is env-gated and wrapped: under
    ``agentcore dev`` there is no deployed store and ``MEMORY_*_ID`` is unset (the
    CLI injects it from deployed state, which is empty locally), so an
    unconditional attach would make every local Francis run fail on a boto call
    to a store that does not exist.
    """
    memory_id = _memory_id()
    if not memory_id:
        return None
    try:
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )

        from bedrock_agentcore.memory.integrations.strands.config import RetrievalConfig

        # Long-term recall needs BOTH halves. The memory declares SEMANTIC and
        # SUMMARIZATION strategies with these namespace templates, and retrieval has to
        # name the same paths or the strategies extract records nobody ever reads -- which
        # looks identical to memory not working, because the write side succeeds silently.
        retrieval_config = {
            f"/analysts/{actor_id}/facts": RetrievalConfig(top_k=3, relevance_score=0.3),
            f"/summaries/{actor_id}/{session_id}": RetrievalConfig(
                top_k=3, relevance_score=0.3
            ),
        }

        config = AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=session_id,
            actor_id=actor_id,
            batch_size=MEMORY_BATCH_SIZE,
            retrieval_config=retrieval_config,
        )
        return AgentCoreMemorySessionManager(config)
    except Exception as exc:
        log.warning(
            "AgentCore Memory configured (%s) but the session manager could not be built: %s: %s. "
            "Continuing without conversation persistence.",
            memory_id,
            type(exc).__name__,
            exc,
        )
        return None


def build_francis(session_id: str, actor_id: str) -> tuple[Any, ToolCapHook]:
    """A fresh Francis Agent for one invocation, plus its tool-cap hook.

    Fresh per invocation, never cached: ``Agent.stream_async`` raises
    ``ConcurrencyException`` if a second invocation begins on the same instance
    while one is in flight, and a runtime microVM can serve overlapping requests.
    Continuity across turns comes from the session manager when memory is
    configured, not from reusing the object.

    The default ``ConcurrentToolExecutor`` is left in place: it is what runs a
    legitimate two-tool turn in parallel, which is the entire reason the 2-tool
    cap is a latency win rather than a latency cost.
    """
    from strands import Agent

    from agents import mantle
    from agents.models import (
        build_bedrock_model,
        build_retry_strategy,
        max_tokens_for,
        model_for,
    )

    cap_hook = ToolCapHook()
    model_id = model_for("synthesizer")

    # Route on the model id, exactly as agents/synthesize.py:_invoke_master does.
    # Francis shares the synthesizer's model, and that model now defaults to
    # `openai.gpt-5.6-luna` -- which is served ONLY by the bedrock-mantle endpoint
    # through the OpenAI Responses API. Handing that id to `build_bedrock_model`
    # builds a Converse client, and Converse rejects it:
    #
    #   ValidationException: ... calling the ConverseStream operation:
    #   The provided model identifier is invalid.
    #
    # Measured against the deployed runtime on 2026-08-11: Francis emitted her
    # `start` frame with all eight tools registered and then died on the first
    # model call, 5.9s in. The bug was invisible locally because MOCK_MODE=1
    # short-circuits before any model is built, and invisible to the type checker
    # because both builders return `Any`. `OpenAIResponsesModel` is a full Strands
    # provider, so tools, the ToolCapHook and ConcurrentToolExecutor all behave
    # identically -- this only changes which transport carries the call.
    if mantle.is_mantle_model(model_id):
        model = mantle.build_responses_model(
            model_id, max_tokens=max_tokens_for("synthesizer")
        )
    else:
        model = build_bedrock_model(model_id)

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        # The eight specialists plus whatever the reference gateway offers. The gateway
        # client is appended, not merged: it is an MCP client object, and Strands discovers
        # its tools at runtime rather than at construction, so the tool list here stays a
        # faithful record of what THIS code owns.
        tools=[*SPECIALIST_TOOLS, *([gateway] if (gateway := build_gateway_client()) else [])],
        hooks=[cap_hook],
        callback_handler=None,  # we stream events ourselves; no stdout printing
        retry_strategy=build_retry_strategy(),
        session_manager=_session_manager(session_id, actor_id),
        name=FRANCIS_NAME,
    )
    return agent, cap_hook


def _mock_answer(question: str) -> str:
    """Offline reply in Francis's own response shape, labelled as a placeholder.

    Uses her two required headings and deliberately omits ``###RECOMMENDATIONS``,
    because her rule is that the section appears only when the analyst asked for
    recommendations. It states plainly that no model ran, and it invents no
    finding, count or risk level.
    """
    return (
        "## EXECUTIVE SUMMARY\n"
        f"No risk level was determined: MOCK_MODE is on, so no model and no specialist agent was "
        f"invoked for {question!r}. Set MOCK_MODE=0 with Bedrock credentials for a real answer.\n\n"
        "## ANALYSIS\n"
        "- No specialist was called, so there are no findings to connect across domains.\n"
        f"- Available specialists: {', '.join(sorted(SPECIALIST_TOOL_NAMES))}.\n"
        "- This placeholder exercises the response shape only."
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


@app.entrypoint
async def invoke(payload, context):
    """Stream Francis's answer to one analyst question.

    The second parameter MUST be named ``context``: ``_takes_context`` matches the
    handler's second parameter literally by that name, so renaming it silently
    stops the ``RequestContext`` (and therefore the session id) from being
    injected.

    Frame sequence:
        start -> [tool_start | tool_done]* -> [delta]* -> done
    ``delta`` frames carry incremental text so the UI can render as Francis
    writes. A terminal ``error`` frame replaces the tail on failure -- and note
    that a mid-stream exception arrives as a data frame BEHIND HTTP 200, so
    consumers must check every frame for ``error``.
    """
    run_started = time.perf_counter()
    session_id = session_id_of(context)
    actor_id = actor_id_of(context)
    question = extract_prompt(payload)
    reset_call_log()

    task_id = app.add_async_task("francis_query", {"session_id": session_id})
    try:
        if not question:
            yield error_frame(
                "Provide a 'prompt' describing the fraud-analysis question, "
                "e.g. {'prompt': 'Is there income fraud on APP-1003?'}",
                kind="MissingPrompt",
                session_id=session_id,
            )
            return

        memory_id = _memory_id()
        yield {
            "event": "start",
            "session_id": session_id,
            "agent": FRANCIS_NAME,
            "tools": sorted(SPECIALIST_TOOL_NAMES),
            "max_tools_per_query": MAX_TOOLS_PER_QUERY,
            "memory": {"configured": bool(memory_id), "batch_size": MEMORY_BATCH_SIZE if memory_id else None},
            "mock_mode": mock_mode(),
            "measures_tokens": not mock_mode(),
            "at_ms": 0.0,
        }

        if mock_mode():
            answer = _mock_answer(str(question))
            yield {"event": "delta", "text": answer, "at_ms": elapsed_ms(run_started)}
            yield {
                "event": "done",
                "session_id": session_id,
                "agent": FRANCIS_NAME,
                "answer": answer,
                "specialists_called": [],
                "tool_calls": 0,
                "tool_calls_blocked": [],
                "latency_ms": elapsed_ms(run_started),
                "model": None,
                "tier": None,
                "measured": False,
                "source": "mock",
                **usage_frame_fields(None),
                "cost_usd": None,
                "mock_mode": True,
            }
            return

        agent, cap_hook = build_francis(session_id, actor_id)
        seen_tools: set[str] = set()
        chunks: list[str] = []
        result: Any = None

        async for event in agent.stream_async(question):
            if not isinstance(event, dict):
                continue

            # Incremental assistant text. `data` is the SDK's own callback key.
            text = event.get("data")
            if isinstance(text, str) and text:
                chunks.append(text)
                yield {"event": "delta", "text": text, "at_ms": elapsed_ms(run_started)}

            current = event.get("current_tool_use") or {}
            name = current.get("name")
            if name and name not in seen_tools:
                seen_tools.add(name)
                yield {
                    "event": "tool_start",
                    "tool": name,
                    "domain": name.removeprefix("call_").removesuffix("_agent") if name else None,
                    "at_ms": elapsed_ms(run_started),
                }

            if event.get("result") is not None:
                result = event["result"]

        answer = "".join(chunks) or (str(result) if result is not None else "")
        calls = call_log()
        for call in calls:
            yield {"event": "tool_done", **call, "at_ms": elapsed_ms(run_started)}

        model_id = getattr(getattr(agent, "model", None), "config", {}).get("model_id") if agent else None
        usage = usage_of(result)
        specialist_cost = sum(c["cost_usd"] for c in calls if c.get("cost_usd") is not None)
        all_specialist_costs_known = bool(calls) and all(c.get("cost_usd") is not None for c in calls)
        francis_cost = cost_of(model_id, usage)
        total_cost = (
            round(francis_cost + specialist_cost, 6)
            if francis_cost is not None and (all_specialist_costs_known or not calls)
            else None
        )

        yield {
            "event": "done",
            "session_id": session_id,
            "agent": FRANCIS_NAME,
            "answer": answer,
            "specialists_called": [c["agent_name"] for c in calls],
            "tool_calls": len(calls),
            # Non-empty means Francis tried to exceed her own 2-tool rule and the
            # hook stopped her. Surfaced rather than hidden.
            "tool_calls_blocked": cap_hook.blocked,
            "latency_ms": elapsed_ms(run_started),
            "model": model_id,
            "tier": tier_of(model_id),
            "model_latency_ms": model_latency_ms_of(result),
            "measured": usage is not None,
            "source": "bedrock",
            **usage_frame_fields(usage),
            "cost_usd": francis_cost,
            "specialist_cost_usd": specialist_cost if all_specialist_costs_known else None,
            "total_cost_usd": total_cost,
            "stop_reason": getattr(result, "stop_reason", None),
            "mock_mode": False,
        }
    except Exception as exc:
        log.exception("Francis invocation failed")
        yield error_frame(
            f"{type(exc).__name__}: {exc}",
            kind=type(exc).__name__,
            session_id=session_id,
        )
    finally:
        app.complete_async_task(task_id)


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8080")))
