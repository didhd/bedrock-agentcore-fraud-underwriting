"""
Master synthesis: the eight analyses in, one validated 21-key adjudication out.

WHY THE SUBSTITUTION IS DONE THIS WAY. ``master_agent_prompt.txt`` is a template.
It carries its own eight placeholders - ``{identity_analysis}`` through
``{rings_analysis}`` - under bare uppercase headings (``IDENTITY:``, ``DEALER:``,
...) inside a ``SPECIALIZED FRAUD ANALYSES`` block delimited by 40-hyphen rule
lines (MSTR-003, MSTR-004). Filling those placeholders in place is what preserves
that structure byte-for-byte. The previous revision instead rebuilt the block with
``f"{d.upper()} ANALYSIS:\\n..."``, which produced ``IDENTITY ANALYSIS:`` where the
customer wrote ``IDENTITY:`` and dropped the rule lines entirely.

``str.format`` cannot be pointed at that file directly: its ``REQUIRED JSON
FORMAT`` block is full of literal braces, so ``format`` would read
``{"master_summary": ...}`` as a field reference and raise ``KeyError``.
:func:`build_synthesis_prompt` therefore doubles every brace in the file and
un-doubles only the eight known placeholders, then calls ``str.format`` - so the
substitution really is the placeholders' own mechanism, and any *other* brace in
her text is guaranteed to survive untouched. A round-trip assertion in
``tests/test_contracts.py`` pins that.

WHY STRUCTURED OUTPUT AND NOT ``str(result)``. The prompt's output contract is a
21-key JSON object with two string enums, eight integer flags, a 1200-character
ceiling and four cross-field rules (see :mod:`agents.contracts`). The previous
revision did ``str(result)`` and then wrapped ``json.loads`` in a bare
``except: pass``, so a malformed adjudication became ``{"raw": "..."}`` and a
missing key became silence. Here the invocation carries
``structured_output_model=Adjudication``: Strands converts the model to a forced
tool spec, and the tool's own handler validates the arguments and hands the
model back its validation errors mid-loop, so most repairs happen inside the
single invocation. If an invocation still yields nothing valid, exactly one bounded
repair invocation runs with the validation error appended, and then this module
**raises**. There is no path from here that emits an unvalidated adjudication.

WHY THE OFFLINE PATH IS PINNED, NOT COUNTED. The customer's decisioning rules
forbid deciding by volume: "Multiple agents may reference overlapping signals and
should not automatically be treated as independent corroboration" (MSTR-022),
"Prioritize evidence quality and corroboration over alert volume" (MSTR-023), "Do
not assign HIGH RISK based on isolated signals, alert volume, overlapping
findings, or ambiguous evidence alone" (MSTR-031). The previous revision's mock
synthesizer was ``if high >= 2 or (high >= 1 and possible >= 2): DECLINE`` - a
vote count, i.e. the exact thing those three rules prohibit, and the old test
suite asserted it. The offline path here instead reads a per-application pinned
expectation (:data:`OFFLINE_EXPECTATIONS`), so a deterministic run cannot be
mistaken for a decision procedure, and an unpinned application raises
:class:`UnpinnedApplicationError` rather than being scored.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from agents.contracts import (
    ADJUDICATION_FIELD_ORDER,
    STIPULATIONS_RECOMMENDATION,
    Adjudication,
)
from agents.models import (
    SYNTHESIZER_MAX_TOKENS,
    SYNTHESIZER_MODEL,
    build_bedrock_model,
    build_retry_strategy,
    tier_label,
)
from agents.pricing import cost_usd_or_none
from prompts.loader import (
    AGENT_NAMES,
    DOMAINS,
    MASTER_ANALYSIS_PLACEHOLDERS,
    MASTER_SYNTHESIS_PROMPT,
)

__all__ = [
    "MISSING_ANALYSIS_NOTICE",
    "OFFLINE_EXPECTATIONS",
    "OfflineExpectation",
    "SYNTHESIS_USER_MESSAGE",
    "SynthesisContractError",
    "SynthesisResult",
    "UnpinnedApplicationError",
    "build_synthesis_prompt",
    "normalize_analyses",
    "pinned_application_ids",
    "synthesize",
    "synthesize_offline",
]


class SynthesisContractError(RuntimeError):
    """Synthesis could not produce an adjudication satisfying the customer's contract."""


class UnpinnedApplicationError(LookupError):
    """Offline synthesis was asked for an application with no pinned expectation."""


#: The one line of non-customer text this port adds to the synthesis turn. Her
#: prompt is a complete instruction set and occupies the system prompt in full;
#: a Bedrock Converse turn still needs a user message, so this is it. It is named
#: and exported so it is auditable rather than buried in an f-string, and it
#: deliberately states no rule, band, threshold or format - every one of those
#: comes from her text.
SYNTHESIS_USER_MESSAGE: Final[str] = "Return the adjudication for the analyses above."

#: Substituted into a placeholder whose specialist failed. Fan-out degrades to
#: seven-of-eight (see :func:`agents.fanout.fan_out`), but MSTR-004 requires all
#: eight headings to be present, so the section is filled with an explicit
#: statement of absence rather than an empty string - an empty section reads as
#: "nothing suspicious found", which is a different claim.
MISSING_ANALYSIS_NOTICE: Final[str] = (
    "ANALYSIS UNAVAILABLE. The {agent_name} specialist did not return an analysis "
    "for this application in this run. Treat this dimension as unassessed. Do not "
    "infer either the presence or the absence of risk from this absence."
)


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def normalize_analyses(analyses: Mapping[str, Any]) -> dict[str, str]:
    """Coerce a fan-out result into ``{domain: analysis_text}``.

    Accepts the plain ``{domain: str}`` mapping and the
    ``{domain: SpecialistResult}`` mapping :func:`agents.fanout.fan_out` returns,
    so a caller never has to unpack one to feed the other.
    """
    out: dict[str, str] = {}
    for domain, value in analyses.items():
        if isinstance(value, str):
            out[domain] = value
            continue
        text = getattr(value, "analysis", None)
        if isinstance(text, str):
            out[domain] = text
            continue
        raise SynthesisContractError(
            f"analyses[{domain!r}] is a {type(value).__name__}; expected a string or "
            "an object with an .analysis string"
        )
    return out


def build_synthesis_prompt(analyses: Mapping[str, Any]) -> str:
    """Fill the verbatim master prompt's own eight placeholders.

    Every brace in the file is doubled first and only the eight placeholders are
    un-doubled, so ``str.format`` substitutes exactly those and the literal JSON
    braces in the ``REQUIRED JSON FORMAT`` block pass through unchanged.

    A domain with no analysis (a failed specialist) gets
    :data:`MISSING_ANALYSIS_NOTICE`, never an empty section.
    """
    texts = normalize_analyses(analyses)
    unknown = sorted(set(texts) - set(DOMAINS))
    if unknown:
        raise SynthesisContractError(
            f"unknown fraud domain(s) in analyses: {unknown}; known: {list(DOMAINS)}"
        )

    substitutions: dict[str, str] = {}
    for domain in DOMAINS:
        text = (texts.get(domain) or "").strip()
        substitutions[f"{domain}_analysis"] = text or MISSING_ANALYSIS_NOTICE.format(
            agent_name=AGENT_NAMES[domain]
        )

    missing = [p for p in MASTER_ANALYSIS_PLACEHOLDERS if p not in substitutions]
    if missing:
        raise SynthesisContractError(
            f"master prompt placeholders left unsubstituted: {missing}"
        )

    template = MASTER_SYNTHESIS_PROMPT.replace("{", "{{").replace("}", "}}")
    for placeholder in MASTER_ANALYSIS_PLACEHOLDERS:
        template = template.replace(f"{{{{{placeholder}}}}}", f"{{{placeholder}}}")
    return template.format(**substitutions)


def degraded_domains(analyses: Mapping[str, Any]) -> list[str]:
    """Domains with no usable analysis, in the customer's order."""
    texts = normalize_analyses(analyses)
    return [d for d in DOMAINS if not (texts.get(d) or "").strip()]


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _MetricsView:
    """Strands-shaped metrics view, so consumers written against ``AgentResult``
    can read synthesis telemetry the same way they read a specialist's."""

    accumulated_usage: dict[str, int] | None
    accumulated_metrics: dict[str, float]


@dataclass(slots=True)
class SynthesisResult:
    """The validated adjudication plus everything measured about producing it.

    ``application_id`` lives here, in the transport envelope, and deliberately not
    inside :class:`~agents.contracts.Adjudication` - the customer's 21-key contract
    does not contain it.
    """

    adjudication: Adjudication
    application_id: str
    model_id: str
    tier: str
    wall_ms: float
    latency_ms: float | None = None
    usage: dict[str, int] | None = None
    cost_usd: float | None = None
    stop_reason: str | None = None
    repair_attempts: int = 0
    degraded_domains: list[str] = field(default_factory=list)
    source: str = "bedrock"

    @property
    def metrics(self) -> _MetricsView:
        """Measured usage/latency in Strands' ``AgentResult.metrics`` shape."""
        accumulated: dict[str, float] = {}
        if self.latency_ms is not None:
            accumulated["latencyMs"] = self.latency_ms
        return _MetricsView(accumulated_usage=self.usage, accumulated_metrics=accumulated)

    def to_event(self) -> dict[str, Any]:
        """Flatten into the ``synthesis_completed`` streaming frame."""
        return {
            "event": "synthesis_completed",
            "application_id": self.application_id,
            "adjudication": self.adjudication.model_dump(),
            "model": self.model_id,
            "tier": self.tier,
            "wall_ms": round(self.wall_ms, 1),
            "latency_ms": None if self.latency_ms is None else round(self.latency_ms, 1),
            "usage": self.usage,
            "cost_usd": self.cost_usd,
            "stop_reason": self.stop_reason,
            "repair_attempts": self.repair_attempts,
            "degraded_domains": self.degraded_domains,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Offline expectations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OfflineExpectation:
    """A pinned adjudication outcome for one fixture application.

    ``why`` records where the expectation comes from, so nothing here reads as a
    decision this code computed.
    """

    overall_risk_decision: str
    recommendation_decision: str
    recommended_stipulations: str
    top_risk_factors: str
    flags: tuple[str, ...]
    why: str


_OFFLINE_NOTE: Final[str] = (
    "No model was called; this is the pinned offline expectation for this fixture."
)


def _expectation_from_fixture(application_id: str) -> OfflineExpectation | None:
    """Read a pinned expectation out of the fixture's own ``expected`` block.

    Fixtures added after this table was written carry their expectation inline,
    together with the requirement id and the customer sentence that justifies it
    (``fixtures/applications/*.json`` -> ``expected``). Reading it here keeps one
    source of truth rather than restating each outcome in two places, and it is
    still a *pinned* value: nothing is derived from the analyses, and no rule counts
    findings, flags or alerts. Returns None when there is no fixture or no block, so
    the caller still raises ``UnpinnedApplicationError``.
    """
    try:
        from fixtures.loader import expected_for  # noqa: PLC0415

        expected = expected_for(application_id)
    except Exception:
        return None
    if not isinstance(expected, Mapping):
        return None

    overall = expected.get("overall_risk_decision")
    recommendation = expected.get("recommendation_decision")
    if not overall or not recommendation:
        return None

    domains = expected.get("domains") or {}
    flags = tuple(
        d for d in DOMAINS if int((domains.get(d) or {}).get("flag", 0) or 0) == 1
    )

    # Only the stipulations outcome may carry stipulations (MSTR-036), and LOW RISK
    # must carry no top risk factors (MSTR-037). The fixture states whether each
    # field is populated; the text itself is generated from the pinned bands so the
    # validators are satisfied without inventing a finding.
    stipulations = ""
    if recommendation == STIPULATIONS_RECOMMENDATION:
        stipulations = str(
            expected.get("recommended_stipulations")
            or "Proof of income; Employment verification; Identity document review"
        )

    top_factors = ""
    if overall != "LOW RISK":
        stated = expected.get("top_risk_factors")
        if isinstance(stated, str) and stated.strip():
            top_factors = stated
        else:
            elevated = [
                d
                for d in DOMAINS
                if (domains.get(d) or {}).get("band") in ("POSSIBLE RISK", "HIGH RISK")
            ][:4]
            if len(elevated) < 3:
                elevated = (elevated + [d for d in DOMAINS if d not in elevated])[:3]
            top_factors = "; ".join(
                f"{i + 1}. {d.capitalize()} dimension assessed "
                f"{(domains.get(d) or {}).get('band', 'LOW RISK')} by its specialist"
                for i, d in enumerate(elevated)
            )

    justification = expected.get("overall_justification") or {}
    why = (
        f"fixtures/applications/{application_id}.json pins this outcome under "
        f"{justification.get('requirement_id', 'the customer contract')}: "
        f"{justification.get('customer_rule', '')}".strip()
    )

    return OfflineExpectation(
        overall_risk_decision=str(overall),
        recommendation_decision=str(recommendation),
        recommended_stipulations=stipulations,
        top_risk_factors=top_factors,
        flags=flags,
        why=why,
    )

#: Per-application pinned outcomes. Each one restates the intent the fixture's own
#: author wrote above it in ``data/applications.py`` - it is not derived from the
#: analyses, and no rule here counts findings, flags or alerts. Adding an
#: application means adding a row; it does not mean adding a heuristic.
OFFLINE_EXPECTATIONS: Final[dict[str, OfflineExpectation]] = {
    "APP-1001": OfflineExpectation(
        overall_risk_decision="LOW RISK",
        recommendation_decision="APPROVE",
        recommended_stipulations="",
        top_risk_factors="",
        flags=(),
        why=(
            "data/applications.py pins APP-1001 as 'Clean first-time buyer. Everything "
            "explainable. LOW risk.'"
        ),
    ),
    "APP-1002": OfflineExpectation(
        overall_risk_decision="LOW RISK",
        recommendation_decision="APPROVE",
        recommended_stipulations="",
        top_risk_factors="",
        flags=(),
        why=(
            "data/applications.py pins APP-1002 as 'Spousal co-borrower, thin file. "
            "Explainable. LOW risk.'"
        ),
    ),
    "APP-1003": OfflineExpectation(
        overall_risk_decision="MEDIUM RISK",
        recommendation_decision=STIPULATIONS_RECOMMENDATION,
        recommended_stipulations=(
            "Proof of income; Bank statement verification; Employment verification"
        ),
        top_risk_factors=(
            "1. Stated income exceeds verified income by 31 percent; "
            "2. Three distinct income amounts reported for the same SSN; "
            "3. Self-employment explains variance but not the magnitude of the "
            "overstatement"
        ),
        flags=("income",),
        why=(
            "data/applications.py pins APP-1003 as 'Income overstatement, ambiguous. "
            "MEDIUM / REVIEW+STIPULATE.' It is also this port's MSTR-035 case: income "
            "is the only flagged dimension, and income deviation alone must not "
            "produce DECLINE."
        ),
    ),
    "APP-1004": OfflineExpectation(
        overall_risk_decision="HIGH RISK",
        recommendation_decision="DECLINE",
        recommended_stipulations="",
        top_risk_factors=(
            "1. SSN linked to three distinct name and date-of-birth combinations in 47 "
            "days; "
            "2. Exact-unit address shared by five unrelated SSNs with phone and email "
            "reuse across four to five SSNs; "
            "3. Employer added eight new SSNs in 90 days on a nine-SSN base; "
            "4. Borrower SSN present in the negative file and linked to prior "
            "synthetic fraud"
        ),
        flags=("identity", "dealer", "employment", "synthetic", "rings"),
        why=(
            "data/applications.py pins APP-1004 as 'Synthetic identity + fraud-ring "
            "overlap. HIGH / DECLINE.'"
        ),
    ),
    "APP-1005": OfflineExpectation(
        overall_risk_decision="MEDIUM RISK",
        recommendation_decision=STIPULATIONS_RECOMMENDATION,
        recommended_stipulations=(
            "Proof of income; Employment verification; Fraud operations escalation"
        ),
        top_risk_factors=(
            "1. Five applications in seven days and nine in 30 days; "
            "2. Reported income rose more than 20 percent in 30 days with no employer "
            "change; "
            "3. Loan is 85 percent of stated annual income at 103 percent LTV"
        ),
        flags=("bustout", "income"),
        why=(
            "data/applications.py pins APP-1005 as 'Bust-out velocity pattern. "
            "MEDIUM-HIGH / REVIEW.' The middle band is required for the "
            "REVIEW AND APPLY STIPULATIONS recommendation (MSTR-033)."
        ),
    ),
}


def pinned_application_ids() -> tuple[str, ...]:
    """Applications the offline path can adjudicate."""
    return tuple(OFFLINE_EXPECTATIONS)


def _offline_summary(domain: str, flagged: bool, unavailable: bool) -> str:
    # An unassessed dimension and a cleared dimension are different statements, so
    # the absence branch is explicit rather than folded into either of the others.
    if unavailable:
        return (
            f"The {AGENT_NAMES[domain]} specialist returned no analysis in this run, so "
            f"this dimension is unassessed and its flag stays 0. {_OFFLINE_NOTE}"
        )
    if flagged:
        return (
            f"This dimension is flagged in the pinned expectation for this fixture. "
            f"{_OFFLINE_NOTE} A live run would state which corroborated findings "
            "support the flag."
        )
    return (
        f"This dimension is not flagged in the pinned expectation for this fixture. "
        f"{_OFFLINE_NOTE} A live run would state the clearing conclusion."
    )


def synthesize_offline(
    analyses: Mapping[str, Any], application_id: str
) -> SynthesisResult:
    """Deterministic synthesis from the pinned expectation for ``application_id``.

    Satisfies every validator in :class:`~agents.contracts.Adjudication` by
    construction, carries no token counts and no cost, and raises rather than
    scoring an application nobody pinned.
    """
    started = time.perf_counter()
    key = (application_id or "").strip().upper()
    expectation = OFFLINE_EXPECTATIONS.get(key) or _expectation_from_fixture(key)
    if expectation is None:
        raise UnpinnedApplicationError(
            f"no pinned offline expectation for {application_id!r}. Offline synthesis "
            "is fixture-pinned on purpose: the customer's MSTR-022/023/031 rules "
            "forbid deriving a verdict from finding or alert volume, so this path will "
            "not score an unknown application. Add a row to "
            f"agents.synthesize.OFFLINE_EXPECTATIONS (pinned: {list(OFFLINE_EXPECTATIONS)})."
        )

    unavailable = set(degraded_domains(analyses))
    flagged = set(expectation.flags)
    gap_note = (
        ""
        if not unavailable
        else (
            " Unassessed in this run (specialist returned nothing): "
            + ", ".join(sorted(unavailable, key=DOMAINS.index))
            + "."
        )
    )
    fields: dict[str, Any] = {
        "master_summary": (
            f"Pinned offline adjudication for {key}: "
            f"{expectation.overall_risk_decision} / "
            f"{expectation.recommendation_decision}. {expectation.why} "
            f"{_OFFLINE_NOTE}{gap_note}"
        ),
        "overall_risk_decision": expectation.overall_risk_decision,
        "recommendation_decision": expectation.recommendation_decision,
        "recommended_stipulations": expectation.recommended_stipulations,
        "top_risk_factors": expectation.top_risk_factors,
    }
    for domain in DOMAINS:
        fields[f"{domain}_summary"] = _offline_summary(
            domain, domain in flagged, domain in unavailable
        )
        # MSTR-039: a flag is 1 only on "clear, material, corroborated evidence".
        # A specialist that never answered produced no evidence, so an unavailable
        # dimension is 0 even when the pinned expectation flags it - the master
        # summary and degraded_domains carry the gap instead.
        fields[f"{domain}_flag"] = 1 if (domain in flagged and domain not in unavailable) else 0

    return SynthesisResult(
        adjudication=Adjudication(**fields),
        application_id=key,
        model_id=SYNTHESIZER_MODEL,
        tier=tier_label(SYNTHESIZER_MODEL),
        wall_ms=(time.perf_counter() - started) * 1000.0,
        latency_ms=None,
        usage=None,
        cost_usd=None,
        stop_reason=None,
        repair_attempts=0,
        degraded_domains=sorted(unavailable, key=DOMAINS.index),
        source="mock",
    )


# ---------------------------------------------------------------------------
# Live synthesis
# ---------------------------------------------------------------------------


def _build_master_agent(prompt: str) -> Any:
    """Fresh master agent for one synthesis.

    The whole substituted prompt is the system prompt, so line 1 is her persona
    sentence (MSTR-001). It is passed as a plain string rather than a cached
    ``SystemContentBlock`` list on purpose: the eight analyses sit in the middle of
    the text, so the prefix changes on every application and a ``cachePoint`` here
    would only ever pay the write premium and never read a hit.
    """
    from strands import Agent
    from strands.agent import NullConversationManager

    return Agent(
        model=build_bedrock_model(SYNTHESIZER_MODEL, max_tokens=SYNTHESIZER_MAX_TOKENS),
        system_prompt=prompt,
        tools=[],
        callback_handler=None,
        conversation_manager=NullConversationManager(),
        retry_strategy=build_retry_strategy(),
        name=AGENT_NAMES["master"],
        agent_id=AGENT_NAMES["master"],
    )


def _usage_from(result: Any) -> dict[str, int] | None:
    # The mantle adapter carries usage directly (already normalised to the Converse
    # convention, so a cached token is billed once).
    direct = getattr(result, "usage", None)
    if isinstance(direct, Mapping) and "inputTokens" in direct:
        return {
            "inputTokens": int(direct["inputTokens"]),
            "outputTokens": int(direct.get("outputTokens", 0)),
            "totalTokens": int(direct.get("totalTokens", 0)),
            "cacheReadInputTokens": int(direct.get("cacheReadInputTokens", 0)),
            "cacheWriteInputTokens": int(direct.get("cacheWriteInputTokens", 0)),
        }
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
    direct = getattr(result, "latency_ms", None)
    if isinstance(direct, (int, float)):
        return float(direct)
    metrics = getattr(result, "metrics", None)
    accumulated = getattr(metrics, "accumulated_metrics", None) if metrics is not None else None
    if isinstance(accumulated, Mapping):
        value = accumulated.get("latencyMs")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _synth_cost(result: Any, usage: dict[str, int] | None) -> float | None:
    """Cost of the synthesis call, from whichever family answered.

    ``agents.pricing`` only knows the Claude rate card, so a GPT-5.x id must be priced
    by ``agents.mantle``. The adapter already computed it for the single-attempt case;
    recompute from accumulated usage so a bounded repair (two billed invocations) is
    charged for both.
    """
    from agents import mantle

    if mantle.is_mantle_model(SYNTHESIZER_MODEL):
        return mantle.cost_usd(SYNTHESIZER_MODEL, usage)
    return cost_usd_or_none(SYNTHESIZER_MODEL, usage)


def _accumulate(into: dict[str, int] | None, more: dict[str, int] | None) -> dict[str, int] | None:
    """Sum usage across a repair attempt: both invocations were really billed."""
    if into is None:
        return more
    if more is None:
        return into
    return {key: into.get(key, 0) + more.get(key, 0) for key in set(into) | set(more)}


class _MantleResult:
    """Adapter giving a mantle response the shape the Converse path already returns.

    ``synthesize`` reads ``structured_output``, ``stop_reason``, ``metrics`` and usage
    off its result; keeping that surface identical means the validation, bounded-repair
    and cost paths are shared between the two model families rather than duplicated.
    """

    __slots__ = ("structured_output", "stop_reason", "usage", "latency_ms", "cost_usd", "raw_text")

    def __init__(self, adjudication: Any, response: dict[str, Any]) -> None:
        self.structured_output = adjudication
        # The Responses API has no tool-use stop reason here; the contract is enforced
        # by parsing and validating the JSON, so report the value the checks expect
        # once (and only once) the object has actually validated.
        self.stop_reason = "tool_use" if adjudication is not None else "end_turn"
        self.usage = response.get("usage")
        self.latency_ms = response.get("latency_ms")
        self.cost_usd = response.get("cost_usd")
        self.raw_text = response.get("text", "")


async def _invoke_master_mantle(prompt: str, user_message: str) -> tuple[Any, str]:
    """One master invocation against a GPT-5.x model on the bedrock-mantle endpoint.

    GPT-5.x is not on Converse, so there is no ``structured_output_model`` tool to
    force the shape. The customer's master prompt already demands strict JSON ("Return
    your output as STRICT JSON only", "Output MUST start with { and end with }"), so the
    text is parsed and validated against :class:`Adjudication` here. A failure returns a
    reason string and feeds the same bounded-repair loop as the Converse path.
    """
    import asyncio
    import json

    from agents import mantle

    response = await asyncio.to_thread(
        mantle.complete,
        SYNTHESIZER_MODEL,
        f"{prompt}\n\n{user_message}",
        max_output_tokens=SYNTHESIZER_MAX_TOKENS,
    )
    text = (response.get("text") or "").strip()
    if not text:
        return _MantleResult(None, response), "the model returned no text"

    # Tolerate a fenced block, but do not tolerate prose around the object: the
    # customer's prompt forbids it and accepting it here would hide a contract breach.
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return _MantleResult(None, response), (
            f"output was not valid JSON ({exc}); the master prompt requires the response "
            "to start with '{' and end with '}' with no commentary"
        )
    try:
        adjudication = Adjudication.model_validate(payload)
    except Exception as exc:
        return _MantleResult(None, response), f"output failed the 21-key contract: {exc}"
    return _MantleResult(adjudication, response), ""


async def _invoke_master(prompt: str, user_message: str) -> tuple[Any, str]:
    """One master invocation. Returns the result and a failure reason ('' if valid)."""
    from agents import mantle

    if mantle.is_mantle_model(SYNTHESIZER_MODEL):
        return await _invoke_master_mantle(prompt, user_message)

    agent = _build_master_agent(prompt)
    result = await agent.invoke_async(user_message, structured_output_model=Adjudication)

    adjudication = getattr(result, "structured_output", None)
    if adjudication is None:
        return result, (
            "the model returned no structured output "
            f"(stop_reason={getattr(result, 'stop_reason', None)!r}). The 21-key "
            "adjudication tool was not called with valid arguments."
        )
    if not isinstance(adjudication, Adjudication):
        return result, (
            f"structured_output is a {type(adjudication).__name__}, expected Adjudication"
        )
    if getattr(result, "stop_reason", None) != "tool_use":
        return result, (
            f"stop_reason is {getattr(result, 'stop_reason', None)!r}; a structured-output "
            "invocation must stop on 'tool_use'. The adjudication cannot be trusted to "
            "be complete."
        )
    return result, ""


async def synthesize(
    analyses: Mapping[str, Any],
    *,
    application_id: str = "",
    mock: bool | None = None,
    max_repair_attempts: int = 1,
) -> SynthesisResult:
    """Synthesize the eight analyses into one validated adjudication.

    Offline (``mock``) resolves to the fixture-pinned expectation for
    ``application_id``. Live invokes the master agent with
    ``structured_output_model=Adjudication``, allows ``max_repair_attempts``
    bounded retries on a contract failure, and then raises
    :class:`SynthesisContractError`. It never returns an unvalidated object and
    never returns a ``{"raw": ...}`` stand-in.
    """
    from agents.fanout import MOCK_MODE

    if (MOCK_MODE if mock is None else mock):
        return synthesize_offline(analyses, application_id)

    prompt = build_synthesis_prompt(analyses)
    gaps = degraded_domains(analyses)
    started = time.perf_counter()
    usage: dict[str, int] | None = None
    latency_ms: float | None = None
    user_message = SYNTHESIS_USER_MESSAGE
    reasons: list[str] = []

    for attempt in range(max_repair_attempts + 1):
        try:
            result, reason = await _invoke_master(prompt, user_message)
        except Exception as exc:  # a transport/model failure is also a bounded retry
            reasons.append(f"attempt {attempt + 1}: {type(exc).__name__}: {exc}")
            if attempt >= max_repair_attempts:
                raise SynthesisContractError(
                    "master synthesis failed on every attempt:\n  "
                    + "\n  ".join(reasons)
                ) from exc
            continue

        usage = _accumulate(usage, _usage_from(result))
        attempt_latency = _latency_from(result)
        if attempt_latency is not None:
            latency_ms = (latency_ms or 0.0) + attempt_latency

        if not reason:
            return SynthesisResult(
                adjudication=result.structured_output,
                application_id=(application_id or "").strip().upper(),
                model_id=SYNTHESIZER_MODEL,
                tier=tier_label(SYNTHESIZER_MODEL),
                wall_ms=(time.perf_counter() - started) * 1000.0,
                latency_ms=latency_ms,
                usage=usage,
                cost_usd=_synth_cost(result, usage),
                stop_reason=getattr(result, "stop_reason", None),
                repair_attempts=attempt,
                degraded_domains=gaps,
                source="bedrock",
            )

        reasons.append(f"attempt {attempt + 1}: {reason}")
        user_message = (
            f"{SYNTHESIS_USER_MESSAGE}\n\nThe previous attempt did not satisfy the "
            f"output contract: {reason}\nReturn all "
            f"{len(ADJUDICATION_FIELD_ORDER)} fields."
        )

    raise SynthesisContractError(
        "master synthesis did not produce a valid adjudication within "
        f"{max_repair_attempts + 1} attempt(s):\n  " + "\n  ".join(reasons)
    )
