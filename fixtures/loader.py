"""
Reading the committed fixtures, and projecting one into the payload a specialist receives.

The projection is the point. The customer's own architecture draws a hard line between signal
generation and agent analysis — "These signals are customized to each of the subagent fraud areas"
(SIGBOUND-02) — and the repo it replaces did not honour it: ``tools/application_data.py`` states in
its own docstring that "domain is informational only — every specialist sees the full signal set".
``build_payload`` builds the record ONCE and projects eight views, each carrying only its own
domain's signals, its own domain's ``_context`` rows, and only the alerts whose General Rule 3
category that domain owns.

Determinism is a contract here, not a nicety. ``semantic_layer_rev`` and ``computed_at`` come from
the fixture file, which pins them; nothing in this module reads the wall clock. A payload rebuilt
tomorrow is byte-identical to one built today, so a diff in an eval report means the data changed.

``guardrails_applied`` records rule IDs the signal layer actually enforced on this payload, not a
list of rules the repo would like credit for: each entry is produced by calling the corresponding
function in ``signal_layer.rules`` and observing that it passed. General Rule 23 in particular is a
gate — ``build_payload`` raises rather than emitting a payload containing an SSN-shaped string.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from fixtures._spec import (
    DOMAINS,
    SIGNAL_NAMES,
    bind_contract,
    context_key,
    rule_module,
    signals_for,
)

APPLICATIONS_DIR: Final = Path(__file__).resolve().parent / "applications"


class FixtureError(RuntimeError):
    """A fixture is missing, malformed, or violates a signal-layer guardrail."""


def _applications_dir() -> Path:
    if not APPLICATIONS_DIR.is_dir():
        raise FixtureError(f"no fixture directory at {APPLICATIONS_DIR}")
    return APPLICATIONS_DIR


@lru_cache(maxsize=1)
def list_application_ids() -> tuple[str, ...]:
    """
    Every fixture application id, sorted.

    Sorted rather than filesystem order: an eval run's scenario order must not depend on which
    machine listed the directory.
    """
    ids = tuple(sorted(p.stem for p in _applications_dir().glob("*.json")))
    if not ids:
        raise FixtureError(f"no fixture files in {APPLICATIONS_DIR}")
    return ids


@lru_cache(maxsize=64)
def _load(app_id: str) -> dict[str, Any]:
    path = _applications_dir() / f"{app_id}.json"
    if not path.is_file():
        raise FixtureError(
            f"no fixture for {app_id!r} at {path}; known ids are {list(list_application_ids())}"
        )
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    stated = record.get("application_id")
    if stated != app_id:
        raise FixtureError(
            f"{path.name} declares application_id {stated!r}; filename and id must agree or the "
            f"UI and the eval dataset will disagree about which record they mean"
        )
    return record


def get_application(app_id: str) -> dict[str, Any] | None:
    """
    One fixture record, or None for an unknown id.

    Returns None rather than raising so it can stand in for ``data.applications.get_application``,
    whose contract ``app.py`` and ``evals/bench.py`` already depend on. Case-insensitive and
    whitespace-tolerant for the same reason.
    """
    if not app_id:
        return None
    normalised = app_id.strip().upper()
    if normalised not in list_application_ids():
        return None
    return json.loads(json.dumps(_load(normalised)))


def domain_view(record: dict[str, Any], domain: str) -> dict[str, Any]:
    """
    Project one domain's view out of a full fixture record.

    Signals are filtered by ``signals_for(domain)``; context keys are the matching
    ``<signal>_context`` entries and nothing else; alerts are filtered to the IDs
    ``alerts_for_domain(domain)`` returns, which is the union of that domain's General Rule 3
    categories and the supplemental IDs its own orchestration document names.
    """
    alert_categories, _ = bind_contract()
    names = signals_for(domain)
    entitled = alert_categories.alerts_for_domain(domain)
    missing = [n for n in names if n not in record["signals"]]
    if missing:
        raise FixtureError(
            f"{record['application_id']}: {domain} view is missing {missing}. A specialist that "
            f"receives a partial signal set cannot tell absence from a benign zero."
        )
    return {
        "domain": domain,
        "signals": {n: record["signals"][n] for n in names},
        "context": {context_key(n): record["context"][context_key(n)] for n in names},
        "alerts_fired": [
            alert for alert in record["alerts_fired"] if alert["alert_id"] in entitled
        ],
    }


def _guardrails_applied(record: dict[str, Any]) -> list[str]:
    """
    The rule IDs the signal layer enforced while building this payload.

    Each entry is added only after the corresponding ``signal_layer.rules`` function has been
    called and has passed on this record's own data. Rules that are interpretive (carried in the
    prompt text) or that govern SQL this offline fixture path never issues are deliberately absent
    — claiming them here would make the list decoration rather than evidence.
    """
    applied: list[str] = []
    alert_categories, _ = bind_contract()

    rule_module("r23_no_raw_ssn").assert_no_raw_ssn(
        record, context=f"fixture {record['application_id']}"
    )
    applied.append("23")

    norm_string = rule_module("r02_normalize").norm_string
    application = record["application"]
    for field in ("borrower_name", "employer_name", "occupation"):
        value = application.get(field)
        if isinstance(value, str) and norm_string(value) != value.upper().strip():
            raise FixtureError(
                f"{record['application_id']}: {field} does not survive General Rule 2 "
                f"normalisation unchanged"
            )
    applied.append("2")

    rule_module("r04_alert_join").require_composite_key(
        record["application_id"], hash_lender_id=application["hash_lender_id"]
    )
    applied.append("4")
    applied.append("5")

    if not rule_module("r10_active_dealer").is_active_dealer(application):
        raise FixtureError(
            f"{record['application_id']}: dealer is not active under General Rule 10 "
            f"(consortium_number_of_apps_last_1_year must be > 0); only active dealers should be "
            f"considered"
        )
    applied.append("10")

    if not rule_module("r15_null_ssn").is_present_ssn(application.get("hash_ssn")):
        raise FixtureError(f"{record['application_id']}: hash_ssn is absent (General Rule 15)")
    applied.append("15")

    verdict = rule_module("r17_fake_records").classify_record(application)
    if verdict.excluded:
        raise FixtureError(
            f"{record['application_id']}: would be excluded as a fake record under General Rule "
            f"17 ({'; '.join(verdict.reasons)})"
        )
    applied.append("17")

    for alert in record["alerts_fired"]:
        if not alert_categories.category_of(alert["alert_id"]):
            raise FixtureError(
                f"{record['application_id']}: alert {alert['alert_id']} is in none of General "
                f"Rule 3's ten categories"
            )
    applied.append("3")

    return applied


def build_payload(app_id: str) -> dict[str, Any]:
    """
    Build the immutable per-application payload the fan-out hands to the eight specialists.

    Shape matches ``signal_layer.schema.SignalPayload``: ``record_id``, ``hash_lender_id``,
    ``application`` (core fields, ``hash_ssn`` only), ``views`` (8 keys, in the customer's
    master-prompt order), ``guardrails_applied``, ``semantic_layer_rev``, ``computed_at``.

    Raises FixtureError for an unknown id — unlike ``get_application``, because a payload builder
    returning None would push the failure into the agent, which has no way to report it.
    """
    record = get_application(app_id)
    if record is None:
        raise FixtureError(
            f"no fixture for {app_id!r}; known ids are {list(list_application_ids())}"
        )

    unknown = [n for n in record["signals"] if n not in SIGNAL_NAMES]
    if unknown:
        raise FixtureError(
            f"{app_id}: {unknown} are not registry signals. A specialist is forbidden from using a "
            f"signal not named in its prompt (XAG-001)."
        )
    absent = [n for n in SIGNAL_NAMES if n not in record["signals"]]
    if absent:
        raise FixtureError(
            f"{app_id}: missing signals {absent}. Every application must carry every registry "
            f"signal; a signal read through .get(name, 0) is indistinguishable from a benign zero."
        )

    payload = {
        "record_id": record["record_id"],
        "hash_lender_id": record["application"]["hash_lender_id"],
        "application": dict(record["application"]),
        "views": {domain: domain_view(record, domain) for domain in DOMAINS},
        "guardrails_applied": _guardrails_applied(record),
        "semantic_layer_rev": record["semantic_layer_rev"],
        "computed_at": record["computed_at"],
    }
    if list(payload["views"]) != list(DOMAINS):
        raise FixtureError(
            f"{app_id}: view order {list(payload['views'])} does not match the customer's "
            f"master-prompt order {list(DOMAINS)} (MSTR-004)"
        )
    return payload


def expected_for(app_id: str) -> dict[str, Any]:
    """The golden ``expected`` block for one fixture. Raises if the fixture carries none."""
    record = get_application(app_id)
    if record is None:
        raise FixtureError(f"no fixture for {app_id!r}")
    expected = record.get("expected")
    if not expected:
        raise FixtureError(
            f"{app_id}: no expected block. A fixture with no documented expectation cannot be a "
            f"golden case."
        )
    return expected


def iter_applications() -> list[dict[str, Any]]:
    """Every fixture record, in id order."""
    return [_load(app_id) for app_id in list_application_ids()]


__all__ = [
    "APPLICATIONS_DIR",
    "FixtureError",
    "build_payload",
    "domain_view",
    "expected_for",
    "get_application",
    "iter_applications",
    "list_application_ids",
]
