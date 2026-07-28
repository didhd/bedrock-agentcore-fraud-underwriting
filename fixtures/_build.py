"""
The authoring tool that writes ``fixtures/applications/*.json``. Not imported at runtime.

WHY a generator rather than 14 hand-written JSON files: every application must carry all 60
signals and all 60 ``<signal>_context`` keys. Hand-writing 1,680 key/value pairs guarantees the
exact defect this task exists to fix — the old ``data/applications.py`` filled 20-30 signals per
record and let ``.get(k, 0)`` invent the rest, so half the "signals" driving the demo verdicts
were absent-and-defaulted. Here a missing signal is impossible: ``build()`` iterates
``SIGNAL_NAMES`` and raises on any scenario that has not supplied a value AND a context row.

Run it with ``python3 -m fixtures._build`` after editing a scenario. The JSON files are the
committed artifact; the tests read those, never this module, so a generator bug cannot hide
behind agreeing test expectations.

Every ``customer_interpretation`` string in the emitted context is read from
``prompts/verbatim/`` at generation time by ``_spec.interpretation_for`` — the fixtures quote the
customer's own sentence about a signal instead of paraphrasing her threshold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from fixtures._spec import (
    DOMAINS,
    SIGNAL_NAMES,
    bind_contract,
    context_key,
    interpretation_for,
)

APPLICATIONS_DIR: Final = Path(__file__).resolve().parent / "applications"

#: Pinned, not wall-clock. A fixture regenerated tomorrow must be byte-identical to one
#: regenerated today, or the diff Rebecca reviews is noise. Both values are recorded in every
#: payload so a reader can tell which semantic-layer revision produced the numbers.
SEMANTIC_LAYER_REV: Final = "pp-semantic-layer-2026-06"
COMPUTED_AT: Final = "2026-06-30T00:00:00Z"


@dataclass(frozen=True, slots=True)
class Alert:
    """One fired alert. ``finding_text`` is descriptive because agents may not cite numbers."""

    alert_id: int
    finding_text: str
    #: Which underlying behaviour produced it. Two alerts sharing a cause are ONE signal
    #: (IDEN-014, INC-015, SYN-016, BST-024, RNG-023) and the fixtures must make that checkable.
    underlying_behavior: str


@dataclass(frozen=True, slots=True)
class Scenario:
    """One application to emit. ``signals`` and ``context`` must cover SIGNAL_NAMES exactly."""

    app_id: str
    scenario: str
    why: str
    application: dict[str, Any]
    signals: dict[str, Any]
    context: dict[str, str]
    alerts: tuple[Alert, ...]
    expected: dict[str, Any]
    notes: tuple[str, ...] = field(default=())


def _context_block(signal: str, value: Any, example: str) -> dict[str, Any]:
    """
    Build one ``<signal>_context`` value.

    Shape is the same for all 60 so an agent can rely on it: the customer's own interpretation
    sentence, the file it came from, and example rows. SIGBOUND-04 requires the context be
    enough to sanity-check the value, spot a false positive, and reason further — a one-line
    prose blob (what the legacy module carried) satisfies none of those.
    """
    interpretation, source = interpretation_for(signal)
    return {
        "signal": signal,
        "value": value,
        "customer_interpretation": interpretation,
        "interpretation_source": source,
        "example_rows": example,
    }


def build(scenario: Scenario) -> dict[str, Any]:
    """Render one scenario to its committed JSON shape, refusing any gap."""
    missing_signals = [s for s in SIGNAL_NAMES if s not in scenario.signals]
    if missing_signals:
        raise ValueError(
            f"{scenario.app_id}: missing values for {missing_signals}. Every application must "
            f"carry every registry signal; an absent signal read through .get(name, 0) is the "
            f"defect this fixture set exists to remove."
        )
    extra_signals = [s for s in scenario.signals if s not in SIGNAL_NAMES]
    if extra_signals:
        raise ValueError(
            f"{scenario.app_id}: {extra_signals} are not in the registry. A specialist is "
            f"forbidden from using a signal not named in its prompt (XAG-001)."
        )
    missing_context = [s for s in SIGNAL_NAMES if s not in scenario.context]
    if missing_context:
        raise ValueError(
            f"{scenario.app_id}: missing example rows for {missing_context}. Agents are told to "
            f"validate every signal against a column ending in _context (BST-005 / RNG-005)."
        )

    alert_categories, _ = bind_contract()
    alerts_fired: list[dict[str, Any]] = []
    for alert in scenario.alerts:
        categories = alert_categories.category_of(alert.alert_id)
        if not categories:
            raise ValueError(
                f"{scenario.app_id}: alert {alert.alert_id} is in none of General Rule 3's ten "
                f"categories, so no specialist would ever receive it."
            )
        alerts_fired.append(
            {
                "alert_id": alert.alert_id,
                "category": categories[0],
                "categories": categories,
                "finding_text": alert.finding_text,
                "underlying_behavior": alert.underlying_behavior,
            }
        )

    return {
        "application_id": scenario.app_id,
        "record_id": scenario.app_id,
        "scenario": scenario.scenario,
        "why_this_fixture_exists": scenario.why,
        "synthetic_data_notice": (
            "SYNTHETIC DEMO RECORD. Fictional borrower, dealer and employer. No real PII. The "
            "SSN is carried only as hash_ssn (General Rule 23: never surface a raw SSN)."
        ),
        "semantic_layer_rev": SEMANTIC_LAYER_REV,
        "computed_at": COMPUTED_AT,
        "application": scenario.application,
        "signals": {s: scenario.signals[s] for s in SIGNAL_NAMES},
        "context": {
            context_key(s): _context_block(s, scenario.signals[s], scenario.context[s])
            for s in SIGNAL_NAMES
        },
        "alerts_fired": alerts_fired,
        "expected": scenario.expected,
        "notes": list(scenario.notes),
    }


def write_all(scenarios: list[Scenario], directory: Path = APPLICATIONS_DIR) -> list[Path]:
    """Emit every scenario, one file per application. Returns the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for scenario in scenarios:
        path = directory / f"{scenario.app_id}.json"
        payload = build(scenario)
        path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        written.append(path)
    return written


def expected_block(
    *,
    overall: str,
    recommendation: str,
    bands: dict[str, str],
    flags: dict[str, int],
    justifications: dict[str, tuple[str, str]],
    overall_justification: tuple[str, str],
    stipulations_expected: bool,
    top_risk_factors_expected: bool,
) -> dict[str, Any]:
    """
    Assemble the golden ``expected`` block for one application.

    ``justifications`` maps a domain to ``(requirement_id, the customer's sentence)``. The
    requirement id is what makes this block reviewable: an expectation without a cited rule is
    just this repo's opinion about the customer's data.
    """
    if set(bands) != set(DOMAINS):
        raise ValueError(f"bands must cover all 8 domains, got {sorted(bands)}")
    if set(flags) != set(DOMAINS):
        raise ValueError(f"flags must cover all 8 domains, got {sorted(flags)}")
    if set(justifications) != set(DOMAINS):
        raise ValueError(f"justifications must cover all 8 domains, got {sorted(justifications)}")
    for domain, band in bands.items():
        if band not in ("LOW RISK", "POSSIBLE RISK", "HIGH RISK"):
            raise ValueError(
                f"{domain}: {band!r} is not a specialist band. Specialists use "
                f"LOW RISK / POSSIBLE RISK / HIGH RISK (XAG-002); only the master says MEDIUM."
            )
    if overall not in ("LOW RISK", "MEDIUM RISK", "HIGH RISK"):
        raise ValueError(f"{overall!r} is not a master band (MSTR-014)")
    if recommendation not in ("APPROVE", "REVIEW AND APPLY STIPULATIONS", "DECLINE"):
        raise ValueError(f"{recommendation!r} is not a master recommendation (MSTR-015)")
    if (recommendation == "REVIEW AND APPLY STIPULATIONS") != stipulations_expected:
        raise ValueError(
            "recommended_stipulations is populated ONLY for REVIEW AND APPLY STIPULATIONS "
            "outcomes (MSTR-036)"
        )
    if (overall != "LOW RISK") != top_risk_factors_expected:
        raise ValueError(
            "top_risk_factors is a numbered list for MEDIUM/HIGH and an empty string for LOW "
            "(MSTR-037 / MSTR-038)"
        )
    return {
        "overall_risk_decision": overall,
        "recommendation_decision": recommendation,
        "overall_justification": {
            "requirement_id": overall_justification[0],
            "customer_rule": overall_justification[1],
        },
        "recommended_stipulations_populated": stipulations_expected,
        "top_risk_factors_populated": top_risk_factors_expected,
        "domains": {
            domain: {
                "band": bands[domain],
                "flag": flags[domain],
                "requirement_id": justifications[domain][0],
                "customer_rule": justifications[domain][1],
            }
            for domain in DOMAINS
        },
    }


__all__ = [
    "APPLICATIONS_DIR",
    "COMPUTED_AT",
    "SEMANTIC_LAYER_REV",
    "Alert",
    "Scenario",
    "build",
    "expected_block",
    "write_all",
]
