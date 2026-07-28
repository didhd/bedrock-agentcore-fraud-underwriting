"""
The payload contract: what a specialist agent receives, and the projection that bounds it.

Point Predictive's process guide (section 3, "Agent Payload") specifies what each agent gets:
Application JSON, Signal JSON, fraud-specific instructions, risk framework, analysis constraints,
and context validation guidance. The first three of those are data and live here; the rest are the
verbatim prompt, which ``prompts.loader`` supplies.

Two properties are load-bearing and are the reason this module exists rather than passing dicts
around:

1. **A specialist sees only its own domain's signals.** ``project_view`` filters by
   ``registry.signals_for(domain)`` and ``alert_categories.alerts_for_domain(domain)``. Handing an
   agent the full signal set — which the previous version of this repo did, nine times per
   application — invites it to reason about fields its prompt gives it no interpretation for.

2. **``render_for_agent`` is deterministic.** Keys are emitted in registry order, JSON is sorted
   and stable. That is what makes the leading section of the rendered block identical across
   applications, which is the only way a prompt cache point can ever hit (see
   ``agents.models.cache_prefix_report`` — the prompts alone are too short to cache).

Raw SSNs never enter a payload. ``SignalPayload.application`` is expected to carry ``hash_ssn``
only, and ``signal_layer.rules.r23_no_raw_ssn`` is the gate that enforces it before anything
leaves this layer (General Rule 23: "NEVER INCLUDE RAW SSN VALUES").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final, Mapping, Sequence

from signal_layer.alert_categories import alerts_for_domain
from signal_layer.registry import DOMAINS, context_key, signals_for

#: Section headings in the rendered payload. Fixed strings so the cacheable prefix is stable.
CORE_HEADING: Final = "=== APPLICATION CORE FIELDS ==="
SIGNAL_HEADING: Final = "=== PRECOMPUTED FRAUD SIGNALS ==="
CONTEXT_HEADING: Final = "=== CONTEXT FIELDS (validate signals against these) ==="
ALERT_HEADING: Final = "=== ALERTS FIRED ==="


@dataclass(frozen=True, slots=True)
class AlertFinding:
    """
    One fired alert, carrying its finding text rather than only its number.

    Every specialist prompt forbids citing alert numbers ("Reference specific findings from
    alerts, not alert numbers"), which is only satisfiable if the payload describes what the alert
    found. ``alert_id`` and ``categories`` are retained for routing and audit, not for the agent
    to quote.
    """

    alert_id: int
    categories: tuple[str, ...]
    finding_text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "categories": list(self.categories),
            "finding_text": self.finding_text,
        }


@dataclass(frozen=True, slots=True)
class DomainView:
    """
    Exactly what one specialist is entitled to: its signals, their context, its alerts.

    Nothing else. There is no data tool on the agent side, so this object is the complete
    universe the specialist reasons over.
    """

    domain: str
    signals: Mapping[str, Any]
    context: Mapping[str, Any]
    alerts_fired: tuple[AlertFinding, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "signals": dict(self.signals),
            "context": dict(self.context),
            "alerts_fired": [a.as_dict() for a in self.alerts_fired],
        }


@dataclass(frozen=True, slots=True)
class SignalPayload:
    """
    The output of the signal layer for one application: eight domain views plus core fields.

    ``guardrails_applied`` carries General Rule IDs (``"RULE-17"``), not prose. It is what lets
    the demo show which governance rules the layer actually enforced for this record instead of
    asserting that governance was preserved.
    """

    record_id: str
    hash_lender_id: str
    application: Mapping[str, Any]
    views: Mapping[str, DomainView]
    guardrails_applied: tuple[str, ...] = ()
    semantic_layer_rev: str = ""
    computed_at: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def view(self, domain: str) -> DomainView:
        """The view for one domain. Raises KeyError rather than yielding an empty view."""
        if domain not in self.views:
            raise KeyError(
                f"no signal view for domain {domain!r}; payload carries {sorted(self.views)}"
            )
        return self.views[domain]

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "hash_lender_id": self.hash_lender_id,
            "application": dict(self.application),
            "views": {d: v.as_dict() for d, v in self.views.items()},
            "guardrails_applied": list(self.guardrails_applied),
            "semantic_layer_rev": self.semantic_layer_rev,
            "computed_at": self.computed_at,
            "notes": list(self.notes),
        }


def project_view(
    domain: str,
    signals: Mapping[str, Any],
    context: Mapping[str, Any],
    alerts_fired: Sequence[AlertFinding] = (),
) -> DomainView:
    """
    Build one domain's view by filtering a full signal set down to that domain's entitlement.

    Signals appear in registry order. A signal the domain owns but the record does not carry is
    omitted rather than defaulted: the specialist prompts require the agent to say a signal is
    absent, and a silent ``0`` would read as a measured value of zero. Alerts are filtered to the
    categories that map to this fraud domain.
    """
    entitled = signals_for(domain)
    view_signals = {name: signals[name] for name in entitled if name in signals}
    view_context = {
        context_key(name): context[context_key(name)]
        for name in entitled
        if context_key(name) in context
    }
    domain_alerts = alerts_for_domain(domain)
    view_alerts = tuple(a for a in alerts_fired if a.alert_id in domain_alerts)
    return DomainView(
        domain=domain,
        signals=view_signals,
        context=view_context,
        alerts_fired=view_alerts,
    )


def project_all(
    signals: Mapping[str, Any],
    context: Mapping[str, Any],
    alerts_fired: Sequence[AlertFinding] = (),
) -> dict[str, DomainView]:
    """One view per fraud domain, in the customer's master-prompt order."""
    return {d: project_view(d, signals, context, alerts_fired) for d in DOMAINS}


def _resolve(
    payload: SignalPayload | Mapping[str, Any], domain: str
) -> tuple[Mapping[str, Any], DomainView]:
    """Pull the application block and one domain view out of either payload representation."""
    if isinstance(payload, SignalPayload):
        return payload.application, payload.view(domain)

    application = payload.get("application", {})
    views = payload.get("views", {})
    if domain not in views:
        raise KeyError(
            f"no signal view for domain {domain!r}; payload carries {sorted(views)}"
        )
    raw = views[domain]
    if isinstance(raw, DomainView):
        return application, raw
    alerts = tuple(
        a
        if isinstance(a, AlertFinding)
        else AlertFinding(
            alert_id=int(a["alert_id"]),
            categories=tuple(a.get("categories", ())),
            finding_text=a.get("finding_text", ""),
        )
        for a in raw.get("alerts_fired", ())
    )
    view = DomainView(
        domain=raw.get("domain", domain),
        signals=raw.get("signals", {}),
        context=raw.get("context", {}),
        alerts_fired=alerts,
    )
    return application, view


def render_for_agent(payload: SignalPayload | Mapping[str, Any], domain: str) -> str:
    """
    Format the payload block the ``domain`` specialist receives, from a whole-application payload.

    Deterministic by construction: fixed headings, sorted JSON keys, stable separators. The
    application block comes first because it is the part most likely to be identical across
    domains for one record, and the signal block last because it is the most domain-specific --
    which is the ordering a cache point wants.

    Accepts either a ``SignalPayload`` or its ``as_dict()`` form, so a caller that has round-tripped
    the payload through JSON (the AgentCore streaming path does) does not have to rebuild it.
    """
    if isinstance(payload, str):
        # Already-rendered text (a caller that projected upstream, or a test double). Pass it
        # through unchanged rather than re-wrapping it: re-rendering a rendered block would
        # duplicate the headings and change the cacheable prefix.
        return payload

    application, view = _resolve(payload, domain)
    blocks = [
        CORE_HEADING,
        json.dumps(dict(application), indent=2, sort_keys=True, default=str),
        "",
        ALERT_HEADING,
        json.dumps([a.as_dict() for a in view.alerts_fired], indent=2, default=str),
        "",
        SIGNAL_HEADING,
        json.dumps(dict(view.signals), indent=2, sort_keys=True, default=str),
        "",
        CONTEXT_HEADING,
        json.dumps(dict(view.context), indent=2, sort_keys=True, default=str),
    ]
    return "\n".join(blocks)


__all__ = [
    "ALERT_HEADING",
    "CONTEXT_HEADING",
    "CORE_HEADING",
    "SIGNAL_HEADING",
    "AlertFinding",
    "DomainView",
    "SignalPayload",
    "project_all",
    "project_view",
    "render_for_agent",
]
