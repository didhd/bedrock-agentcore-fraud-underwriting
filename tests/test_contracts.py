"""
Contract gate for the master adjudication, the fan-out, and the synthesis path.

WHY: ``master_agent_prompt.txt``'s ``REQUIRED JSON FORMAT`` block is a wire
contract whose keys become columns in ``UNDERWRITING_REPORT_OUTPUT``. Every rule
in it is asserted here in both directions - a conforming payload must be accepted
and a violating payload must be rejected - so "we implemented the contract" is a
test result rather than a claim. The old port emitted 22 keys with
``application_id`` first, validated nothing, and its mock synthesizer decided by
counting HIGH verdicts; each of those is a named test below.

Runs entirely offline. No Bedrock call, no AWS credentials, no network.

Run:  MOCK_MODE=1 python -m pytest tests/test_contracts.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import pytest
from pydantic import BaseModel, ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.contracts import (  # noqa: E402
    ADJUDICATION_FIELD_ORDER,
    ADJUDICATION_KEYS,
    ENUM_CONFLICT,
    MASTER_RISK_VALUES,
    MAX_SUMMARY_CHARS,
    PDF_RECOMMENDATION_VALUES,
    PDF_RISK_VALUES,
    PDF_TO_PROMPT_ENUM_MAP,
    RECOMMENDATION_VALUES,
    SPECIALIST_RISK_VALUES,
    STIPULATIONS_RECOMMENDATION,
    Adjudication,
    EnumConflictError,
    contains_emoji,
    map_pdf_enum,
    parse_top_risk_factors,
)
from agents.fanout import (  # noqa: E402
    ANALYSIS_TITLES,
    SpecialistResult,
    fan_out,
    fan_out_streaming,
    mock_analysis,
)
from agents.synthesize import (  # noqa: E402
    MISSING_ANALYSIS_NOTICE,
    OFFLINE_EXPECTATIONS,
    SYNTHESIS_USER_MESSAGE,
    SynthesisContractError,
    UnpinnedApplicationError,
    build_synthesis_prompt,
    degraded_domains,
    normalize_analyses,
    pinned_application_ids,
    synthesize,
    synthesize_offline,
)
from prompts.loader import (  # noqa: E402
    AGENT_NAMES,
    DOMAINS,
    MASTER_ANALYSIS_PLACEHOLDERS,
    MASTER_SYNTHESIS_PROMPT,
    SPECIALIST_PROMPTS,
)

# ---------------------------------------------------------------------------
# The spec's key order, written out literally.
#
# Deliberately NOT derived from DOMAINS or from the model: this list is the
# assertion. If it were computed from the same source the model computes from,
# a reordering of that source would move both and the test would pass while the
# customer's contract broke.
# ---------------------------------------------------------------------------
SPEC_ORDER = [
    "master_summary",
    "overall_risk_decision",
    "recommendation_decision",
    "recommended_stipulations",
    "top_risk_factors",
    "identity_summary",
    "identity_flag",
    "dealer_summary",
    "dealer_flag",
    "straw_summary",
    "straw_flag",
    "employment_summary",
    "employment_flag",
    "income_summary",
    "income_flag",
    "synthetic_summary",
    "synthetic_flag",
    "bustout_summary",
    "bustout_flag",
    "rings_summary",
    "rings_flag",
]

FLAG_FIELDS = [k for k in SPEC_ORDER if k.endswith("_flag")]
SUMMARY_FIELDS = [k for k in SPEC_ORDER if k.endswith("_summary")]


def payload(**overrides) -> dict:
    """A minimal spec-shaped, contract-satisfying adjudication payload."""
    data: dict = {
        "master_summary": "No material fraud concern across the eight dimensions.",
        "overall_risk_decision": "LOW RISK",
        "recommendation_decision": "APPROVE",
        "recommended_stipulations": "",
        "top_risk_factors": "",
    }
    for domain in DOMAINS:
        data[f"{domain}_summary"] = f"{domain} indicators are explainable."
        data[f"{domain}_flag"] = 0
    data.update(overrides)
    return data


def medium_payload(**overrides) -> dict:
    """A MEDIUM RISK / stipulations payload, the only band that carries them."""
    base = {
        "overall_risk_decision": "MEDIUM RISK",
        "recommendation_decision": STIPULATIONS_RECOMMENDATION,
        "recommended_stipulations": "Proof of income; Employment verification",
        "top_risk_factors": (
            "1. Stated income exceeds verified by 31 percent; "
            "2. Three distinct income amounts for one SSN; "
            "3. Loan is 85 percent of stated annual income"
        ),
        "income_flag": 1,
    }
    base.update(overrides)
    return payload(**base)


# ===========================================================================
# MSTR-013: exactly 21 keys, exact order, no application_id
# ===========================================================================



@pytest.fixture(autouse=True)
def claude_specialists(monkeypatch):
    """Pin the specialists to a Claude id for this module.

    The shipped assignment is GPT-5.6 Luna on bedrock-mantle (measured 4.5x faster and
    6.8x cheaper at identical band agreement). These tests assert properties of the
    CONVERSE path -- that ``_build_agent`` is called fresh per invocation, that eight
    Strands agents run concurrently, that a cachePoint report is carried -- so they must
    name a Converse model rather than inherit whatever the assignment happens to be.
    The mantle path has its own coverage in tests/test_fanout_mantle.py.
    """
    import agents.fanout as fanout_module

    monkeypatch.setattr(
        fanout_module, "model_for", lambda domain: "global.anthropic.claude-sonnet-5"
    )


def test_exactly_twenty_one_fields():
    assert len(Adjudication.model_fields) == 21
    assert len(SPEC_ORDER) == 21


def test_model_dump_preserves_the_customers_key_order():
    adjudication = Adjudication(**payload())
    assert list(adjudication.model_dump()) == SPEC_ORDER


def test_model_dump_json_preserves_the_customers_key_order():
    """The wire order, not just the dict order: this is what lands in Snowflake."""
    raw = Adjudication(**payload()).model_dump_json()
    assert list(json.loads(raw)) == SPEC_ORDER


def test_declared_field_order_matches_spec_order():
    assert list(Adjudication.model_fields) == SPEC_ORDER
    assert list(ADJUDICATION_FIELD_ORDER) == SPEC_ORDER
    assert list(ADJUDICATION_KEYS) == SPEC_ORDER


def test_master_summary_is_the_first_key():
    assert SPEC_ORDER[0] == "master_summary"
    assert list(Adjudication.model_fields)[0] == "master_summary"


def test_application_id_is_not_part_of_the_contract():
    """The old port made it key #1, producing 22 keys. It travels in the envelope."""
    assert "application_id" not in Adjudication.model_fields
    with pytest.raises(ValidationError):
        Adjudication(**payload(application_id="APP-1001"))


def test_dealer_precedes_straw_as_in_the_master_prompt():
    """Her master prompt orders DEALER before STRAW; the PDF's agent list does not."""
    assert SPEC_ORDER.index("dealer_summary") < SPEC_ORDER.index("straw_summary")


def test_every_domain_has_a_summary_and_a_flag():
    assert len(FLAG_FIELDS) == 8
    assert len(SUMMARY_FIELDS) == 9  # eight domains + master
    for domain in DOMAINS:
        assert f"{domain}_summary" in SPEC_ORDER
        assert f"{domain}_flag" in SPEC_ORDER


# ===========================================================================
# JSON schema: all 21 required, none nullable
# ===========================================================================


def test_json_schema_marks_all_twenty_one_fields_required():
    """Structured output constrains the model with this schema; an Optional field
    would silently drop out of ``required`` and become omissible."""
    schema = Adjudication.model_json_schema()
    assert schema["required"] == SPEC_ORDER
    assert len(schema["required"]) == 21
    assert sorted(schema["properties"]) == sorted(SPEC_ORDER)


def test_no_field_is_nullable_in_the_json_schema():
    schema = Adjudication.model_json_schema()
    for name, spec in schema["properties"].items():
        rendered = json.dumps(spec)
        assert '"null"' not in rendered, f"{name} admits null"
        assert "anyOf" not in spec, f"{name} is a union, i.e. probably Optional"


def test_json_schema_forbids_additional_properties():
    assert Adjudication.model_json_schema().get("additionalProperties") is False


def test_enum_values_reach_the_json_schema_verbatim():
    properties = Adjudication.model_json_schema()["properties"]
    assert properties["overall_risk_decision"]["enum"] == list(MASTER_RISK_VALUES)
    assert properties["recommendation_decision"]["enum"] == list(RECOMMENDATION_VALUES)
    for flag in FLAG_FIELDS:
        assert properties[flag]["enum"] == [0, 1]
        assert properties[flag]["type"] == "integer"


# ===========================================================================
# MSTR-014 / MSTR-015: enum membership is exact
# ===========================================================================


@pytest.mark.parametrize("value", MASTER_RISK_VALUES)
def test_every_risk_enum_value_is_accepted(value):
    field_overrides: dict = {"overall_risk_decision": value}
    if value != "LOW RISK":
        field_overrides["top_risk_factors"] = "1. a; 2. b; 3. c"
    assert Adjudication(**payload(**field_overrides)).overall_risk_decision == value


@pytest.mark.parametrize(
    "value",
    [
        "POSSIBLE RISK",  # the specialists' and the PDF's mid-band, not the master's
        "MEDIUM",
        "low risk",
        "LOW  RISK",
        "MANUAL REVIEW",
        "",
    ],
)
def test_risk_enum_rejects_everything_else(value):
    with pytest.raises(ValidationError):
        Adjudication(**payload(overall_risk_decision=value))


@pytest.mark.parametrize("value", RECOMMENDATION_VALUES)
def test_every_recommendation_enum_value_is_accepted(value):
    if value == STIPULATIONS_RECOMMENDATION:
        assert Adjudication(**medium_payload()).recommendation_decision == value
        return
    assert Adjudication(**payload(recommendation_decision=value)).recommendation_decision == value


@pytest.mark.parametrize(
    "value",
    [
        "APPROVE WITH STIPULATIONS",  # PDF-only
        "MANUAL REVIEW",  # PDF-only, has no prompt counterpart at all
        "REVIEW",
        "approve",
        "REVIEW AND APPLY STIPULATION",
        "",
    ],
)
def test_recommendation_enum_rejects_everything_else(value):
    with pytest.raises(ValidationError):
        Adjudication(**payload(recommendation_decision=value))


# ===========================================================================
# MSTR-016: flags are integers 0 or 1
# ===========================================================================


@pytest.mark.parametrize("flag", FLAG_FIELDS)
def test_flags_accept_zero_and_one_as_int(flag):
    for value in (0, 1):
        adjudication = Adjudication(**payload(**{flag: value}))
        got = getattr(adjudication, flag)
        assert got == value
        assert isinstance(got, int)


@pytest.mark.parametrize("value", [2, -1, "1", "0", 1.5, None])
def test_flags_reject_non_binary_values(value):
    with pytest.raises(ValidationError):
        Adjudication(**payload(identity_flag=value))


@pytest.mark.parametrize("value", [True, False])
def test_flags_reject_booleans(value):
    """``Literal[0, 1]`` alone would coerce True/False to 1/0. MSTR-016 says integer."""
    with pytest.raises(ValidationError) as excinfo:
        Adjudication(**payload(rings_flag=value))
    assert "boolean" in str(excinfo.value)


def test_flag_types_survive_a_json_round_trip():
    raw = Adjudication(**payload(identity_flag=1)).model_dump_json()
    reparsed = json.loads(raw)
    assert reparsed["identity_flag"] == 1
    assert isinstance(reparsed["identity_flag"], int)
    assert not isinstance(reparsed["identity_flag"], bool)


# ===========================================================================
# MSTR-010: no nulls anywhere
# ===========================================================================


@pytest.mark.parametrize("field", SPEC_ORDER)
def test_no_field_accepts_null(field):
    with pytest.raises(ValidationError):
        Adjudication(**payload(**{field: None}))


def test_null_error_names_the_field_and_cites_the_rule():
    with pytest.raises(ValidationError) as excinfo:
        Adjudication(**payload(dealer_summary=None))
    message = str(excinfo.value)
    assert "dealer_summary" in message
    assert "MSTR-010" in message


def test_missing_key_is_rejected():
    """MSTR-009: "Every field must always exist.\""""
    incomplete = payload()
    del incomplete["rings_flag"]
    with pytest.raises(ValidationError):
        Adjudication(**incomplete)


# ===========================================================================
# MSTR-011: summaries under 1200 characters
# ===========================================================================


@pytest.mark.parametrize("field", SUMMARY_FIELDS)
def test_summary_at_the_ceiling_is_accepted(field):
    assert MAX_SUMMARY_CHARS == 1200
    value = "a" * (MAX_SUMMARY_CHARS - 1)
    assert len(getattr(Adjudication(**payload(**{field: value})), field)) == 1199


@pytest.mark.parametrize("field", SUMMARY_FIELDS)
def test_summary_at_or_over_the_ceiling_is_rejected(field):
    """"under 1200 characters" - so 1200 itself is already too long."""
    with pytest.raises(ValidationError):
        Adjudication(**payload(**{field: "a" * MAX_SUMMARY_CHARS}))


def test_empty_summary_is_structurally_allowed():
    """The prompt caps length; it sets no floor. Not inventing one."""
    assert Adjudication(**payload(income_summary="")).income_summary == ""


# ===========================================================================
# MSTR-020: no emojis
# ===========================================================================


@pytest.mark.parametrize("emoji", ["\U0001f6a9", "✅", "⚠️", "\U0001f534"])
def test_emoji_is_rejected_in_a_summary(emoji):
    with pytest.raises(ValidationError) as excinfo:
        Adjudication(**payload(master_summary=f"Risk noted {emoji}"))
    assert "MSTR-020" in str(excinfo.value)


@pytest.mark.parametrize(
    "text",
    [
        "Loan-to-income 0.85 - elevated but explainable.",
        "Income rose 40% — no employer change.",
        "Verified income vs. stated: ±31%.",
        "Ratio ≥ 0.8 warrants scrutiny.",
    ],
)
def test_ordinary_underwriting_punctuation_is_not_mistaken_for_an_emoji(text):
    """An over-broad emoji regex would reject the customer's own prose."""
    assert not contains_emoji(text)
    assert Adjudication(**payload(master_summary=text)).master_summary == text


def test_emoji_detector_actually_matches_something():
    """Canary: the absence assertions above must not be vacuous."""
    assert contains_emoji("\U0001f6a9")


# ===========================================================================
# MSTR-036 / MASTER-08: recommended_stipulations is gated on one outcome
# ===========================================================================


def test_stipulations_outcome_requires_populated_stipulations():
    with pytest.raises(ValidationError) as excinfo:
        Adjudication(**medium_payload(recommended_stipulations=""))
    assert "MSTR-036" in str(excinfo.value)


def test_stipulations_outcome_with_stipulations_is_accepted():
    adjudication = Adjudication(**medium_payload())
    assert adjudication.recommendation_decision == STIPULATIONS_RECOMMENDATION
    assert adjudication.recommended_stipulations


@pytest.mark.parametrize("recommendation", ["APPROVE", "DECLINE"])
def test_other_outcomes_must_leave_stipulations_empty(recommendation):
    overrides = {
        "recommendation_decision": recommendation,
        "recommended_stipulations": "Proof of income",
    }
    if recommendation == "DECLINE":
        overrides["overall_risk_decision"] = "HIGH RISK"
        overrides["top_risk_factors"] = "1. a; 2. b; 3. c"
    with pytest.raises(ValidationError) as excinfo:
        Adjudication(**payload(**overrides))
    assert "MSTR-036" in str(excinfo.value)


@pytest.mark.parametrize("recommendation", ["APPROVE", "DECLINE"])
def test_other_outcomes_with_empty_stipulations_are_accepted(recommendation):
    overrides: dict = {"recommendation_decision": recommendation}
    if recommendation == "DECLINE":
        overrides["overall_risk_decision"] = "HIGH RISK"
        overrides["top_risk_factors"] = "1. a; 2. b; 3. c"
    assert Adjudication(**payload(**overrides)).recommended_stipulations == ""


# ===========================================================================
# MSTR-033: the stipulations outcome belongs to MEDIUM RISK only
# ===========================================================================


@pytest.mark.parametrize("risk", ["LOW RISK", "HIGH RISK"])
def test_stipulations_outcome_outside_medium_risk_is_rejected(risk):
    overrides = {
        "overall_risk_decision": risk,
        "recommendation_decision": STIPULATIONS_RECOMMENDATION,
        "recommended_stipulations": "Proof of income",
    }
    if risk == "HIGH RISK":
        overrides["top_risk_factors"] = "1. a; 2. b; 3. c"
    with pytest.raises(ValidationError) as excinfo:
        Adjudication(**payload(**overrides))
    assert "MSTR-033" in str(excinfo.value)


# ===========================================================================
# MSTR-037 / MSTR-038: top_risk_factors is band-conditional
# ===========================================================================


def test_low_risk_requires_empty_top_risk_factors():
    with pytest.raises(ValidationError) as excinfo:
        Adjudication(**payload(top_risk_factors="1. a; 2. b; 3. c"))
    assert "MSTR-038" in str(excinfo.value)


def test_low_risk_with_empty_top_risk_factors_is_accepted():
    assert Adjudication(**payload()).top_risk_factors == ""


@pytest.mark.parametrize("risk", ["MEDIUM RISK", "HIGH RISK"])
@pytest.mark.parametrize("count", [3, 4])
def test_medium_and_high_accept_three_or_four_numbered_factors(risk, count):
    factors = "; ".join(f"{i}. factor {i}" for i in range(1, count + 1))
    overrides = {"overall_risk_decision": risk, "top_risk_factors": factors}
    if risk == "MEDIUM RISK":
        overrides["recommendation_decision"] = STIPULATIONS_RECOMMENDATION
        overrides["recommended_stipulations"] = "Proof of income"
    adjudication = Adjudication(**payload(**overrides))
    assert len(parse_top_risk_factors(adjudication.top_risk_factors)) == count


@pytest.mark.parametrize("count", [0, 1, 2, 5, 6])
def test_high_risk_rejects_the_wrong_number_of_factors(count):
    factors = "; ".join(f"{i}. factor {i}" for i in range(1, count + 1))
    with pytest.raises(ValidationError) as excinfo:
        Adjudication(
            **payload(overall_risk_decision="HIGH RISK", top_risk_factors=factors)
        )
    assert "MSTR-037" in str(excinfo.value)


@pytest.mark.parametrize(
    "factors",
    [
        "Rapid velocity; SSN reuse; Income jump",  # unnumbered
        "- Rapid velocity; - SSN reuse; - Income jump",  # bulleted
        "1) a; 2) b; 3) c",  # wrong delimiter after the digit
        "1.a; 2.b; 3.c",  # no space after the period
    ],
)
def test_high_risk_rejects_unnumbered_factor_lists(factors):
    with pytest.raises(ValidationError) as excinfo:
        Adjudication(
            **payload(overall_risk_decision="HIGH RISK", top_risk_factors=factors)
        )
    assert "MSTR-037" in str(excinfo.value)


def test_newline_separated_numbered_list_is_accepted():
    factors = "1. First factor\n2. Second factor\n3. Third factor"
    adjudication = Adjudication(
        **payload(overall_risk_decision="HIGH RISK", top_risk_factors=factors)
    )
    assert len(parse_top_risk_factors(adjudication.top_risk_factors)) == 3


def test_the_prompts_own_illustrated_factor_string_validates():
    """Verbatim from master_agent_prompt.txt's TOP RISK FACTORS example."""
    factors = (
        "1. Rapid application velocity with 5 apps in 7 days; "
        "2. SSN linked to 3 different addresses; "
        "3. Income increased 40% without job change; "
        "4. Dealer has elevated fraud rate"
    )
    adjudication = Adjudication(
        **payload(overall_risk_decision="HIGH RISK", top_risk_factors=factors)
    )
    items = parse_top_risk_factors(adjudication.top_risk_factors)
    assert len(items) == 4
    assert all(re.match(r"^\d+\. ", item) for item in items)


# ===========================================================================
# Round trip through model_validate_json
# ===========================================================================


def test_round_trip_through_model_validate_json():
    original = Adjudication(**medium_payload())
    raw = original.model_dump_json()
    assert raw.strip()[0] == "{" and raw.strip()[-1] == "}"  # MSTR-017
    reparsed = Adjudication.model_validate_json(raw)
    assert reparsed == original
    assert list(json.loads(raw)) == SPEC_ORDER


def test_model_validate_json_on_a_spec_shaped_payload():
    """A hand-written JSON document in the customer's own key order and shape."""
    raw = json.dumps(
        {
            "master_summary": (
                "Income overstatement is the only unresolved concern; identity, "
                "dealer and network dimensions are explainable."
            ),
            "overall_risk_decision": "MEDIUM RISK",
            "recommendation_decision": "REVIEW AND APPLY STIPULATIONS",
            "recommended_stipulations": "Proof of income; Bank statement verification",
            "top_risk_factors": (
                "1. Stated income exceeds verified by 31 percent; "
                "2. Three distinct income amounts for one SSN; "
                "3. Self-employment explains variance but not magnitude"
            ),
            "identity_summary": "SSN maps to one identity combination.",
            "identity_flag": 0,
            "dealer_summary": "Consortium score is portfolio context, not fraud evidence.",
            "dealer_flag": 0,
            "straw_summary": "No co-borrower; straw risk is minimal.",
            "straw_flag": 0,
            "employment_summary": "Single-member LLC; not a shell-employer pattern.",
            "employment_flag": 0,
            "income_summary": "Stated income exceeds verified by 31 percent.",
            "income_flag": 1,
            "synthetic_summary": "File is 900 days old; no manufactured-credit pattern.",
            "synthetic_flag": 0,
            "bustout_summary": "One application in 7 days; no velocity concern.",
            "bustout_flag": 0,
            "rings_summary": "No shared-identifier overlap.",
            "rings_flag": 0,
        }
    )
    adjudication = Adjudication.model_validate_json(raw)
    assert list(adjudication.model_dump()) == SPEC_ORDER
    assert adjudication.income_flag == 1
    assert sum(getattr(adjudication, f) for f in FLAG_FIELDS) == 1


def test_model_validate_json_rejects_a_null_valued_document():
    raw = json.dumps(payload(recommended_stipulations=None))
    with pytest.raises(ValidationError):
        Adjudication.model_validate_json(raw)


# ===========================================================================
# MSTR-035: income alone must not DECLINE (the contract permits it; the fixture
# expectation is what encodes the rule, so both are asserted)
# ===========================================================================


def test_income_only_flag_with_decline_is_not_produced_by_the_offline_path():
    """MSTR-035: "Income deviation alone should generally not justify DECLINE"."""
    for app_id, expectation in OFFLINE_EXPECTATIONS.items():
        if set(expectation.flags) == {"income"}:
            assert expectation.recommendation_decision != "DECLINE", app_id


def test_app_1003_is_the_income_only_case_and_does_not_decline():
    expectation = OFFLINE_EXPECTATIONS["APP-1003"]
    assert set(expectation.flags) == {"income"}
    assert expectation.recommendation_decision == STIPULATIONS_RECOMMENDATION


# ===========================================================================
# A13: the enum conflict is documented, not silently resolved
# ===========================================================================


def test_three_distinct_risk_vocabularies_are_recorded():
    assert MASTER_RISK_VALUES == ("LOW RISK", "MEDIUM RISK", "HIGH RISK")
    assert PDF_RISK_VALUES == ("LOW RISK", "POSSIBLE RISK", "HIGH RISK")
    assert SPECIALIST_RISK_VALUES == ("LOW RISK", "POSSIBLE RISK", "HIGH RISK")
    assert "MEDIUM RISK" not in PDF_RISK_VALUES
    assert "POSSIBLE RISK" not in MASTER_RISK_VALUES


def test_the_pdf_recommendation_set_has_four_values_and_the_prompt_has_three():
    assert len(PDF_RECOMMENDATION_VALUES) == 4
    assert len(RECOMMENDATION_VALUES) == 3
    assert "MANUAL REVIEW" in PDF_RECOMMENDATION_VALUES
    assert "MANUAL REVIEW" not in RECOMMENDATION_VALUES


def test_the_implemented_enums_are_the_prompt_files():
    """The prompt is the executable artifact, so it is what ships."""
    assert ENUM_CONFLICT["implemented"]["artifact"] == "docs/master_agent_prompt.txt"
    assert ENUM_CONFLICT["implemented"]["overall_risk_decision"] == list(MASTER_RISK_VALUES)
    assert ENUM_CONFLICT["implemented"]["recommendation_decision"] == list(RECOMMENDATION_VALUES)
    assert "5.1" in ENUM_CONFLICT["not_implemented"]["artifact"]


def test_pdf_mid_band_maps_to_the_prompts_mid_band():
    assert map_pdf_enum("overall_risk_decision", "POSSIBLE RISK") == "MEDIUM RISK"
    assert map_pdf_enum("overall_risk_decision", "LOW RISK") == "LOW RISK"
    assert map_pdf_enum("overall_risk_decision", "HIGH RISK") == "HIGH RISK"


def test_pdf_stipulations_recommendation_maps_to_the_prompts_wording():
    assert (
        map_pdf_enum("recommendation_decision", "APPROVE WITH STIPULATIONS")
        == STIPULATIONS_RECOMMENDATION
    )


def test_manual_review_refuses_to_map():
    """It exists only in the PDF and is not a rename of any prompt value."""
    with pytest.raises(EnumConflictError) as excinfo:
        map_pdf_enum("recommendation_decision", "MANUAL REVIEW")
    assert "customer decision" in str(excinfo.value)


def test_the_enum_map_never_produces_a_value_outside_the_implemented_enums():
    for value in PDF_TO_PROMPT_ENUM_MAP["overall_risk_decision"].values():
        assert value in MASTER_RISK_VALUES
    for value in PDF_TO_PROMPT_ENUM_MAP["recommendation_decision"].values():
        assert value in RECOMMENDATION_VALUES


# ===========================================================================
# Fan-out: shape, degradation, no fabricated telemetry
# ===========================================================================


def test_fan_out_returns_all_eight_domains_in_the_customers_order():
    results = asyncio.run(fan_out("RENDERED DOMAIN VIEW", mock=True))
    assert list(results) == list(DOMAINS)
    assert all(isinstance(r, SpecialistResult) for r in results.values())


def test_fan_out_mock_results_carry_no_token_counts_and_no_cost():
    """A mock run must report missing measurements as missing, never as plausible
    numbers. The old port's NOMINAL_TOKENS is exactly what this forbids."""
    results = asyncio.run(fan_out("VIEW", mock=True))
    for domain, result in results.items():
        assert result.usage is None, domain
        assert result.cost_usd is None, domain
        assert result.latency_ms is None, domain
        assert result.source == "mock"


def test_fan_out_uses_the_customers_agent_names():
    results = asyncio.run(fan_out("VIEW", mock=True))
    for domain, result in results.items():
        assert result.agent_name == AGENT_NAMES[domain]


def test_fan_out_can_run_a_subset_but_keeps_the_prompt_order():
    results = asyncio.run(fan_out("VIEW", domains=["rings", "identity"], mock=True))
    assert list(results) == ["identity", "rings"]


def test_fan_out_rejects_an_unknown_domain():
    from agents.fanout import FanOutError

    with pytest.raises(FanOutError) as excinfo:
        asyncio.run(fan_out("VIEW", domains=["vehicle"], mock=True))
    assert "vehicle" in str(excinfo.value)


def test_mock_analyses_use_the_customers_title_contract():
    """Six agents define an exact title; bustout and rings define none, so none
    is invented for them."""
    for domain in DOMAINS:
        text = mock_analysis(domain)
        title = ANALYSIS_TITLES[domain]
        if title is None:
            assert domain in ("bustout", "rings")
            assert not text.startswith("Identity")
            assert "Risk Analysis\n" not in text
        else:
            assert text.startswith(f"{title}\n")


def test_titles_match_the_verbatim_prompt_output_blocks():
    for domain, title in ANALYSIS_TITLES.items():
        prompt = SPECIALIST_PROMPTS[domain]
        if title is None:
            assert "- Title:" not in prompt, domain
        else:
            assert f"- Title: {title}" in prompt, domain


def test_mock_analyses_never_use_the_invented_risk_findings_format():
    """The old port forced every agent into "RISK: <band>\\nFINDINGS: ...\"."""
    for domain in DOMAINS:
        text = mock_analysis(domain)
        assert not re.search(r"^\s*RISK:", text, re.M), domain
        assert "FINDINGS:" not in text, domain
        assert "ROLE:" not in text, domain


def test_mock_analyses_carry_no_emoji_and_no_alert_numbers():
    for domain in DOMAINS:
        text = mock_analysis(domain)
        assert not contains_emoji(text), domain
        assert not re.search(r"[Aa]lert\s*#?\d+", text), domain


def test_fan_out_streaming_emits_start_then_complete_then_summary():
    async def collect():
        return [event async for event in fan_out_streaming("VIEW", mock=True)]

    events = asyncio.run(collect())
    kinds = [event["event"] for event in events]
    assert kinds[:8] == ["agent_started"] * 8
    assert kinds[8:16] == ["agent_completed"] * 8
    assert kinds[-1] == "fanout_completed"
    assert {event["domain"] for event in events[:8]} == set(DOMAINS)


def test_fan_out_streaming_summary_reports_no_cost_when_nothing_was_measured():
    async def last():
        events = [event async for event in fan_out_streaming("VIEW", mock=True)]
        return events[-1]

    summary = asyncio.run(last())
    assert summary["agent_cost_usd"] is None
    assert summary["failed"] == []
    assert sorted(summary["succeeded"]) == sorted(DOMAINS)
    assert summary["wall_ms"] >= 0


def test_a_failed_specialist_is_recorded_not_raised():
    """Graceful degradation: seven of eight must still adjudicate."""
    results = asyncio.run(fan_out("VIEW", mock=True))
    results["rings"].analysis = ""
    results["rings"].error = "ThrottlingException: rate exceeded"
    assert results["rings"].ok is False
    assert results["identity"].ok is True
    assert degraded_domains(results) == ["rings"]
    event = results["rings"].to_event()
    assert event["event"] == "agent_failed"
    assert "error" in event


def test_render_domain_input_refuses_an_unprojected_payload(monkeypatch):
    """An agent must never see another domain's signals: if the projection is
    unavailable and the payload is not already text, this raises."""
    import agents.fanout as fanout_module

    monkeypatch.setitem(sys.modules, "signal_layer.schema", None)
    with pytest.raises(fanout_module.FanOutError) as excinfo:
        fanout_module.render_domain_input({"signals": {}}, "identity")
    assert "another domain's signals" in str(excinfo.value)
    # Already-rendered text still passes through, so the guard is not a blanket ban.
    assert fanout_module.render_domain_input("TEXT", "identity") == "TEXT"


def test_every_specialist_receives_only_its_own_domain_view():
    """SIGBOUND-01/02. The renderer is called once per domain with that domain's
    name and nothing else; no agent is handed the full signal set."""
    seen: list[tuple[object, str]] = []

    def renderer(payload, domain):
        seen.append((payload, domain))
        return f"VIEW OF {domain}"

    sentinel = object()
    asyncio.run(fan_out(sentinel, mock=True, renderer=renderer))
    assert [domain for _, domain in seen] == list(DOMAINS)
    assert all(obj is sentinel for obj, _ in seen)


# ===========================================================================
# Fan-out on the live code path, with a stubbed Agent.
#
# These exercise the real (non-mock) branch of _run_specialist - asyncio.gather,
# the semaphore, usage/latency extraction and costing - without any Bedrock call.
# ===========================================================================


class _FakeMetrics:
    def __init__(self, usage, latency_ms):
        self.accumulated_usage = usage
        self.accumulated_metrics = {} if latency_ms is None else {"latencyMs": latency_ms}


class _FakeResult:
    def __init__(self, text, usage=None, latency_ms=None, stop_reason="end_turn"):
        self.message = {"role": "assistant", "content": [{"text": text}]}
        self.metrics = _FakeMetrics(usage, latency_ms)
        self.stop_reason = stop_reason
        self.structured_output = None


class _FakeAgent:
    """Records concurrency and returns a canned result after a real await."""

    live = 0
    peak = 0

    def __init__(self, *, text="analysis", usage=None, latency_ms=None, raises=None):
        self._text = text
        self._usage = usage
        self._latency_ms = latency_ms
        self._raises = raises

    async def invoke_async(self, prompt, **kwargs):
        type(self).live += 1
        type(self).peak = max(type(self).peak, type(self).live)
        try:
            await asyncio.sleep(0.02)
            if self._raises is not None:
                raise self._raises
            return _FakeResult(self._text, self._usage, self._latency_ms)
        finally:
            type(self).live -= 1


REAL_USAGE = {
    "inputTokens": 400,
    "outputTokens": 120,
    "totalTokens": 520,
    "cacheReadInputTokens": 900,
    "cacheWriteInputTokens": 0,
}


def test_fan_out_runs_the_eight_specialists_concurrently(monkeypatch):
    """asyncio.gather, not a serial loop: eight 20ms agents must overlap."""
    import agents.fanout as fanout_module

    _FakeAgent.peak = 0
    monkeypatch.setattr(
        fanout_module, "_build_agent", lambda domain, model_id: _FakeAgent(text=f"{domain} prose")
    )
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    results = asyncio.run(fan_out("VIEW", mock=False))
    assert _FakeAgent.peak == 8, f"peak concurrency was {_FakeAgent.peak}, expected 8"
    assert all(result.ok for result in results.values())
    assert results["identity"].analysis == "identity prose"


def test_the_semaphore_actually_bounds_concurrency(monkeypatch):
    import agents.fanout as fanout_module

    _FakeAgent.peak = 0
    monkeypatch.setattr(fanout_module, "_build_agent", lambda domain, model_id: _FakeAgent())
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    asyncio.run(fan_out("VIEW", mock=False, max_concurrency=3))
    assert _FakeAgent.peak == 3


def test_measured_usage_and_cost_come_from_the_result(monkeypatch):
    """Cache keys via .get(), and cost from agents.pricing on those real tokens."""
    import agents.fanout as fanout_module
    from agents.pricing import cost_usd

    monkeypatch.setattr(
        fanout_module,
        "_build_agent",
        lambda domain, model_id: _FakeAgent(usage=dict(REAL_USAGE), latency_ms=1848.0),
    )
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    result = asyncio.run(fan_out("VIEW", domains=["identity"], mock=False))["identity"]
    assert result.usage == REAL_USAGE
    assert result.latency_ms == 1848.0
    assert result.wall_ms > 0
    assert result.cost_usd == pytest.approx(cost_usd(result.model_id, REAL_USAGE))
    assert result.source == "bedrock"


def test_usage_without_the_optional_cache_keys_defaults_them_to_zero(monkeypatch):
    """cacheRead/cacheWrite are OPTIONAL in Bedrock's Usage TypedDict."""
    import agents.fanout as fanout_module

    monkeypatch.setattr(
        fanout_module,
        "_build_agent",
        lambda domain, model_id: _FakeAgent(
            usage={"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}
        ),
    )
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    result = asyncio.run(fan_out("VIEW", domains=["income"], mock=False))["income"]
    assert result.usage["cacheReadInputTokens"] == 0
    assert result.usage["cacheWriteInputTokens"] == 0
    assert result.cost_usd is not None


def test_seven_of_eight_still_returns_when_one_specialist_throws(monkeypatch):
    """Graceful degradation is the whole point of return_exceptions=True."""
    import agents.fanout as fanout_module

    def build(domain, model_id):
        if domain == "rings":
            return _FakeAgent(raises=RuntimeError("ThrottlingException: rate exceeded"))
        return _FakeAgent(text=f"{domain} prose", usage=dict(REAL_USAGE))

    monkeypatch.setattr(fanout_module, "_build_agent", build)
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    results = asyncio.run(fan_out("VIEW", mock=False))
    assert len(results) == 8
    assert results["rings"].ok is False
    assert "ThrottlingException" in results["rings"].error
    assert results["rings"].usage is None
    assert results["rings"].cost_usd is None
    assert sum(1 for r in results.values() if r.ok) == 7
    # And the synthesis path still adjudicates on the remaining seven.
    synthesis = synthesize_offline(results, "APP-1004")
    assert synthesis.degraded_domains == ["rings"]


def test_streaming_reports_the_failure_as_a_frame_not_an_exception(monkeypatch):
    """AgentCore swallows a mid-stream exception into a terminal error frame AFTER
    HTTP 200, so a per-agent failure must be an ordinary frame."""
    import agents.fanout as fanout_module

    def build(domain, model_id):
        if domain == "dealer":
            return _FakeAgent(raises=RuntimeError("boom"))
        return _FakeAgent(usage=dict(REAL_USAGE))

    monkeypatch.setattr(fanout_module, "_build_agent", build)
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    async def collect():
        return [event async for event in fan_out_streaming("VIEW", mock=False)]

    events = asyncio.run(collect())
    failed = [event for event in events if event["event"] == "agent_failed"]
    assert len(failed) == 1
    assert failed[0]["domain"] == "dealer"
    assert "boom" in failed[0]["error"]
    summary = events[-1]
    assert summary["failed"] == ["dealer"]
    assert len(summary["succeeded"]) == 7
    # One dimension has no measured cost, so the total is None rather than a
    # seven-eighths sum presented as a total.
    assert summary["agent_cost_usd"] is None


def test_streaming_totals_cost_only_when_every_dimension_was_measured(monkeypatch):
    import agents.fanout as fanout_module

    monkeypatch.setattr(
        fanout_module,
        "_build_agent",
        lambda domain, model_id: _FakeAgent(usage=dict(REAL_USAGE), latency_ms=1000.0),
    )
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    async def last():
        events = [event async for event in fan_out_streaming("VIEW", mock=False)]
        return events[-1]

    summary = asyncio.run(last())
    assert summary["agent_cost_usd"] is not None
    assert summary["agent_cost_usd"] > 0
    assert summary["slowest_domain"] in DOMAINS


def test_streaming_yields_each_agent_as_it_finishes_not_batched_at_the_end(monkeypatch):
    """The SSE entrypoint's value is incremental delivery. Eight staggered agents
    must arrive in completion order, the first long before the last."""
    import agents.fanout as fanout_module

    delays = {domain: 0.02 * (index + 1) for index, domain in enumerate(DOMAINS)}

    def build(domain, model_id):
        return _FakeAgentWithDelay(delays[domain], f"{domain} prose")

    monkeypatch.setattr(fanout_module, "_build_agent", build)
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    async def collect():
        import time as _time

        start = _time.perf_counter()
        arrivals = []
        async for event in fan_out_streaming("VIEW", mock=False):
            if event["event"] == "agent_completed":
                arrivals.append((event["domain"], (_time.perf_counter() - start) * 1000))
        return arrivals

    arrivals = asyncio.run(collect())
    assert [domain for domain, _ in arrivals] == list(DOMAINS)  # slowest is last
    assert arrivals[0][1] < arrivals[-1][1] / 2, "frames arrived batched, not streamed"


class _FakeAgentWithDelay:
    def __init__(self, delay, text):
        self._delay = delay
        self._text = text

    async def invoke_async(self, prompt, **kwargs):
        await asyncio.sleep(self._delay)
        return _FakeResult(self._text, dict(REAL_USAGE), self._delay * 1000)


def test_a_consumer_that_stops_early_does_not_leak_specialist_tasks(monkeypatch):
    """A dropped SSE connection must not leave eight in-flight Bedrock calls."""
    import agents.fanout as fanout_module

    monkeypatch.setattr(
        fanout_module,
        "_build_agent",
        lambda domain, model_id: _FakeAgentWithDelay(0.5, "prose"),
    )
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    async def stop_after_three():
        generator = fan_out_streaming("VIEW", mock=False)
        count = 0
        async for _ in generator:
            count += 1
            if count == 3:
                break
        await generator.aclose()
        await asyncio.sleep(0.05)
        return [
            task
            for task in asyncio.all_tasks()
            if task.get_name().startswith("specialist-") and not task.done()
        ]

    assert asyncio.run(stop_after_three()) == []


def test_a_fresh_agent_is_built_for_every_specialist_on_every_invocation(monkeypatch):
    """A shared Strands Agent raises ConcurrencyException under stream_async, and a
    reused one reports cumulative token usage."""
    import agents.fanout as fanout_module

    built: list[object] = []

    def build(domain, model_id):
        agent = _FakeAgent()
        built.append(agent)
        return agent

    monkeypatch.setattr(fanout_module, "_build_agent", build)
    monkeypatch.setattr(fanout_module, "widen_default_executor", lambda *a, **k: None)

    asyncio.run(fan_out("VIEW", mock=False))
    asyncio.run(fan_out("VIEW", mock=False))
    assert len(built) == 16
    assert len({id(agent) for agent in built}) == 16


def test_the_specialist_agent_is_configured_the_way_the_platform_requires():
    """Constructed offline (no AWS call): tools empty, printing handler off, null
    conversation manager, the customer's agent name, and a cachePoint block."""
    from strands.agent import NullConversationManager

    from agents.fanout import _build_agent
    from agents.models import model_for

    agent = _build_agent("identity", model_for("identity"))
    assert agent.tool_names == []
    assert agent.callback_handler is not None  # the null handler, not the printer
    assert "Printing" not in type(agent.callback_handler).__name__
    assert isinstance(agent.conversation_manager, NullConversationManager)
    assert agent.name == AGENT_NAMES["identity"]
    content = agent._system_prompt_content
    assert content[0]["text"] == SPECIALIST_PROMPTS["identity"]
    assert content[-1] == {"cachePoint": {"type": "default"}}


def test_the_cache_prefix_report_is_carried_rather_than_assumed():
    """Below a model's minimum cacheable prefix a cachePoint silently does nothing.
    The per-result report records which, so no cost claim can assume a hit."""
    results = asyncio.run(fan_out("VIEW", mock=True))
    for domain, result in results.items():
        report = result.prompt_cache
        assert isinstance(report["will_cache"], bool), domain
        assert report["min_cacheable_tokens"] is not None, domain
        assert report["prompt_tokens"] > 0, domain


# ===========================================================================
# Synthesis: verbatim placeholder substitution
# ===========================================================================


def test_synthesis_prompt_fills_the_masters_own_eight_placeholders():
    analyses = {domain: f"<<{domain} analysis body>>" for domain in DOMAINS}
    prompt = build_synthesis_prompt(analyses)
    for domain in DOMAINS:
        assert f"<<{domain} analysis body>>" in prompt
    for placeholder in MASTER_ANALYSIS_PLACEHOLDERS:
        assert "{" + placeholder + "}" not in prompt


def test_synthesis_prompt_preserves_the_bare_uppercase_headings():
    """MSTR-004: her headings are ``IDENTITY:``, not ``IDENTITY ANALYSIS:``."""
    prompt = build_synthesis_prompt({domain: "x" for domain in DOMAINS})
    for domain in DOMAINS:
        assert f"\n{domain.upper()}:\n" in prompt
        assert f"{domain.upper()} ANALYSIS:" not in prompt


def test_synthesis_prompt_preserves_the_forty_hyphen_rule_lines():
    """MSTR-003: the rule lines around SPECIALIZED FRAUD ANALYSES are hers."""
    prompt = build_synthesis_prompt({domain: "x" for domain in DOMAINS})
    assert prompt.count("-" * 40) == MASTER_SYNTHESIS_PROMPT.count("-" * 40)
    assert "SPECIALIZED FRAUD ANALYSES" in prompt


def test_synthesis_prompt_keeps_the_persona_sentence_first():
    """MSTR-001."""
    prompt = build_synthesis_prompt({domain: "x" for domain in DOMAINS})
    assert prompt.startswith("You are a senior forensic underwriting analyst.")


def test_synthesis_prompt_leaves_the_literal_json_braces_untouched():
    """``str.format`` would otherwise read ``{"master_summary": ...}`` as a field."""
    prompt = build_synthesis_prompt({domain: "x" for domain in DOMAINS})
    assert '"master_summary": "..."' in prompt
    assert '"rings_flag": 0' in prompt
    assert '"overall_risk_decision": "LOW RISK | MEDIUM RISK | HIGH RISK"' in prompt


def test_synthesis_prompt_equals_a_naive_placeholder_replacement():
    """The format-based substitution must not perturb one other byte of her text."""
    analyses = {domain: f"BODY-{domain}" for domain in DOMAINS}
    expected = MASTER_SYNTHESIS_PROMPT
    for domain in DOMAINS:
        expected = expected.replace(f"{{{domain}_analysis}}", f"BODY-{domain}")
    assert build_synthesis_prompt(analyses) == expected


def test_synthesis_prompt_names_the_missing_specialist_instead_of_leaving_a_gap():
    analyses = {domain: "x" for domain in DOMAINS}
    analyses["rings"] = ""
    prompt = build_synthesis_prompt(analyses)
    assert "\nRINGS:\n" in prompt
    assert AGENT_NAMES["rings"] in prompt
    assert "ANALYSIS UNAVAILABLE" in prompt
    assert MISSING_ANALYSIS_NOTICE.format(agent_name=AGENT_NAMES["rings"]) in prompt


def test_synthesis_prompt_accepts_fan_out_results_directly():
    results = asyncio.run(fan_out("VIEW", mock=True))
    prompt = build_synthesis_prompt(results)
    assert "Identity Fraud Risk Analysis" in prompt
    assert normalize_analyses(results)["identity"] == results["identity"].analysis


def test_synthesis_prompt_rejects_an_unknown_domain():
    analyses = {domain: "x" for domain in DOMAINS}
    analyses["vehicle"] = "x"
    with pytest.raises(SynthesisContractError):
        build_synthesis_prompt(analyses)


def test_the_only_added_text_states_no_rule():
    """The user turn this port adds must not smuggle in a band or a format."""
    for forbidden in ("LOW RISK", "MEDIUM", "HIGH", "JSON", "flag", "1200", "emoji"):
        assert forbidden not in SYNTHESIS_USER_MESSAGE


# ===========================================================================
# Offline synthesis: fixture-pinned, never vote-counted
# ===========================================================================


@pytest.mark.parametrize("app_id", sorted(OFFLINE_EXPECTATIONS))
def test_offline_synthesis_satisfies_every_validator(app_id):
    results = asyncio.run(fan_out("VIEW", mock=True))
    synthesis = synthesize_offline(results, app_id)
    dumped = synthesis.adjudication.model_dump()
    assert list(dumped) == SPEC_ORDER
    # Re-validating the dump proves the object is not merely constructed but valid.
    assert Adjudication(**dumped) == synthesis.adjudication


@pytest.mark.parametrize("app_id", sorted(OFFLINE_EXPECTATIONS))
def test_offline_synthesis_matches_its_pinned_expectation(app_id):
    results = asyncio.run(fan_out("VIEW", mock=True))
    adjudication = synthesize_offline(results, app_id).adjudication
    expectation = OFFLINE_EXPECTATIONS[app_id]
    assert adjudication.overall_risk_decision == expectation.overall_risk_decision
    assert adjudication.recommendation_decision == expectation.recommendation_decision
    flagged = {d for d in DOMAINS if getattr(adjudication, f"{d}_flag") == 1}
    assert flagged == set(expectation.flags)


def test_offline_synthesis_carries_no_token_counts_and_no_cost():
    results = asyncio.run(fan_out("VIEW", mock=True))
    synthesis = synthesize_offline(results, "APP-1001")
    assert synthesis.usage is None
    assert synthesis.cost_usd is None
    assert synthesis.latency_ms is None
    assert synthesis.source == "mock"
    assert synthesis.metrics.accumulated_usage is None
    assert synthesis.metrics.accumulated_metrics == {}


def test_offline_synthesis_refuses_an_unpinned_application():
    """The customer's MSTR-022/023/031 rules forbid deciding by volume, so there
    is no fallback scoring path to fall back to."""
    results = asyncio.run(fan_out("VIEW", mock=True))
    with pytest.raises(UnpinnedApplicationError) as excinfo:
        synthesize_offline(results, "APP-9999")
    assert "MSTR-022" in str(excinfo.value)


def test_offline_synthesis_does_not_count_findings():
    """Identical analyses for two applications must still produce their own pinned
    verdicts - proof the outcome is not derived from the inputs' volume."""
    results = asyncio.run(fan_out("VIEW", mock=True))
    low = synthesize_offline(results, "APP-1001").adjudication
    high = synthesize_offline(results, "APP-1004").adjudication
    assert low.overall_risk_decision == "LOW RISK"
    assert high.overall_risk_decision == "HIGH RISK"
    assert low.recommendation_decision == "APPROVE"
    assert high.recommendation_decision == "DECLINE"


def test_an_unassessed_dimension_is_never_flagged():
    """MSTR-039: a flag is 1 only on corroborated evidence, and a specialist that
    never answered produced none."""
    results = asyncio.run(fan_out("VIEW", mock=True))
    results["rings"].analysis = ""
    results["rings"].error = "ThrottlingException"
    synthesis = synthesize_offline(results, "APP-1004")
    assert "rings" in OFFLINE_EXPECTATIONS["APP-1004"].flags
    assert synthesis.adjudication.rings_flag == 0
    assert synthesis.degraded_domains == ["rings"]
    assert "rings" in synthesis.adjudication.master_summary


def test_synthesize_dispatches_to_the_offline_path_when_mocked():
    results = asyncio.run(fan_out("VIEW", mock=True))
    synthesis = asyncio.run(synthesize(results, application_id="APP-1005", mock=True))
    assert synthesis.source == "mock"
    assert synthesis.adjudication.overall_risk_decision == "MEDIUM RISK"
    assert synthesis.repair_attempts == 0


def test_synthesis_result_event_carries_the_adjudication_and_no_invented_numbers():
    results = asyncio.run(fan_out("VIEW", mock=True))
    event = asyncio.run(
        synthesize(results, application_id="APP-1001", mock=True)
    ).to_event()
    assert event["event"] == "synthesis_completed"
    assert list(event["adjudication"]) == SPEC_ORDER
    assert event["usage"] is None
    assert event["cost_usd"] is None
    assert event["application_id"] == "APP-1001"


def test_every_pinned_application_exists_in_the_fixture_dataset():
    from data.applications import list_application_ids

    known = set(list_application_ids())
    assert set(pinned_application_ids()) <= known


def test_pinned_expectations_are_internally_consistent():
    """Each row must be constructible, which is the same as saying each row obeys
    the customer's cross-field rules."""
    for app_id, expectation in OFFLINE_EXPECTATIONS.items():
        if expectation.recommendation_decision == STIPULATIONS_RECOMMENDATION:
            assert expectation.overall_risk_decision == "MEDIUM RISK", app_id
            assert expectation.recommended_stipulations, app_id
        else:
            assert expectation.recommended_stipulations == "", app_id
        if expectation.overall_risk_decision == "LOW RISK":
            assert expectation.top_risk_factors == "", app_id
        else:
            items = parse_top_risk_factors(expectation.top_risk_factors)
            assert 3 <= len(items) <= 4, app_id
        assert expectation.why, app_id


# ===========================================================================
# Live synthesis, with a stubbed master Agent.
#
# Structured output, the stop_reason assertion, the bounded repair retry, and the
# hard requirement that nothing unvalidated ever gets returned.
# ===========================================================================


class _FakeSynthResult:
    def __init__(self, structured_output, stop_reason="tool_use", usage=None, latency_ms=None):
        self.structured_output = structured_output
        self.stop_reason = stop_reason
        self.message = {"role": "assistant", "content": [{"text": "{}"}]}
        self.metrics = _FakeMetrics(usage, latency_ms)


class _FakeMasterAgent:
    """Returns a scripted sequence of results, one per invocation."""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[str] = []
        self.structured_output_models: list[object] = []

    async def invoke_async(self, prompt, **kwargs):
        self.calls.append(prompt)
        self.structured_output_models.append(kwargs.get("structured_output_model"))
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _install_master(monkeypatch, script):
    """Install a fake master on the CONVERSE path.

    The default synthesizer is now GPT-5.6 Luna on the bedrock-mantle endpoint, which
    does not go through ``_build_master_agent`` at all. These tests exercise the Strands
    structured-output contract (forced tool, ``stop_reason == 'tool_use'``, the bounded
    repair loop), so they pin the synthesizer to a Claude id. The mantle path has its own
    coverage in tests/test_mantle.py.
    """
    import agents.synthesize as synth_module

    monkeypatch.setattr(
        synth_module, "SYNTHESIZER_MODEL", "global.anthropic.claude-sonnet-5", raising=False
    )
    agent = _FakeMasterAgent(script)
    monkeypatch.setattr(synth_module, "_build_master_agent", lambda prompt: agent)
    return agent


SYNTH_USAGE = {
    "inputTokens": 2100,
    "outputTokens": 640,
    "totalTokens": 2740,
    "cacheReadInputTokens": 0,
    "cacheWriteInputTokens": 0,
}


def test_live_synthesis_requests_structured_output_with_the_contract_model(monkeypatch):
    valid = Adjudication(**payload())
    agent = _install_master(
        monkeypatch, [_FakeSynthResult(valid, usage=dict(SYNTH_USAGE), latency_ms=3200.0)]
    )
    results = asyncio.run(fan_out("VIEW", mock=True))

    synthesis = asyncio.run(synthesize(results, application_id="APP-1001", mock=False))
    assert agent.structured_output_models == [Adjudication]
    assert synthesis.adjudication is valid
    assert synthesis.stop_reason == "tool_use"
    assert synthesis.repair_attempts == 0
    assert synthesis.usage == SYNTH_USAGE
    assert synthesis.latency_ms == 3200.0
    assert synthesis.cost_usd is not None
    assert synthesis.source == "bedrock"


def test_live_synthesis_rejects_a_result_that_did_not_stop_on_tool_use(monkeypatch):
    """stop_reason != 'tool_use' means the adjudication may be truncated."""
    valid = Adjudication(**payload())
    _install_master(
        monkeypatch,
        [
            _FakeSynthResult(valid, stop_reason="max_tokens"),
            _FakeSynthResult(valid, stop_reason="max_tokens"),
        ],
    )
    results = asyncio.run(fan_out("VIEW", mock=True))
    with pytest.raises(SynthesisContractError) as excinfo:
        asyncio.run(synthesize(results, application_id="APP-1001", mock=False))
    assert "tool_use" in str(excinfo.value)


def test_live_synthesis_retries_once_and_succeeds(monkeypatch):
    valid = Adjudication(**medium_payload())
    agent = _install_master(
        monkeypatch,
        [
            _FakeSynthResult(None, stop_reason="end_turn", usage=dict(SYNTH_USAGE)),
            _FakeSynthResult(valid, usage=dict(SYNTH_USAGE)),
        ],
    )
    results = asyncio.run(fan_out("VIEW", mock=True))

    synthesis = asyncio.run(synthesize(results, application_id="APP-1003", mock=False))
    assert synthesis.repair_attempts == 1
    assert synthesis.adjudication is valid
    assert len(agent.calls) == 2
    # The repair turn tells the model what failed instead of re-asking blindly.
    assert "did not satisfy the output contract" in agent.calls[1]
    assert "21" in agent.calls[1]
    # Both invocations were really billed, so both are counted.
    assert synthesis.usage["inputTokens"] == 2 * SYNTH_USAGE["inputTokens"]


def test_the_repair_retry_is_bounded_and_then_raises(monkeypatch):
    """The old port's bare ``except: pass`` emitted ``{"raw": ...}``. There is no
    such path here."""
    _install_master(
        monkeypatch,
        [_FakeSynthResult(None, stop_reason="end_turn") for _ in range(4)],
    )
    results = asyncio.run(fan_out("VIEW", mock=True))
    with pytest.raises(SynthesisContractError) as excinfo:
        asyncio.run(synthesize(results, application_id="APP-1001", mock=False))
    message = str(excinfo.value)
    assert "attempt 1" in message and "attempt 2" in message
    assert "no structured output" in message


def test_live_synthesis_never_returns_an_unvalidated_object(monkeypatch):
    """A non-Adjudication in structured_output must not be handed back."""

    class NotAnAdjudication(BaseModel):
        master_summary: str = "wrong model"

    _install_master(
        monkeypatch,
        [
            _FakeSynthResult(NotAnAdjudication()),
            _FakeSynthResult(NotAnAdjudication()),
        ],
    )
    results = asyncio.run(fan_out("VIEW", mock=True))
    with pytest.raises(SynthesisContractError) as excinfo:
        asyncio.run(synthesize(results, application_id="APP-1001", mock=False))
    assert "expected Adjudication" in str(excinfo.value)


def test_a_transport_failure_is_retried_then_surfaced(monkeypatch):
    _install_master(
        monkeypatch,
        [
            RuntimeError("ThrottlingException"),
            RuntimeError("ThrottlingException"),
        ],
    )
    results = asyncio.run(fan_out("VIEW", mock=True))
    with pytest.raises(SynthesisContractError) as excinfo:
        asyncio.run(synthesize(results, application_id="APP-1001", mock=False))
    assert "ThrottlingException" in str(excinfo.value)


def test_a_transport_failure_followed_by_success_returns(monkeypatch):
    valid = Adjudication(**payload())
    _install_master(monkeypatch, [RuntimeError("ThrottlingException"), _FakeSynthResult(valid)])
    results = asyncio.run(fan_out("VIEW", mock=True))
    synthesis = asyncio.run(synthesize(results, application_id="APP-1001", mock=False))
    assert synthesis.adjudication is valid
    # No usage was recorded for the throw, so nothing is invented for it.
    assert synthesis.usage is None
    assert synthesis.cost_usd is None


def test_live_synthesis_records_the_degraded_dimensions(monkeypatch):
    valid = Adjudication(**payload())
    _install_master(monkeypatch, [_FakeSynthResult(valid)])
    results = asyncio.run(fan_out("VIEW", mock=True))
    results["bustout"].analysis = ""
    results["bustout"].error = "ThrottlingException"
    synthesis = asyncio.run(synthesize(results, application_id="APP-1005", mock=False))
    assert synthesis.degraded_domains == ["bustout"]


def test_the_repair_attempt_builds_a_fresh_master_agent(monkeypatch):
    """Reusing the instance would report cumulative token usage and, on the real
    Agent, risk ConcurrencyException."""
    import agents.synthesize as synth_module

    built: list[object] = []
    script = [
        _FakeSynthResult(None, stop_reason="end_turn"),
        _FakeSynthResult(Adjudication(**payload())),
    ]

    def build(prompt):
        agent = _FakeMasterAgent([script[len(built)]])
        built.append(agent)
        return agent

    # Pin the Converse path: the default synthesizer is a mantle model that never

    # calls _build_master_agent (see _install_master).

    monkeypatch.setattr(

        synth_module, "SYNTHESIZER_MODEL", "global.anthropic.claude-sonnet-5", raising=False

    )

    monkeypatch.setattr(synth_module, "_build_master_agent", build)
    results = asyncio.run(fan_out("VIEW", mock=True))
    synthesis = asyncio.run(synthesize(results, application_id="APP-1001", mock=False))
    assert synthesis.repair_attempts == 1
    assert len(built) == 2
    assert built[0] is not built[1]


def test_the_synthesis_system_prompt_is_the_substituted_customer_prompt(monkeypatch):
    """The whole substituted prompt is the system prompt, so line 1 is her persona
    sentence and her 21-key JSON block is intact."""
    import agents.synthesize as synth_module

    captured: list[str] = []

    def build(prompt):
        captured.append(prompt)
        return _FakeMasterAgent([_FakeSynthResult(Adjudication(**payload()))])

    # Pin the Converse path: the default synthesizer is a mantle model that never

    # calls _build_master_agent (see _install_master).

    monkeypatch.setattr(

        synth_module, "SYNTHESIZER_MODEL", "global.anthropic.claude-sonnet-5", raising=False

    )

    monkeypatch.setattr(synth_module, "_build_master_agent", build)
    results = asyncio.run(fan_out("VIEW", mock=True))
    asyncio.run(synthesize(results, application_id="APP-1001", mock=False))

    prompt = captured[0]
    assert prompt.startswith("You are a senior forensic underwriting analyst.")
    assert "Return your output as STRICT JSON only. No extra text." in prompt
    assert '"master_summary": "..."' in prompt
    assert "Identity Fraud Risk Analysis" in prompt  # the fan-out's analysis landed
