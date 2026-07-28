"""
Tests for the fixture set. These read the COMMITTED JSON, never the generator.

That distinction is the point of the file: if these tests imported ``fixtures._build`` and asserted
against the scenarios, a generator bug would produce broken JSON and agreeing expectations. Every
assertion below opens ``fixtures/applications/*.json`` and checks it against either the signal-layer
contract or the customer's own prompt text.

Four groups:

  contract      every signal present, every ``_context`` paired, every alert id real, views
                disjoint-by-domain and exhaustive in union
  safety        no raw-SSN pattern anywhere, General Rule 23
  expectations  every ``expected`` block cites a requirement id, and the calibration distribution
                matches the customer's "LOW should be the most common outcome"
  false positives  the five records that must stay LOW are present and marked LOW
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from fixtures import build_payload, get_application, list_application_ids
from fixtures._spec import (
    CONTEXT_SUFFIX,
    DERIVED_SIGNAL_NAMES,
    DOMAINS,
    NO_INTERPRETATION,
    SIGNAL_NAMES,
    VERBATIM_DIR,
    bind_contract,
    context_key,
    domains_for,
    interpretation_for,
    signals_for,
    try_registry,
)

APPLICATIONS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "applications"

#: General Rule 23's shape. Any match anywhere in a fixture is a failure.
RAW_SSN = re.compile(r"\d{3}-\d{2}-\d{4}")

#: A bare nine-digit run, checked only on SSN-ish keys: a nine-digit VIN-ish or ID-ish value
#: elsewhere is not an SSN, and a blanket rule would make the test noisy rather than strict.
BARE_NINE_DIGITS = re.compile(r"\b\d{9}\b")

#: Records whose scenario exists to prove a false positive stays LOW. Keys are the domain the rule
#: belongs to, so the test can assert the specific band rather than only the overall outcome.
FALSE_POSITIVE_RECORDS: dict[str, tuple[str, str]] = {
    "APP-1006": ("employment", "EMP-007"),
    "APP-1007": ("dealer", "DLR-009"),
    "APP-1008": ("employment", "EMP-017"),
    "APP-1009": ("rings", "RNG-013"),
    "APP-1010": ("identity", "IDEN-018"),
}

#: The five ids the UI and README already reference. Removing one breaks documentation.
LEGACY_IDS = ("APP-1001", "APP-1002", "APP-1003", "APP-1004", "APP-1005")

REQUIREMENT_ID = re.compile(
    r"^(IDEN|INC|EMP|DLR|STRW|SYN|BST|RNG|MSTR|XAG|SIGBOUND|PROC|ARCH|OVW)-\d{2,3}$"
)


def _raw(app_id: str) -> dict[str, Any]:
    return json.loads((APPLICATIONS_DIR / f"{app_id}.json").read_text(encoding="utf-8"))


def _flag_signals(records: dict[str, dict[str, Any]]) -> set[str]:
    """
    The signals the fixture set only ever expresses as a flag.

    Derived from the committed values rather than declared, so a signal that gains a third value
    later stops being treated as a flag without anyone remembering to update a list.
    """
    flags: set[str] = set()
    for signal in SIGNAL_NAMES:
        observed = {record["signals"][signal] for record in records.values()}
        if all(isinstance(v, bool) or v in (0, 1) for v in observed):
            flags.add(signal)
    return flags


@pytest.fixture(scope="module")
def app_ids() -> tuple[str, ...]:
    return list_application_ids()


@pytest.fixture(scope="module")
def records(app_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    return {app_id: _raw(app_id) for app_id in app_ids}


# ---------------------------------------------------------------------------
# Coverage of the decision space
# ---------------------------------------------------------------------------


def test_at_least_twelve_applications(app_ids: tuple[str, ...]) -> None:
    assert len(app_ids) >= 12, (
        f"only {len(app_ids)} fixtures; the customer's decision space needs at least 12 to cover "
        f"the LOW/MEDIUM/HIGH bands plus the documented false-positive exceptions"
    )


def test_legacy_ids_are_preserved(app_ids: tuple[str, ...]) -> None:
    missing = [i for i in LEGACY_IDS if i not in app_ids]
    assert not missing, f"{missing} are referenced by the UI and README and must not disappear"


def test_filename_matches_declared_application_id(records: dict[str, dict[str, Any]]) -> None:
    for app_id, record in records.items():
        assert record["application_id"] == app_id
        assert record["record_id"] == app_id, (
            "record_id is the agent-visible identifier (agents are forbidden from using "
            "application_id) and must resolve to the same record"
        )


# ---------------------------------------------------------------------------
# Signals and the _context convention
# ---------------------------------------------------------------------------


def test_every_fixture_carries_every_registry_signal(records: dict[str, dict[str, Any]]) -> None:
    for app_id, record in records.items():
        absent = [s for s in SIGNAL_NAMES if s not in record["signals"]]
        assert not absent, (
            f"{app_id} is missing {absent}. An absent signal read through .get(name, 0) is "
            f"indistinguishable from a benign zero, which is the defect this set replaces."
        )
        extra = [s for s in record["signals"] if s not in SIGNAL_NAMES]
        assert not extra, f"{app_id} carries non-registry signals {extra} (XAG-001)"


def test_every_signal_has_a_matching_context_key(records: dict[str, dict[str, Any]]) -> None:
    for app_id, record in records.items():
        for signal in SIGNAL_NAMES:
            key = context_key(signal)
            assert key in record["context"], (
                f"{app_id}: no {key}. Agents are instructed to validate signals using 'columns "
                f"that end in _context' (BST-005, RNG-005); a signal without one is unvalidatable."
            )


def test_every_context_key_ends_in_context_and_names_a_real_signal(
    records: dict[str, dict[str, Any]],
) -> None:
    for app_id, record in records.items():
        for key in record["context"]:
            assert key.endswith(CONTEXT_SUFFIX), f"{app_id}: {key} does not end in _context"
            base = key[: -len(CONTEXT_SUFFIX)]
            assert base in SIGNAL_NAMES, (
                f"{app_id}: {key} pairs with {base!r}, which is not a registry signal. The legacy "
                f"module's ad-hoc topic blobs (identity_context, ltv_context) are exactly this."
            )


def test_context_block_value_agrees_with_the_signal_value(
    records: dict[str, dict[str, Any]],
) -> None:
    for app_id, record in records.items():
        for signal in SIGNAL_NAMES:
            block = record["context"][context_key(signal)]
            assert block["signal"] == signal
            assert block["value"] == record["signals"][signal], (
                f"{app_id}: {signal} context echoes {block['value']!r} but the signal is "
                f"{record['signals'][signal]!r}; an agent told to validate against context would "
                f"be validating against a contradiction"
            )


def test_context_carries_example_rows_not_a_one_line_verdict(
    records: dict[str, dict[str, Any]],
) -> None:
    """
    SIGBOUND-04: context must let an agent sanity-check, spot false positives, AND reason on.

    A COUNT has to be backed by figures — the whole purpose of the customer's example
    ("dealer_apps_last_7d_context field shows example applications at the dealer in the last 7
    days") is that the agent can see the rows behind the number. A FLAG is sanity-checked by a
    statement of what is or is not there, so it may be stated in words.

    Which is which is derived from the values the fixture set actually holds
    (``_flag_signals``), not from a hand-maintained list, so a signal that later grows a third
    value starts being held to the count standard automatically.
    """
    flags = _flag_signals(records)
    for app_id, record in records.items():
        for signal in SIGNAL_NAMES:
            example = record["context"][context_key(signal)]["example_rows"]
            value = record["signals"][signal]
            assert isinstance(example, str) and len(example) >= 40, (
                f"{app_id}: {signal} example_rows is {example!r}. Too short to support independent "
                f"reasoning, which SIGBOUND-04 requires of every context field."
            )
            if signal not in flags:
                assert any(ch.isdigit() for ch in example), (
                    f"{app_id}: {signal} is the count {value!r} but its example_rows carries no "
                    f"figure, so an agent cannot sanity-check it against the rows behind it"
                )


def test_flag_context_is_interpretable_either_by_figure_or_by_absence(
    records: dict[str, dict[str, Any]],
) -> None:
    """
    A flag's context must let the agent tell absence from a benign zero.

    ``coborr_distinct_primary_borrowers_count = 0`` means one of two very different things — there
    is no co-borrower at all, or there is one linked to nobody — and the straw prompt's branch
    structure turns on which. Either form of evidence settles it: the underlying figures, or an
    explicit statement that the thing is not there. A row with neither is unusable.
    """
    absence_or_presence = re.compile(
        r"\b(no|none|never|not|nor|absent|missing|null|present)\b", re.IGNORECASE
    )
    for app_id, record in records.items():
        for signal in _flag_signals(records):
            example = record["context"][context_key(signal)]["example_rows"]
            has_figure = any(ch.isdigit() for ch in example)
            states_absence = bool(absence_or_presence.search(example))
            assert has_figure or states_absence, (
                f"{app_id}: {signal} is {record['signals'][signal]!r} and its context neither "
                f"carries a figure nor states what is or is not there: {example!r}"
            )


def test_customer_interpretation_is_quoted_from_a_verbatim_prompt(
    records: dict[str, dict[str, Any]],
) -> None:
    """The threshold text in a context block is the customer's sentence, not a paraphrase."""
    sources = {p.name: p.read_text(encoding="utf-8") for p in VERBATIM_DIR.glob("*.txt")}
    for app_id, record in records.items():
        for signal in SIGNAL_NAMES:
            block = record["context"][context_key(signal)]
            text = block["customer_interpretation"]
            if signal in NO_INTERPRETATION:
                assert text == "", (
                    f"{app_id}: {signal} has no stated interpretation in any customer document, so "
                    f"its context must carry an empty string rather than an invented band"
                )
                continue
            expected_text, expected_source = interpretation_for(signal)
            assert text == expected_text, f"{app_id}: {signal} interpretation drifted from source"
            source_name = Path(block["interpretation_source"]).name
            assert source_name in sources, f"{app_id}: {signal} cites unknown source {source_name}"
            assert text in sources[source_name], (
                f"{app_id}: {signal} interpretation is not a literal substring of {source_name}"
            )
            assert block["interpretation_source"] == expected_source


def test_near_duplicate_signal_names_are_both_materialised(
    records: dict[str, dict[str, Any]],
) -> None:
    """XAG-006: de-duping degrades whichever agent spells it the other way."""
    pairs = (
        ("employer_distinct_other_ssns_count", "employer_other_ssns_count"),
        ("phone_reuse_distinct_ssn_count", "phone_ssn_count"),
        ("ssn_income_increase_20_percent_or_more",
         "ssn_income_increased_20_percent_last_30d_rapid_growth"),
        ("loan_to_annual_income_ratio", "ssn_loan_80_percent_or_more_annual_income_high_ratio"),
    )
    for left, right in pairs:
        assert left in SIGNAL_NAMES and right in SIGNAL_NAMES
        for app_id, record in records.items():
            assert left in record["signals"] and right in record["signals"], (
                f"{app_id}: {left} and {right} must both exist under the names their own prompts "
                f"use"
            )


def test_every_fixture_signal_name_appears_in_a_customer_prompt() -> None:
    """
    No signal is this repo's invention.

    A name is acceptable either because it is a literal string in a customer document, or because
    it is listed in ``DERIVED_SIGNAL_NAMES`` with the customer sentence it is derived from — and
    ``test_derived_signal_names_cite_real_customer_text`` checks those sentences are real. A name
    that is neither is a fabrication.
    """
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in VERBATIM_DIR.glob("*.txt"))
    unexplained = [
        s for s in SIGNAL_NAMES if s not in corpus and s not in DERIVED_SIGNAL_NAMES
    ]
    assert not unexplained, (
        f"{unexplained} appear in no customer document and carry no recorded derivation"
    )
    spurious = [s for s in DERIVED_SIGNAL_NAMES if s in corpus]
    assert not spurious, (
        f"{spurious} ARE literal customer strings, so listing them as derived understates "
        f"fidelity; remove them from DERIVED_SIGNAL_NAMES"
    )


def test_derived_signal_names_cite_real_customer_text() -> None:
    """A derivation is only honest if the sentence it derives from actually exists."""
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in VERBATIM_DIR.glob("*.txt"))
    normalised = re.sub(r"\s+", " ", corpus)
    for name, anchor in DERIVED_SIGNAL_NAMES.items():
        if not anchor:
            # Derived from the process-guide PDF rather than a .txt prompt. The PDF is an
            # image-based scan, so a substring check is impossible; recorded as such deliberately
            # rather than backed by a quote this repo cannot verify.
            assert name == "dealer_apps_last_7d", (
                f"{name} records an empty derivation; only the PDF-sourced signal may do that"
            )
            continue
        assert re.sub(r"\s+", " ", anchor) in normalised, (
            f"{name} claims to derive from {anchor!r}, which is not in the customer corpus"
        )


# ---------------------------------------------------------------------------
# alerts_fired
# ---------------------------------------------------------------------------


def test_every_fixture_has_an_alerts_fired_array(records: dict[str, dict[str, Any]]) -> None:
    for app_id, record in records.items():
        assert isinstance(record["alerts_fired"], list), (
            f"{app_id}: alerts_fired must exist as an array (PROC-03), even when empty"
        )


def test_at_least_one_fixture_has_no_alerts_and_most_have_some(
    records: dict[str, dict[str, Any]],
) -> None:
    counts = {a: len(r["alerts_fired"]) for a, r in records.items()}
    assert any(c == 0 for c in counts.values()), (
        "no fixture has an empty alerts_fired array, so nothing proves the payload builder and the "
        "specialists handle zero alerts rather than assuming at least one"
    )
    assert sum(1 for c in counts.values() if c > 0) >= 10, counts


def test_every_alert_id_resolves_to_a_real_category(records: dict[str, dict[str, Any]]) -> None:
    alert_categories, _ = bind_contract()
    for app_id, record in records.items():
        for alert in record["alerts_fired"]:
            resolved = alert_categories.category_of(alert["alert_id"])
            assert resolved, (
                f"{app_id}: alert {alert['alert_id']} is in none of General Rule 3's ten "
                f"categories, so no specialist would ever be shown it"
            )
            assert alert["category"] in resolved
            assert alert["categories"] == resolved, (
                f"{app_id}: alert {alert['alert_id']} declares {alert['categories']} but General "
                f"Rule 3 says {resolved}; IDs are not unique across categories"
            )


def test_alert_finding_text_is_descriptive_and_never_cites_a_number(
    records: dict[str, dict[str, Any]],
) -> None:
    """All 8 specialists must 'reference specific findings from alerts, not alert numbers'."""
    citation = re.compile(r"\balert\s*(?:#|no\.?|number)?\s*\d+", re.IGNORECASE)
    for app_id, record in records.items():
        for alert in record["alerts_fired"]:
            text = alert["finding_text"]
            assert len(text) >= 40, f"{app_id}: alert {alert['alert_id']} finding_text is too terse"
            assert not citation.search(text), (
                f"{app_id}: alert {alert['alert_id']} finding_text cites an alert number "
                f"({text!r}); XAG-004 forbids the agent from doing so, so the text it is given "
                f"must not model it"
            )
            assert str(alert["alert_id"]) not in text


def test_every_alert_declares_its_underlying_behaviour(
    records: dict[str, dict[str, Any]],
) -> None:
    """Needed to make 'multiple alerts from the same underlying behavior = one signal' testable."""
    for app_id, record in records.items():
        for alert in record["alerts_fired"]:
            behaviour = alert["underlying_behavior"]
            assert isinstance(behaviour, str) and behaviour.strip(), (
                f"{app_id}: alert {alert['alert_id']} has no underlying_behavior, so IDEN-014 / "
                f"RNG-023 cannot be checked against this record"
            )


def test_a_fixture_exists_where_all_alerts_share_one_underlying_behaviour(
    records: dict[str, dict[str, Any]],
) -> None:
    """The 'alert volume alone does NOT indicate risk' record, and it must be marked LOW."""
    matches = [
        app_id
        for app_id, record in records.items()
        if len(record["alerts_fired"]) >= 5
        and len({a["underlying_behavior"] for a in record["alerts_fired"]}) == 1
    ]
    assert matches, (
        "no fixture fires 5+ alerts that all trace to one underlying behaviour, so the most "
        "repeated guardrail in the customer's corpus is untested"
    )
    for app_id in matches:
        expected = records[app_id]["expected"]
        assert expected["overall_risk_decision"] == "LOW RISK", (
            f"{app_id} fires {len(records[app_id]['alerts_fired'])} alerts from ONE behaviour and "
            f"is marked {expected['overall_risk_decision']}; IDEN-018 requires that alert volume "
            f"alone does not indicate risk"
        )


def test_the_high_risk_records_rest_on_independent_behaviours(
    records: dict[str, dict[str, Any]],
) -> None:
    """MSTR-031: HIGH must not be assignable from alert volume or overlapping findings."""
    highs = [a for a, r in records.items() if r["expected"]["overall_risk_decision"] == "HIGH RISK"]
    assert highs, "no HIGH RISK fixture, so the top band is untested"
    for app_id in highs:
        behaviours = {a["underlying_behavior"] for a in records[app_id]["alerts_fired"]}
        assert len(behaviours) >= 4, (
            f"{app_id} is HIGH RISK on only {len(behaviours)} distinct underlying behaviours "
            f"({sorted(behaviours)}); MSTR-029 requires multiple strongly corroborated patterns"
        )


# ---------------------------------------------------------------------------
# General Rule 23 — no raw SSN
# ---------------------------------------------------------------------------


def test_no_raw_ssn_pattern_anywhere_in_any_fixture() -> None:
    for path in sorted(APPLICATIONS_DIR.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        found = RAW_SSN.findall(text)
        assert not found, (
            f"{path.name} contains SSN-shaped text {found[:3]}; General Rule 23 forbids surfacing "
            f"a raw SSN anywhere"
        )


def test_ssn_fields_carry_only_hashes(records: dict[str, dict[str, Any]]) -> None:
    for app_id, record in records.items():
        application = record["application"]
        assert "borr_ssn" not in application and "ssn" not in application, (
            f"{app_id}: only hash_ssn may be projected (General Rule 23)"
        )
        hash_ssn = application["hash_ssn"]
        assert hash_ssn.startswith("hash_ssn_"), f"{app_id}: {hash_ssn!r} is not a hash"
        assert not BARE_NINE_DIGITS.search(hash_ssn)


def test_build_payload_enforces_rule_23_as_a_gate(app_ids: tuple[str, ...]) -> None:
    for app_id in app_ids:
        assert "23" in build_payload(app_id)["guardrails_applied"], (
            f"{app_id}: rule 23 is not recorded as applied, so the raw-SSN gate did not run"
        )


# ---------------------------------------------------------------------------
# Domain views
# ---------------------------------------------------------------------------


def test_payload_has_the_eight_views_in_the_customers_order(app_ids: tuple[str, ...]) -> None:
    for app_id in app_ids:
        assert list(build_payload(app_id)["views"]) == list(DOMAINS), (
            f"{app_id}: view order must match the master prompt's IDENTITY, DEALER, STRAW, "
            f"EMPLOYMENT, INCOME, SYNTHETIC, BUSTOUT, RINGS (MSTR-004)"
        )


def test_every_view_is_non_empty_and_holds_only_its_own_domains_signals(
    app_ids: tuple[str, ...],
) -> None:
    for app_id in app_ids:
        views = build_payload(app_id)["views"]
        for domain in DOMAINS:
            view = views[domain]
            assert view["signals"], f"{app_id}: {domain} view has no signals"
            difference = sorted(set(view["signals"]) ^ set(signals_for(domain)))
            assert not difference, (
                f"{app_id}: {domain} view disagrees with the registry on {difference}; "
                f"SIGBOUND-02 gives each domain its own signal set"
            )
            for name in view["signals"]:
                assert domain in domains_for(name), (
                    f"{app_id}: {domain} sees {name}, which belongs to {domains_for(name)}"
                )


def test_view_context_keys_match_view_signals_exactly(app_ids: tuple[str, ...]) -> None:
    for app_id in app_ids:
        for domain, view in build_payload(app_id)["views"].items():
            assert set(view["context"]) == {context_key(s) for s in view["signals"]}, (
                f"{app_id}: {domain} view's context keys do not correspond 1:1 to its signals"
            )


def test_the_union_of_the_eight_views_covers_the_whole_registry(
    app_ids: tuple[str, ...],
) -> None:
    for app_id in app_ids:
        views = build_payload(app_id)["views"]
        union: set[str] = set()
        for view in views.values():
            union |= set(view["signals"])
        assert union == set(SIGNAL_NAMES), (
            f"{app_id}: the 8 views omit {sorted(set(SIGNAL_NAMES) - union)}; a signal in the "
            f"registry that reaches no specialist is dead weight in the payload"
        )


def test_shared_signals_reach_both_of_their_domains(app_ids: tuple[str, ...]) -> None:
    for app_id in app_ids:
        views = build_payload(app_id)["views"]
        for signal in ("phone_reuse_distinct_ssn_count", "address_reuse_distinct_ssn_count",
                       "email_reuse_distinct_ssn_count", "rapid_app_velocity",
                       "ssn_income_high_variance_inconsistent_reporting"):
            for domain in domains_for(signal):
                assert signal in views[domain]["signals"], (
                    f"{app_id}: {signal} is named in the {domain} prompt but absent from its view"
                )


def test_view_alerts_are_restricted_to_that_domains_entitlement(
    app_ids: tuple[str, ...],
) -> None:
    alert_categories, _ = bind_contract()
    for app_id in app_ids:
        for domain, view in build_payload(app_id)["views"].items():
            entitled = alert_categories.alerts_for_domain(domain)
            for alert in view["alerts_fired"]:
                assert alert["alert_id"] in entitled, (
                    f"{app_id}: {domain} was shown alert {alert['alert_id']}, which its "
                    f"orchestration document does not route to it"
                )


def test_every_domain_receives_alerts_somewhere_in_the_fixture_set(
    app_ids: tuple[str, ...],
) -> None:
    totals = {d: 0 for d in DOMAINS}
    for app_id in app_ids:
        for domain, view in build_payload(app_id)["views"].items():
            totals[domain] += len(view["alerts_fired"])
    starved = [d for d, n in totals.items() if n == 0]
    assert not starved, (
        f"{starved} never receive a single fired alert across all 14 fixtures, so their "
        f"alert-handling instructions are untested"
    )


# ---------------------------------------------------------------------------
# Payload metadata and guardrails
# ---------------------------------------------------------------------------


def test_payload_is_deterministic(app_ids: tuple[str, ...]) -> None:
    """No wall clock: two builds of the same record must be byte-identical."""
    for app_id in app_ids:
        first = json.dumps(build_payload(app_id), sort_keys=True)
        second = json.dumps(build_payload(app_id), sort_keys=True)
        assert first == second, f"{app_id}: payload is not reproducible"


def test_payload_metadata_is_pinned_not_generated(app_ids: tuple[str, ...]) -> None:
    for app_id in app_ids:
        payload = build_payload(app_id)
        assert payload["semantic_layer_rev"] == "pp-semantic-layer-2026-06"
        assert payload["computed_at"] == "2026-06-30T00:00:00Z", (
            "computed_at must come from the fixture, not from datetime.now(): a fabricated "
            "timestamp makes every eval report irreproducible"
        )


def test_guardrails_applied_are_rule_ids_that_exist(app_ids: tuple[str, ...]) -> None:
    from fixtures._spec import _load_submodule_directly  # noqa: PLC0415

    try:
        source = _load_submodule_directly("rules._source")
    except (ImportError, ModuleNotFoundError):  # pragma: no cover
        pytest.skip("signal_layer.rules._source unavailable")
    for app_id in app_ids:
        applied = build_payload(app_id)["guardrails_applied"]
        assert applied, f"{app_id}: no guardrails recorded"
        unknown = [r for r in applied if r not in source.RULE_IDS]
        assert not unknown, f"{app_id}: {unknown} are not General Rule ids"
        assert len(set(applied)) == len(applied), f"{app_id}: duplicate rule ids in {applied}"


def test_payload_carries_the_composite_grain(app_ids: tuple[str, ...]) -> None:
    """General Rules 4 and 5: the grain is (application_id, hash_lender_id)."""
    for app_id in app_ids:
        payload = build_payload(app_id)
        assert payload["record_id"] == app_id
        assert payload["hash_lender_id"].startswith("hash_lndr_")


# ---------------------------------------------------------------------------
# expected blocks
# ---------------------------------------------------------------------------


def test_every_fixture_has_an_expected_block(records: dict[str, dict[str, Any]]) -> None:
    for app_id, record in records.items():
        expected = record.get("expected")
        assert expected, f"{app_id}: no expected block, so it cannot be a golden case"
        assert set(expected["domains"]) == set(DOMAINS), (
            f"{app_id}: expected block must state a band for all 8 domains"
        )


def test_expected_blocks_cite_a_requirement_id_and_a_customer_sentence(
    records: dict[str, dict[str, Any]],
) -> None:
    sources = {p.name: p.read_text(encoding="utf-8") for p in VERBATIM_DIR.glob("*.txt")}
    corpus = "\n".join(sources.values())
    normalised = re.sub(r"\s+", " ", corpus)
    for app_id, record in records.items():
        expected = record["expected"]
        entries = [expected["overall_justification"]] + list(expected["domains"].values())
        for entry in entries:
            rid = entry["requirement_id"]
            assert REQUIREMENT_ID.match(rid), f"{app_id}: {rid!r} is not a spec requirement id"
            rule = re.sub(r"\s+", " ", entry["customer_rule"]).strip()
            assert rule, f"{app_id}: {rid} cites no customer sentence"
            assert rule in normalised, (
                f"{app_id}: the sentence cited for {rid} is not a literal substring of the "
                f"customer's prompts:\n  {rule[:160]!r}"
            )


def test_expected_bands_use_the_right_vocabulary_per_layer(
    records: dict[str, dict[str, Any]],
) -> None:
    """XAG-002: specialists say POSSIBLE RISK; only the master says MEDIUM RISK."""
    for app_id, record in records.items():
        expected = record["expected"]
        assert expected["overall_risk_decision"] in ("LOW RISK", "MEDIUM RISK", "HIGH RISK")
        assert expected["recommendation_decision"] in (
            "APPROVE",
            "REVIEW AND APPLY STIPULATIONS",
            "DECLINE",
        )
        for domain, entry in expected["domains"].items():
            assert entry["band"] in ("LOW RISK", "POSSIBLE RISK", "HIGH RISK"), (
                f"{app_id}/{domain}: {entry['band']!r} - a specialist never says MEDIUM RISK"
            )
            assert entry["flag"] in (0, 1) and not isinstance(entry["flag"], bool)


def test_expected_recommendation_agrees_with_the_expected_band(
    records: dict[str, dict[str, Any]],
) -> None:
    for app_id, record in records.items():
        expected = record["expected"]
        overall = expected["overall_risk_decision"]
        recommendation = expected["recommendation_decision"]
        if recommendation == "REVIEW AND APPLY STIPULATIONS":
            assert overall == "MEDIUM RISK", (
                f"{app_id}: MSTR-033 permits stipulations only for MEDIUM RISK"
            )
        if overall == "LOW RISK":
            assert recommendation == "APPROVE", f"{app_id}: MSTR-032"
        assert expected["recommended_stipulations_populated"] == (
            recommendation == "REVIEW AND APPLY STIPULATIONS"
        ), f"{app_id}: MSTR-036"
        assert expected["top_risk_factors_populated"] == (overall != "LOW RISK"), (
            f"{app_id}: MSTR-037 / MSTR-038 - top_risk_factors is empty exactly when LOW RISK"
        )


def test_a_flag_of_one_requires_a_non_low_band(records: dict[str, dict[str, Any]]) -> None:
    """MSTR-039: flags are set only on clear, material, corroborated evidence."""
    for app_id, record in records.items():
        for domain, entry in record["expected"]["domains"].items():
            if entry["flag"] == 1:
                assert entry["band"] != "LOW RISK", (
                    f"{app_id}/{domain}: flag 1 with a LOW RISK band is incoherent"
                )
                assert record["expected"]["overall_risk_decision"] != "LOW RISK", (
                    f"{app_id}: {domain}_flag is 1 but the overall outcome is LOW RISK"
                )


def test_low_is_the_most_common_expected_outcome(records: dict[str, dict[str, Any]]) -> None:
    """XAG-003 / RNG-021 / BST-022: LOW must remain common, and HIGH uncommon."""
    overall = [r["expected"]["overall_risk_decision"] for r in records.values()]
    lows = overall.count("LOW RISK")
    highs = overall.count("HIGH RISK")
    assert lows > len(overall) / 2, (
        f"only {lows} of {len(overall)} fixtures are LOW; the customer states LOW should be the "
        f"most common outcome, and a fixture set skewed to fraud trains the opposite calibration"
    )
    assert highs <= 2, f"{highs} HIGH fixtures; HIGH RISK outcomes should remain uncommon"

    bands = [
        entry["band"]
        for record in records.values()
        for entry in record["expected"]["domains"].values()
    ]
    assert bands.count("LOW RISK") > len(bands) * 0.6, (
        f"only {bands.count('LOW RISK')} of {len(bands)} per-domain bands are LOW"
    )


def test_all_three_master_bands_and_all_three_recommendations_are_covered(
    records: dict[str, dict[str, Any]],
) -> None:
    overall = {r["expected"]["overall_risk_decision"] for r in records.values()}
    assert overall == {"LOW RISK", "MEDIUM RISK", "HIGH RISK"}, overall
    recommendations = {r["expected"]["recommendation_decision"] for r in records.values()}
    assert recommendations == {"APPROVE", "REVIEW AND APPLY STIPULATIONS", "DECLINE"}


def test_every_specialist_band_is_exercised_somewhere(
    records: dict[str, dict[str, Any]],
) -> None:
    seen: dict[str, set[str]] = {d: set() for d in DOMAINS}
    for record in records.values():
        for domain, entry in record["expected"]["domains"].items():
            seen[domain].add(entry["band"])
    for domain, bands in seen.items():
        assert "LOW RISK" in bands, f"{domain} is never LOW in any fixture"
        assert len(bands) >= 2, (
            f"{domain} only ever reports {bands}; a domain with one possible answer proves nothing"
        )


# ---------------------------------------------------------------------------
# The anti-false-positive records
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("app_id", "expectation"), sorted(FALSE_POSITIVE_RECORDS.items()))
def test_false_positive_fixture_is_present_and_low(
    app_id: str, expectation: tuple[str, str]
) -> None:
    domain, requirement_id = expectation
    record = get_application(app_id)
    assert record is not None, f"{app_id} is missing; it encodes {requirement_id}"
    expected = record["expected"]
    assert expected["overall_risk_decision"] == "LOW RISK", (
        f"{app_id} must stay LOW overall: it exists to prove {requirement_id} is honoured"
    )
    assert expected["recommendation_decision"] == "APPROVE"
    entry = expected["domains"][domain]
    assert entry["band"] == "LOW RISK", (
        f"{app_id}/{domain} must be LOW RISK - the record's whole purpose is that {requirement_id} "
        f"prevents an escalation here"
    )
    assert entry["flag"] == 0
    assert entry["requirement_id"] == requirement_id, (
        f"{app_id}/{domain} cites {entry['requirement_id']}, expected {requirement_id}"
    )
    assert all(e["flag"] == 0 for e in expected["domains"].values()), (
        f"{app_id}: no flag may be set on a record whose overall outcome is LOW RISK"
    )


def test_dealer_900_fixture_really_carries_a_900_plus_score() -> None:
    record = get_application("APP-1007")
    assert record is not None
    score = record["signals"]["dealer_consortium_score"]
    assert score >= 900, (
        f"APP-1007 exists to test DLR-009 and its dealer_consortium_score is only {score}; the "
        f"'900+ high' band must actually be reached for the rule to be exercised"
    )
    assert record["signals"]["phone_reuse_count_this_dealer"] == 0, (
        "APP-1007 must have NO coordination evidence, or it stops isolating the score"
    )
    assert record["signals"]["dealer_risk_score"] >= 900


def test_null_employer_fixture_really_has_null_contact_fields() -> None:
    record = get_application("APP-1006")
    assert record is not None
    application = record["application"]
    for field in (
        "employer_street_addr",
        "employer_city",
        "employer_state",
        "employer_zip",
        "employer_phone",
    ):
        assert application[field] is None, (
            f"APP-1006.{field} is {application[field]!r}; EMP-007 is about ALL of address, phone, "
            f"city, state and ZIP being missing"
        )


def test_staffing_and_military_fixtures_really_carry_high_ssn_counts() -> None:
    staffing = get_application("APP-1005")
    military = get_application("APP-1008")
    assert staffing is not None and military is not None
    assert staffing["signals"]["employer_distinct_other_ssns_count"] >= 100, (
        "EMP-011 calls 100+ SSNs normal for a large employer; the staffing fixture must exceed it"
    )
    assert military["signals"]["employer_distinct_other_ssns_count"] >= 1000
    assert military["signals"]["employer_concentration_pct"] > 20.0, (
        "DLR-015's >20% threshold must actually be crossed, with the near-a-base explanation "
        "present, for the exception to be exercised"
    )
    assert military["expected"]["domains"]["employment"]["band"] == "LOW RISK"
    assert staffing["expected"]["domains"]["employment"]["band"] == "LOW RISK", (
        "APP-1005 is MEDIUM overall; its employment domain must still clear independently"
    )


def test_multi_unit_fixture_separates_building_from_unit_overlap() -> None:
    benign = get_application("APP-1009")
    ring = get_application("APP-1004")
    assert benign is not None and ring is not None
    assert benign["signals"]["address_ssn_count_same_building"] >= 50
    assert benign["signals"]["address_ssn_count_same_unit"] == 1, (
        "APP-1009 is the benign case: heavy building-level overlap, no exact-unit overlap (SYN-010)"
    )
    assert benign["signals"]["is_multi_unit_address"] is True
    assert ring["signals"]["address_ssn_count_same_unit"] >= 4, (
        "APP-1004 is the contrasting case: RNG-014's 'repeated exact-unit reuse'"
    )
    assert (
        benign["signals"]["address_reuse_distinct_ssn_count"]
        > ring["signals"]["address_reuse_distinct_ssn_count"]
    ), (
        "the benign fixture must carry the LARGER raw reuse count, or the test does not prove that "
        "magnitude alone is not the signal"
    )


def test_no_coborrower_fixtures_exist_and_are_low_on_straw() -> None:
    records = {a: _raw(a) for a in list_application_ids()}
    without = [a for a, r in records.items() if r["signals"]["has_coborrower"] is False]
    assert len(without) >= 3, (
        f"only {len(without)} fixtures have no co-borrower; STRW-005's 2-3 sentence branch needs "
        f"more than a single instance to be reliably exercised"
    )
    for app_id in without:
        entry = records[app_id]["expected"]["domains"]["straw"]
        assert entry["band"] == "LOW RISK", (
            f"{app_id} has no co-borrower, so the primary straw mechanism is absent (STRW-008)"
        )
        assert entry["flag"] == 0


def test_a_spousal_coborrower_fixture_is_low_on_straw() -> None:
    records = {a: _raw(a) for a in list_application_ids()}
    spousal = [
        a
        for a, r in records.items()
        if r["application"].get("coborrower_relationship_type") == "spouse"
    ]
    assert spousal, "no spousal co-borrower fixture, so STRW-015's family rule is untested"
    for app_id in spousal:
        entry = records[app_id]["expected"]["domains"]["straw"]
        assert entry["band"] == "LOW RISK", (
            f"{app_id}: family relationships are generally benign (STRW-015)"
        )


def test_a_nonfamily_coborrower_fixture_reaches_the_straw_high_branch() -> None:
    records = {a: _raw(a) for a in list_application_ids()}
    matches = [
        a
        for a, r in records.items()
        if r["expected"]["domains"]["straw"]["band"] == "HIGH RISK"
    ]
    assert matches, "no straw HIGH fixture, so STRW-018 is untested"
    for app_id in matches:
        signals = records[app_id]["signals"]
        assert records[app_id]["application"]["coborrower_relationship_type"] != "spouse"
        assert signals["coborr_distinct_primary_borrowers_count"] >= 3, (
            "STRW-010: 3+ primaries with different dealers is the concerning shape"
        )
        assert signals["coborr_3_plus_apps_same_dealer_flag"] == 1
        assert signals["credit_score_gap"] > 100, "STRW-011"


def test_the_ambiguous_income_fixture_sits_just_above_the_25_percent_line() -> None:
    record = get_application("APP-1003")
    assert record is not None
    overstatement = record["signals"]["ssn_income_overstatement_percentage"]
    assert 25 < overstatement <= 35, (
        f"APP-1003 overstates by {overstatement}%; the ambiguous MEDIUM band needs a value just "
        f"above INC-012's 25% 'materially suspicious' line, not far above it"
    )
    assert record["signals"]["ssn_income_high_variance_inconsistent_reporting"] == 1
    assert record["expected"]["recommendation_decision"] == "REVIEW AND APPLY STIPULATIONS", (
        "MSTR-035: income deviation alone should not justify DECLINE"
    )


def test_the_bustout_fixture_carries_the_stated_velocity_and_exposure() -> None:
    record = get_application("APP-1005")
    assert record is not None
    signals = record["signals"]
    assert signals["ssn_apps_last_7d"] == 5 and signals["ssn_apps_last_30d"] == 9, (
        "the bust-out scenario is 5 apps in 7 days and 9 in 30; the eval dataset asserts those "
        "exact numbers"
    )
    assert signals["ssn_loan_80_percent_or_more_annual_income_high_ratio"] == 1
    assert signals["loan_to_annual_income_ratio"] >= 0.8
    assert record["expected"]["domains"]["bustout"]["flag"] == 1


# ---------------------------------------------------------------------------
# Agreement with the real signal layer, once it lands
# ---------------------------------------------------------------------------


def test_mirror_agrees_with_the_real_registry_if_it_has_landed() -> None:
    registry = try_registry()
    if registry is None:
        pytest.skip("signal_layer.registry has not landed yet; the mirror is the contract for now")
    real_names = set(getattr(registry, "SIGNAL_SPEC", {}))
    assert real_names, "signal_layer.registry exposes no SIGNAL_SPEC"
    assert real_names == set(SIGNAL_NAMES), (
        f"fixture mirror and signal_layer.registry disagree: only in registry "
        f"{sorted(real_names - set(SIGNAL_NAMES))}, only in mirror "
        f"{sorted(set(SIGNAL_NAMES) - real_names)}"
    )
    for domain in DOMAINS:
        assert set(registry.signals_for(domain)) == set(signals_for(domain)), (
            f"{domain}: mirror and registry disagree about domain ownership"
        )


def test_fixtures_satisfy_the_real_registry_if_it_has_landed(
    records: dict[str, dict[str, Any]],
) -> None:
    registry = try_registry()
    if registry is None:
        pytest.skip("signal_layer.registry has not landed yet")
    for app_id, record in records.items():
        for name in getattr(registry, "SIGNAL_SPEC", {}):
            assert name in record["signals"], f"{app_id} is missing registry signal {name}"
            assert context_key(name) in record["context"]
