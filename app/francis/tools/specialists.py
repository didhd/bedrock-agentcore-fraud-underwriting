"""
The eight specialist sub-agents, exposed to Francis as tools.

THE TOOL NAMES ARE NOT A DESIGN CHOICE. Francis's system prompt is the customer's
verbatim orchestration document, and its routing table names these eight
functions literally:

    - 'identity' 'SSN' 'PII'        -> call_identity_agent ONLY
    - 'synthetic' 'fake identity'   -> call_synthetic_agent ONLY
    - 'straw' 'proxy' 'nominee'     -> call_straw_agent ONLY
    - 'bust' 'default' 'delinquent' -> call_bustout_agent ONLY
    - 'dealer' 'dealership'         -> call_dealer_agent ONLY
    - 'income' 'salary' 'earnings'  -> call_income_agent ONLY
    - 'employment' 'employer' 'job' -> call_employment_agent ONLY
    - 'ring' 'network' 'organized'  -> call_rings_agent ONLY

Rename one and the routing rule silently stops resolving: the model reads an
instruction to call a tool that does not exist, and either invents a call that
fails or narrates instead of routing. ``tests/test_runtime_contract.py`` asserts
the exact set of eight names for that reason.

Each tool builds a FRESH sub-Agent per call. That is not defensive style, it is
required: ``Agent.stream_async`` takes a non-blocking ``threading.Lock`` and
raises ``ConcurrencyException`` if a second invocation begins while one is in
flight. Strands' default ``ConcurrentToolExecutor`` runs a legitimate two-tool
turn in parallel, so a shared module-level Agent would fail exactly on the
two-tool case the customer's prompt explicitly allows.

Each sub-agent gets that domain's verbatim prompt from ``prompts/`` plus its own
``DomainView`` projection, so an agent still never sees another domain's signals
on the interactive path either.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from strands import tool

from app.runtime_support import (
    ANALYSIS_TITLES,
    agent_name_of,
    build_signal_payload,
    cost_of,
    make_renderer,
    mock_mode,
    model_latency_ms_of,
    normalize_application_id,
    usage_of,
)
from prompts.loader import DOMAINS, ORCHESTRATION_NOTES, SPECIALIST_PROMPTS

log = logging.getLogger("pp.francis.tools")

#: Populated by every tool call so the entrypoint can report which specialists
#: actually ran, with real per-call telemetry. Reset per invocation by
#: :func:`reset_call_log`. A list, not a counter, because the ``done`` frame
#: reports per-agent model/latency/usage and the 2-tool cap needs the count.
_CALL_LOG: list[dict[str, Any]] = []

#: The customer's rule, verbatim from the orchestration document: "SPEED
#: OPTIMIZATION: Call MAXIMUM 2 tools per query." Enforced in code as well as in
#: the prompt because it is load-bearing for the latency target -- a model that
#: fans out to five specialists on an ambiguous question turns a 3-second answer
#: into a 15-second one.
MAX_TOOLS_PER_QUERY = 2


def reset_call_log() -> None:
    """Clear the per-invocation call log. Called once per entrypoint invocation."""
    _CALL_LOG.clear()


def call_log() -> list[dict[str, Any]]:
    """The specialist calls made during this invocation, in completion order."""
    return list(_CALL_LOG)


def tool_call_count() -> int:
    """How many specialist tools have been invoked in this turn."""
    return len(_CALL_LOG)


def _build_subagent(domain: str) -> Any:
    """A fresh sub-Agent for one domain, on that domain's verbatim prompt.

    The system prompt is the specialist prompt plus that domain's orchestration
    notes -- the same two documents the customer's own sub-agent carries. Nothing
    is paraphrased or reordered; both strings come from ``prompts/`` which
    sha256-verifies them against the customer's files at import.
    """
    from strands import Agent

    from agents.models import build_bedrock_model, build_retry_strategy, model_for

    model_id = model_for(domain)
    system_prompt = f"{SPECIALIST_PROMPTS[domain]}\n\n{ORCHESTRATION_NOTES[domain]}"
    return Agent(
        model=build_bedrock_model(model_id),
        system_prompt=system_prompt,
        callback_handler=None,  # the sub-agent must not print to the parent's stdout
        retry_strategy=build_retry_strategy(),
        name=agent_name_of(domain),
    ), model_id


def _mock_answer(domain: str, question: str, application_id: str | None) -> str:
    """Offline stand-in, labelled as such in the text itself.

    Carries the exact OUTPUT title the domain's prompt defines, and no title at
    all for bustout and rings whose prompts define none ("Return only the final
    analysis."). Never invents a finding, a count or a score.
    """
    title = ANALYSIS_TITLES[domain]
    scope = f" for {application_id}" if application_id else ""
    body = (
        f"MOCK_MODE: the {agent_name_of(domain)} sub-agent was not invoked, so this is a "
        f"placeholder rather than an analysis{scope}. Question routed: {question!r}. "
        "Set MOCK_MODE=0 with Bedrock credentials to run the real specialist."
    )
    return f"{title}\n\n{body}" if title else body


async def _run_domain(domain: str, question: str, application_id: str = "") -> str:
    """Shared body for the eight tools.

    Returns the specialist's prose. On failure it returns an error *string*
    rather than raising: a raised exception inside a tool becomes a tool-result
    error the model then has to narrate around, whereas a returned string lets
    Francis report the gap in her own ANALYSIS section. Every outcome is recorded
    in ``_CALL_LOG`` with real telemetry or explicit ``None``s.
    """
    started = time.perf_counter()
    normalized = normalize_application_id(application_id, explicit=bool(application_id))
    entry: dict[str, Any] = {
        "domain": domain,
        "agent_name": agent_name_of(domain),
        "application_id": normalized,
        "question": question,
        "model": None,
        "latency_ms": None,
        "model_latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "source": "mock" if mock_mode() else "bedrock",
        "error": None,
    }
    _CALL_LOG.append(entry)

    # Defence in depth on the customer's MAXIMUM-2 rule. The hook in main.py
    # cancels a third tool call before it starts; this check catches a path that
    # bypasses the hook (a direct call, or a future Strands version that runs
    # tools without firing BeforeToolCallEvent) and refuses in-band.
    if len(_CALL_LOG) > MAX_TOOLS_PER_QUERY:
        entry["error"] = "tool-cap"
        log.warning(
            "specialist call #%d for %r exceeds the customer's MAXIMUM %d tools per query; refusing",
            len(_CALL_LOG),
            domain,
            MAX_TOOLS_PER_QUERY,
        )
        return (
            f"Not run: this query already used {MAX_TOOLS_PER_QUERY} specialist tools, which is "
            "the maximum per query. Answer from the results already returned."
        )

    try:
        if mock_mode():
            answer = _mock_answer(domain, question, normalized)
            entry["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
            return answer

        if not normalized:
            entry["error"] = "no application id"
            return (
                f"Not run: the {domain} specialist needs an application id. Ask the analyst which "
                "application to review, or pass application_id."
            )

        signal_payload, record, _source = build_signal_payload(normalized)
        if signal_payload is None and record is None:
            entry["error"] = "application not found"
            return f"Not run: application {normalized} was not found in the signal layer."

        view = make_renderer(record)(signal_payload, domain)
        agent, model_id = _build_subagent(domain)
        entry["model"] = model_id
        result = await agent.invoke_async(f"{question}\n\n{view}")

        usage = usage_of(result)
        entry.update(
            {
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                "model_latency_ms": model_latency_ms_of(result),
                "input_tokens": None if usage is None else usage["inputTokens"],
                "output_tokens": None if usage is None else usage["outputTokens"],
                "cost_usd": cost_of(model_id, usage),
            }
        )
        return str(result)
    except Exception as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        log.exception("specialist %s failed", domain)
        return f"The {agent_name_of(domain)} specialist failed: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# The eight tools. Names are the customer's routing-table names, verbatim.
#
# The docstring of each becomes the tool description the model routes on, so each
# one states the domain's scope in the customer's own vocabulary from the routing
# table (the keywords she routes by) -- not a rewritten summary.
# ---------------------------------------------------------------------------


@tool
async def call_identity_agent(question: str, application_id: str = "") -> str:
    """Analyze IDENTITY fraud: identity, SSN and PII misuse, identity element reuse, identity volatility.

    Args:
        question: the analyst's question, passed through unchanged.
        application_id: optional application id to scope the analysis, e.g. 'APP-1004'.
    """
    return await _run_domain("identity", question, application_id)


@tool
async def call_synthetic_agent(question: str, application_id: str = "") -> str:
    """Analyze SYNTHETIC IDENTITY fraud: synthetic and fake identities, manufactured credit files.

    Args:
        question: the analyst's question, passed through unchanged.
        application_id: optional application id to scope the analysis, e.g. 'APP-1004'.
    """
    return await _run_domain("synthetic", question, application_id)


@tool
async def call_straw_agent(question: str, application_id: str = "") -> str:
    """Analyze STRAW BORROWER fraud: straw, proxy and nominee borrowers, borrower/co-borrower role switching.

    Args:
        question: the analyst's question, passed through unchanged.
        application_id: optional application id to scope the analysis, e.g. 'APP-1004'.
    """
    return await _run_domain("straw", question, application_id)


@tool
async def call_bustout_agent(question: str, application_id: str = "") -> str:
    """Analyze BUST-OUT fraud: bust-out patterns, defaults and delinquency, application velocity and exposure.

    Args:
        question: the analyst's question, passed through unchanged.
        application_id: optional application id to scope the analysis, e.g. 'APP-1004'.
    """
    return await _run_domain("bustout", question, application_id)


@tool
async def call_dealer_agent(question: str, application_id: str = "") -> str:
    """Analyze DEALER fraud: dealer and dealership-orchestrated fraud, kept separate from dealer credit risk.

    Args:
        question: the analyst's question, passed through unchanged.
        application_id: optional application id to scope the analysis, e.g. 'APP-1004'.
    """
    return await _run_domain("dealer", question, application_id)


@tool
async def call_income_agent(question: str, application_id: str = "") -> str:
    """Analyze INCOME fraud: income, salary and earnings misrepresentation and overstatement.

    Args:
        question: the analyst's question, passed through unchanged.
        application_id: optional application id to scope the analysis, e.g. 'APP-1004'.
    """
    return await _run_domain("income", question, application_id)


@tool
async def call_employment_agent(question: str, application_id: str = "") -> str:
    """Analyze EMPLOYMENT fraud: employment, employer and job misrepresentation, fake or shell employers.

    Args:
        question: the analyst's question, passed through unchanged.
        application_id: optional application id to scope the analysis, e.g. 'APP-1004'.
    """
    return await _run_domain("employment", question, application_id)


@tool
async def call_rings_agent(question: str, application_id: str = "") -> str:
    """Analyze FRAUD RINGS: rings, networks and organized coordinated activity across borrowers, dealers and identifiers.

    Args:
        question: the analyst's question, passed through unchanged.
        application_id: optional application id to scope the analysis, e.g. 'APP-1004'.
    """
    return await _run_domain("rings", question, application_id)


#: The eight tools, in the order the customer's routing table lists them.
SPECIALIST_TOOLS: tuple[Any, ...] = (
    call_identity_agent,
    call_synthetic_agent,
    call_straw_agent,
    call_bustout_agent,
    call_dealer_agent,
    call_income_agent,
    call_employment_agent,
    call_rings_agent,
)

#: Tool name -> domain, so a hook can map a ``toolUse`` back to its domain.
TOOL_NAME_TO_DOMAIN: dict[str, str] = {f"call_{domain}_agent": domain for domain in DOMAINS}

#: The exact set of tool names Francis's routing table references. Asserted by
#: tests/test_runtime_contract.py.
SPECIALIST_TOOL_NAMES: frozenset[str] = frozenset(TOOL_NAME_TO_DOMAIN)
