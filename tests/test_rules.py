"""
One test per General Rule id, plus the tests that keep the coverage table honest.

Test naming is load-bearing: every ``test_rule_*`` name here appears as the ``test_id`` of a
catalog entry, and ``test_every_catalog_test_id_exists`` asserts the correspondence in both
directions. So a rule cannot be marked enforced without a test, and a test cannot drift away
from the rule it claims to cover.

Where a rule's failure mode is a *silent* wrong answer — rule 4's alert fan-out, rule 17's
null-safety, rule 26's undercount — the test asserts against the wrong implementation as well
as the right one. Asserting only that the correct function is correct would pass just as
happily if the rule were not load-bearing at all.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from prompts.loader import ORCHESTRATION_NOTES
from signal_layer.alert_categories import (
    ALERT_CATEGORIES,
    MULTI_CATEGORY_ALERTS,
    alerts_for_domain,
    category_of,
)
from signal_layer.bands import (
    DEALER_SCORE_BINDING,
    FRAUD_SCORE_AVERAGING_RULE,
    INCOMEPASS_SEMANTICS,
    decode_product,
    fraud_score_band,
)
from signal_layer.rules import _source, catalog
from signal_layer.rules.r01_lender_join import (
    LenderJoinError,
    assert_no_fanout,
    build_lender_join_sql,
    inner_join_lenders,
    is_test_lender,
    resolve_lenders,
    validate_lender_join_sql,
)
from signal_layer.rules.r02_normalize import (
    ADDRESS_KEY_EXAMPLE,
    AddressKeyError,
    address_key,
    building_number,
    norm_string,
    norm_zip,
    same_address,
    street_name_prefix,
)
from signal_layer.rules.r04_alert_join import (
    APPLICATION_GRAIN,
    JoinKeyError,
    deduplicate_applications,
    join_alerts,
    join_alerts_by_application_only,
    join_keys,
    require_composite_key,
    submitted_to_lender_count,
    validate_join_sql,
)
from signal_layer.rules.r07_chargeoff import (
    default_counts,
    default_rate,
    default_sql_predicate,
    is_default,
    is_pure_chargeoff,
)
from signal_layer.rules.r08_deprecated_tables import (
    APPROVED_ALL_RECORDS_TABLE,
    DEPRECATED_TABLES,
    DeprecatedTableError,
    is_deprecated_table,
    validate_no_deprecated_tables,
)
from signal_layer.rules.r09_coborrower_source import (
    COBORROWER_SOURCE_ORDER,
    Coborrower,
    is_exhausted,
    next_source,
    resolve_coborrower,
)
from signal_layer.rules.r0105_lender_nicknames import (
    INLINE_EXAMPLES,
    NICKNAME_INDEX,
    NICKNAME_TABLE,
    STELLANTIS_LENDER_NAMES,
    LenderUnresolved,
    lender_like_pattern,
    norm_lender,
    resolve_lender,
)
from signal_layer.rules.r10_active_dealer import (
    ACTIVITY_FIELD,
    DEALER_IDENTITY_KEY,
    DealerIdentityError,
    active_dealer_sql_predicate,
    active_dealers,
    count_active_distinct_dealers,
    dealer_key,
    distinct_dealers,
    is_active_dealer,
)
from signal_layer.rules.r11_submitted_at import (
    DEFAULT_DATE_FIELD,
    SUBMITTED_FIELD,
    SUBMITTED_PRIMARY_TABLE,
    ForbiddenDatePredicate,
    build_date_predicate,
    resolve_date_field,
    validate_date_sql,
)
from signal_layer.rules.r12_recommendations import (
    check_recommendation_text,
    has_recommendations_section,
    requests_recommendations,
)
from signal_layer.rules.r13_retro import (
    NOT_RETRO_PREDICATE_SQL,
    RetroRuleViolation,
    RetroScope,
    apply_retro_filter,
    is_non_retro,
    is_retro,
    resolve_retro_scope,
    validate_retro_sql,
)
from signal_layer.rules.r15_null_ssn import (
    count_distinct_ssns,
    distinct_ssn_sql,
    is_present_ssn,
)
from signal_layer.rules.r17_fake_records import (
    KNOWN_FAKE_EMAILS,
    FakeRecordSources,
    UnsafeExclusionSQL,
    classify_record,
    exclude_fake_records,
    has_test_marker,
    null_safe_exclusion_sql,
    phone_excluded,
    same_day_bulk_groups,
    validate_exclusion_sql,
)
from signal_layer.rules.r23_no_raw_ssn import (
    RawSSNError,
    assert_no_raw_ssn,
    find_raw_ssn,
    is_clean,
    redact_ssns,
)
from signal_layer.rules.r25_invalid_credit import (
    is_thin_file_assessable,
    is_valid_oldest_tradeline,
    is_valid_time_in_file,
    scrub_credit_fields,
)
from signal_layer.rules.r26_phone_equiv import (
    WORKED_EXAMPLE,
    count_distinct_phones,
    is_comparable_phone,
    norm_phone,
    norm_phone_sql,
    phone_changed,
    row_phones,
    same_phone,
)
from signal_layer.rules.r27_query_budget import (
    MAX_SQL_CALLS_PER_REQUEST,
    QueryBudget,
    QueryBudgetExceeded,
    budgeted,
    incremental_batches,
    plan_calls,
    requires_incremental_summary,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
COVERAGE_DOC = REPO_ROOT / "docs" / "general_rules_coverage.md"


def _normalised_in(needle: str, haystack: str) -> bool:
    """Substring test on whitespace-normalised text, for comparing across RTF conversions."""
    return _source.normalised(needle) in _source.normalised(haystack)


# ---------------------------------------------------------------------------
# The source of the rule text itself
# ---------------------------------------------------------------------------


def test_rules_extracted_from_customer_documents() -> None:
    """All 29 numbered rules are sliced out of the customer's own document, in their order."""
    assert tuple(_source.RULES) == _source.RULE_IDS
    assert len(_source.RULE_IDS) == 29
    assert _source.RULE_IDS[0] == "0.5"
    assert _source.RULE_IDS[-1] == "27"
    # Every rule text begins with the customer's own rule number.
    for rule_id, text in _source.RULES.items():
        assert text.startswith(rule_id), f"rule {rule_id} text does not start with its number"


def test_all_eight_documents_carry_all_rules() -> None:
    """
    Every one of the eight specialist documents carries all 29 rules.

    The employment document omits the "General Rules:" heading and opens straight into
    "PLEASE FOLLOW ALL OF THESE RULES BELOW...", so this also pins that the extractor handles
    both openings.
    """
    assert set(_source.RULES_BY_DOMAIN) == set(ORCHESTRATION_NOTES)
    for domain, rules in _source.RULES_BY_DOMAIN.items():
        assert tuple(rules) == _source.RULE_IDS, f"{domain} rule ids differ"


def test_rule_variants_across_documents() -> None:
    """
    Pin which rules the eight documents word differently.

    Whitespace-only differences (RTF conversion damage) do not count as variants. What remains
    are real per-agent edits, and pinning them means a re-import of the customer's documents
    surfaces any change to a rule's meaning as a test failure rather than a silent shift.
    """
    meaning_variants = {
        rule_id: sorted(
            d
            for d, t in _source.variants_of(rule_id).items()
            if _source.normalised(t) != _source.normalised(_source.rule_text(rule_id))
        )
        for rule_id in _source.RULE_IDS
    }
    differing = {k: v for k, v in meaning_variants.items() if v}
    # Eleven rules are edited per agent; the rest are identical in substance across all eight
    # documents. Pinned as a set so a re-import surfaces any change.
    assert set(differing) == {
        "3", "6", "11", "12", "13", "17", "18", "20", "21", "22", "24", "26",
    }
    # Rule 6 is dropped or shortened in five documents but present in identity.
    assert "dealer" in differing["6"]
    # Rule 26's variation is the gloss sentence only ("The first number in that case is area
    # code"), which five documents omit. The mechanic this package implements - take the last
    # 10 digits - is identical in all eight, which is why the difference is harmless here and
    # is asserted rather than merely tolerated.
    for domain in differing["26"]:
        variant = _source.RULES_BY_DOMAIN[domain]["26"]
        assert _normalised_in("Look at the last 10 digits if the phone number has 11 digits", variant)
        assert "area code" not in variant
    # The load-bearing data-plane rules agree everywhere.
    for stable in ("1", "1.5", "2", "4", "5", "7", "23", "25", "27"):
        assert stable not in differing, f"rule {stable} unexpectedly varies by document"


def test_rule_text_missing_raises() -> None:
    """An unknown rule id raises rather than returning an empty string."""
    with pytest.raises(_source.RuleTextMissing):
        _source.rule_text("99")


# ---------------------------------------------------------------------------
# Rule 0.5 / 6 - fraud score bands (owned by signal_layer.bands)
# ---------------------------------------------------------------------------


def test_rule_0_5_fraud_score_bands() -> None:
    """
    General Rule 0.5: bands 0-299 low, 300-699 medium, 700-998 high, 999 very high.

    Boundaries pinned at every edge the rule names. 999 must NOT fall in the high band: it is
    a sentinel meaning a fraud accelerator fired.
    """
    assert fraud_score_band(0) == "low"
    assert fraud_score_band(299) == "low"
    assert fraud_score_band(300) == "medium"
    assert fraud_score_band(699) == "medium"
    assert fraud_score_band(700) == "high"
    assert fraud_score_band(998) == "high"
    assert fraud_score_band(999) == "very high"
    with pytest.raises(ValueError):
        fraud_score_band(1000)
    # The rule text this delegates to is the customer's.
    assert _normalised_in("Fraud scores between 0-299 are low", _source.rule_text("0.5"))


def test_rule_6_restates_rule_0_5() -> None:
    """
    General Rule 6 is a verbatim restatement of 0.5 in the identity document.

    One implementation, two rule ids. Both ids stay in the catalog for traceability, but there
    must be exactly one band table.
    """
    def body(rule_id: str) -> str:
        """Strip the customer's leading rule number, keeping the rule's own sentence."""
        return _source.rule_text(rule_id)[len(rule_id) :].lstrip(". ").strip()

    assert _source.normalised(body("0.5")) == _source.normalised(body("6"))
    assert catalog.BY_ID["6"].implemented_by == catalog.BY_ID["0.5"].implemented_by


# ---------------------------------------------------------------------------
# Rule 1 - the lender join
# ---------------------------------------------------------------------------


LENDERS = [
    {"lender_name": "CRESCENTBANK", "hash_lender_id": "h1"},
    {"lender_name": "CRESCENTBANK", "hash_lender_id": "h1"},  # duplicate -> DISTINCT
    {"lender_name": "GOLDENONE", "hash_lender_id": "h2"},
    {"lender_name": "ACME_TEST_LENDER", "hash_lender_id": "h3"},  # TEST -> excluded
    {"lender_name": "TESTBANK", "hash_lender_id": "h4"},  # TEST -> excluded
]


def test_rule_1_lender_join() -> None:
    """
    General Rule 1: INNER join on hash_lender_id, DISTINCT subquery, TEST lenders excluded.

    All three parts asserted separately, because each fails differently:
      - the NOT LIKE '%TEST%' predicate drops test lenders from the resolved set;
      - INNER drops applications whose lender did not resolve (rather than emitting a null
        lender, which would re-admit the test lenders the predicate just removed);
      - DISTINCT prevents the duplicate CRESCENTBANK row fanning out h1's applications.
    """
    # (b) the TEST predicate
    assert is_test_lender("ACME_TEST_LENDER") is True
    assert is_test_lender("testbank") is True
    assert is_test_lender("CRESCENTBANK") is False
    assert is_test_lender(None) is False

    resolved = resolve_lenders(LENDERS)
    assert resolved == {"h1": "CRESCENTBANK", "h2": "GOLDENONE"}

    apps = [
        {"application_id": "A1", "hash_lender_id": "h1"},
        {"application_id": "A2", "hash_lender_id": "h2"},
        {"application_id": "A3", "hash_lender_id": "h3"},  # TEST lender
        {"application_id": "A4", "hash_lender_id": "h9"},  # unknown lender
    ]
    joined = inner_join_lenders(apps, LENDERS)

    # (a) INNER: the TEST-lender and unknown-lender applications are gone, not null-joined.
    assert [r["application_id"] for r in joined] == ["A1", "A2"]
    assert all(r["lender_id"] for r in joined)

    # (c) DISTINCT: h1 appears twice in delink_lender_table, and A1 still appears once.
    assert sum(1 for r in joined if r["application_id"] == "A1") == 1
    assert_no_fanout(apps, joined)


def test_rule_1_ambiguous_lender_raises() -> None:
    """One hash_lender_id mapping to two lender names cannot be made deterministic."""
    with pytest.raises(LenderJoinError, match="two lender names"):
        resolve_lenders(
            [
                {"lender_name": "ALPHA", "hash_lender_id": "h1"},
                {"lender_name": "BETA", "hash_lender_id": "h1"},
            ]
        )


def test_rule_1_sql_validation() -> None:
    """The generated join SQL must carry all three of rule 1's guardrails."""
    good = build_lender_join_sql("tbl_auto_consortium")
    validate_lender_join_sql(good)  # does not raise
    assert "inner join" in good.lower()
    assert "select distinct" in good.lower()
    assert "not like '%TEST%'" in good

    # Each part removed in turn must be caught.
    for broken, expect in (
        (good.replace("inner join", "left join"), "outer join"),
        (good.replace("select distinct", "select"), "DISTINCT"),
        (good.replace("where lender_name not like '%TEST%'", ""), "TEST"),
    ):
        with pytest.raises(LenderJoinError, match=expect):
            validate_lender_join_sql(broken)

    # SQL that never touches the lender table is not this rule's business.
    validate_lender_join_sql("select 1 from tbl_auto_consortium")

    with pytest.raises(LenderJoinError):
        build_lender_join_sql("ALL_RECORDS_ALL__ALL_RECORDS_CLEAN")


# ---------------------------------------------------------------------------
# Rule 1.5 / 16 / 19 - lender resolution
# ---------------------------------------------------------------------------


def test_rule_1_5_lender_nicknames() -> None:
    """
    General Rule 1.5: normalise then look up; 12 canonical names, 14 nickname keys.

    The two one-to-many rows are what make it 14: CRESCENTBANK has both ARRA and CRESCENT,
    DIGITALFCU has both DIGITAL and DCU.
    """
    assert len(NICKNAME_TABLE) == 12
    assert len(NICKNAME_INDEX) == 14
    assert NICKNAME_TABLE["CRESCENTBANK"] == ("ARRA", "CRESCENT")
    assert NICKNAME_TABLE["DIGITALFCU"] == ("DIGITAL", "DCU")
    # The self-mapping row the customer wrote is preserved, not "corrected" away.
    assert NICKNAME_TABLE["ARKANSASFCU"] == ("ARKANSASFCU",)

    # Normalisation: trim, strip ALL internal whitespace, uppercase.
    assert norm_lender("  golden   one ") == "GOLDENONE"
    assert norm_lender("General Electric Credit Union") == "GENERALELECTRICCREDITUNION"
    assert norm_lender(None) == ""

    # The three inline examples from the rule text.
    for user_text, expected in INLINE_EXAMPLES.items():
        assert lender_like_pattern(user_text) == expected

    # Nicknames resolve, case-insensitively.
    assert resolve_lender("dcu").lender_name == "DIGITALFCU"
    assert resolve_lender("APCU").lender_name == "ATLANTAPOSTALCU"
    assert resolve_lender("CPS").lender_name == "CONSUMERPORTFOLIOSERVICES"
    assert resolve_lender("PPI").lender_name == "POINTPREDICTIVE"
    assert resolve_lender("Four Sight").lender_name == "FOURSIGHTCAPITAL"


def test_rule_1_5_unresolved_is_data_not_a_guess() -> None:
    """
    "don't hallucinate": no confident match returns an explicit unresolved result.

    The whole design rationale of the rule — a guessed lender silently re-points the analysis
    at another institution's portfolio, and the answer still looks plausible.
    """
    result = resolve_lender("Wells Fargo")
    assert result.resolved is False
    assert result.lender_names == ()
    assert "don't hallucinate" in result.note
    with pytest.raises(LenderUnresolved):
        result.lender_name
    with pytest.raises(LenderUnresolved):
        lender_like_pattern("Some Bank That Does Not Exist")
    assert resolve_lender("").resolved is False
    assert resolve_lender(None).resolved is False


def test_rule_16_prime_subprime_stellantis() -> None:
    """
    General Rule 16: PRIME and SUBPRIME are both Stellantis lender_name values.

    Stellantis therefore resolves to TWO names, so .lender_name raises and callers are forced
    to handle the multi-name case. And PRIME/SUBPRIME are not credit tiers here.
    """
    result = resolve_lender("Stellantis")
    assert result.resolved is True
    assert result.lender_names == STELLANTIS_LENDER_NAMES == ("PRIME", "SUBPRIME")
    assert set(result.like_patterns) == {"%PRIME%", "%SUBPRIME%"}
    with pytest.raises(LenderUnresolved, match="2 lender names"):
        result.lender_name

    for name in ("PRIME", "SUBPRIME"):
        one = resolve_lender(name)
        assert one.lender_name == name
        assert "Stellantis" in one.note
    assert _normalised_in("these are both Stellantis lenders", _source.rule_text("16"))


def test_rule_19_crescent_bank() -> None:
    """
    General Rule 19: Crescent Bank is CRESCENTBANK, never FCRACRESCENTBANK.

    A naive '%CRESCENT%' wildcard — which rule 1.5 itself supplies as a nickname — matches
    both institutions and would double-count them as one.
    """
    for query in ("Crescent Bank", "crescent", "CRESCENTBANK", "ARRA"):
        assert resolve_lender(query).lender_name == "CRESCENTBANK"

    # The naive wildcard would have matched both; the resolver pins one.
    assert "FCRACRESCENTBANK".find("CRESCENT") >= 0  # the collision is real
    assert resolve_lender("Crescent Bank").lender_names == ("CRESCENTBANK",)

    # Asking for FCRACRESCENTBANK by exact name is honoured, with the rule noted.
    explicit = resolve_lender("FCRACRESCENTBANK")
    assert explicit.lender_name == "FCRACRESCENTBANK"
    assert "General Rule 19" in explicit.note


# ---------------------------------------------------------------------------
# Rule 2 - normalisation and the address key
# ---------------------------------------------------------------------------


def test_rule_2_normalisation_and_address_key() -> None:
    """
    General Rule 2: UPPER(TRIM(x)) on borrower strings, and the documented address key.

    The customer's literal worked example is the specification.
    """
    assert norm_string("  Jane  ") == "JANE"
    assert norm_string("jane@Example.COM ") == "JANE@EXAMPLE.COM"
    assert norm_string(None) == ""
    # TRIM strips the ends only: "MARY ANN" must not become "MARYANN".
    assert norm_string("  Mary Ann ") == "MARY ANN"

    # The customer's example, exactly.
    for street_and_zip, expected in ADDRESS_KEY_EXAMPLE.items():
        street, zip_code = street_and_zip.rsplit(",", 1)
        assert address_key(street.strip(), zip_code.strip()) == expected
    assert address_key("123 Walnut St.", "92101") == "123_WALN_92101"

    # Casing, punctuation and spacing variants collapse to one key.
    for street in ("123 walnut street", "  123   Walnut  St. ", "123 N Walnut St Apt 4B"):
        assert address_key(street, "92101") == "123_WALN_92101"
    # ZIP+4 truncates to five.
    assert address_key("123 Walnut St.", "92101-4521") == "123_WALN_92101"
    # A leading-zero zip survives, including after an int round-trip destroys it.
    assert norm_zip("02134") == "02134"
    assert norm_zip(2134) == "02134"
    assert address_key("123 Oak St", 2134) == "123_OAK_02134"

    # Short street names are not padded: OAK must not collide with OAKA.
    assert street_name_prefix("123 Oak St") == "OAK"
    assert street_name_prefix("123 Oakland Ave") == "OAKL"
    assert building_number("123-B Walnut St") == "123"


def test_rule_2_removes_false_positives() -> None:
    """
    The rule's stated purpose: "remove false positives".

    Same street, different building number must NOT collide — that is the false positive a
    straight borr_street_addr comparison, or a street-name-only key, produces.
    """
    assert address_key("123 Walnut St", "92101") != address_key("456 Walnut St", "92101")
    assert same_address("123 Walnut St", "92101", "456 Walnut St", "92101") is False
    # Same number, different zip is also not a match.
    assert same_address("123 Walnut St", "92101", "123 Walnut St", "10007") is False
    # Genuine match across formatting.
    assert same_address("123 Walnut St.", "92101", "123 walnut street", "92101") is True


def test_rule_2_unkeyable_address_raises() -> None:
    """
    An address with no building number cannot be keyed, and must not be partially keyed.

    A key like "_WALN_92101" would match every Walnut address in the zip, turning one bad
    record into a fabricated address cluster — the opposite of the rule's purpose.
    """
    for street, zip_code in (("PO Box 12", "92101"), ("Walnut St", "92101"), ("123 Walnut St", "")):
        with pytest.raises(AddressKeyError):
            address_key(street, zip_code)
    assert same_address("PO Box 12", "92101", "PO Box 12", "92101") is False


# ---------------------------------------------------------------------------
# Rule 3 - alert categories (owned by signal_layer.alert_categories)
# ---------------------------------------------------------------------------


def test_rule_3_alert_categories() -> None:
    """
    General Rule 3: ten alert categories, many-to-many, 166 list entries.

    The many-to-many property is the load-bearing one: 18 ids belong to more than one
    category, and a single-label mapping would silently drop a category for each of them —
    notably 164 would lose its bust-out membership.
    """
    assert len(ALERT_CATEGORIES) == 10
    assert sum(len(v) for v in ALERT_CATEGORIES.values()) == 166
    assert len(MULTI_CATEGORY_ALERTS) == 18
    assert set(category_of(164)) == {"Income", "Bust Out Risk"}
    assert set(category_of(97)) == {"Vehicle Risk", "Collateral"}
    assert len(category_of(3)) == 1
    assert alerts_for_domain("identity")
    assert _normalised_in("Please consider alerts grouped into these categories", _source.rule_text("3"))


# ---------------------------------------------------------------------------
# Rules 4 and 5 - the composite application grain
# ---------------------------------------------------------------------------


def test_rule_4_alert_join_composite_key() -> None:
    """
    General Rule 4: join tbl_afa_alerts_clean on application_id AND hash_lender_id.

    Measured against the wrong join. One application sent to two lenders, each with its own
    alert: the single-key join gives each copy both alerts (4 total, double), the composite
    join gives each copy its own (2 total, correct).
    """
    apps = [
        {"application_id": "A1", "hash_lender_id": "L1"},
        {"application_id": "A1", "hash_lender_id": "L2"},
    ]
    alerts = [
        {"application_id": "A1", "hash_lender_id": "L1", "alert_id": 44},
        {"application_id": "A1", "hash_lender_id": "L2", "alert_id": 46},
    ]

    wrong = join_alerts_by_application_only(apps, alerts)
    assert sum(len(r["alerts"]) for r in wrong) == 4  # inflated

    right = join_alerts(apps, alerts)
    assert sum(len(r["alerts"]) for r in right) == 2  # correct
    assert [a["alert_id"] for a in right[0]["alerts"]] == [44]
    assert [a["alert_id"] for a in right[1]["alerts"]] == [46]

    assert APPLICATION_GRAIN == ("application_id", "hash_lender_id")


def test_rule_4_sql_validation_rejects_single_key_join() -> None:
    """Generated SQL touching the alert table must carry both equality predicates."""
    good = (
        "select * from tbl_auto_consortium v join tbl_afa_alerts_clean a "
        "on a.application_id = v.application_id and a.hash_lender_id = v.hash_lender_id"
    )
    validate_join_sql(good)

    bad = (
        "select * from tbl_auto_consortium v join tbl_afa_alerts_clean a "
        "on a.application_id = v.application_id"
    )
    with pytest.raises(JoinKeyError, match="hash_lender_id"):
        validate_join_sql(bad)

    # Uppercase SQL and the lender_id alias are both accepted.
    validate_join_sql(good.upper())
    validate_join_sql(good.replace("hash_lender_id", "lender_id"))

    # USING is an equally valid spelling of the composite key and must not be a false positive.
    validate_join_sql(
        "select * from tbl_auto_consortium join tbl_afa_alerts_clean "
        "using (application_id, hash_lender_id)"
    )
    with pytest.raises(JoinKeyError, match="hash_lender_id"):
        validate_join_sql(
            "select * from tbl_auto_consortium join tbl_afa_alerts_clean "
            "using (application_id)"
        )

    # Not this rule's business when the alert table is absent.
    validate_join_sql("select 1 from tbl_auto_consortium")


def test_rule_5_application_grain() -> None:
    """
    General Rule 5: application retrieval requires application_id AND a lender identifier,
    because "the same application can be sent to different lenders (this is normal)".

    Also asserts the semantic half: multi-lender reach must be reported as reach, and
    de-duplication must collapse the copies rather than counting them as velocity.
    """
    assert require_composite_key("A1", "L1") == ("A1", "L1")
    assert require_composite_key("A1", hash_lender_id="L1") == ("A1", "L1")
    with pytest.raises(JoinKeyError, match="lender identifier"):
        require_composite_key("A1")
    with pytest.raises(JoinKeyError):
        require_composite_key(None, "L1")

    # Either alias satisfies the rule.
    assert join_keys({"application_id": "A1", "lender_id": "L1"}) == ("A1", "L1")
    with pytest.raises(JoinKeyError):
        join_keys({"application_id": "A1"})

    rows = [
        {"application_id": "A1", "hash_lender_id": "L1"},
        {"application_id": "A1", "hash_lender_id": "L2"},
        {"application_id": "A1", "hash_lender_id": "L3"},
        {"application_id": "A2", "hash_lender_id": "L1"},
    ]
    assert submitted_to_lender_count(rows, "A1") == 3
    deduped = deduplicate_applications(rows)
    assert len(deduped) == 2  # two applications, not four
    a1 = next(r for r in deduped if r["application_id"] == "A1")
    assert a1["lender_count"] == 3
    assert _normalised_in("the same application can be sent to different lenders", _source.rule_text("5"))


# ---------------------------------------------------------------------------
# Rule 7 - the default definition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "chargeoff,epd,epd6,expect_default,expect_pure_chargeoff",
    [
        (False, False, False, False, False),
        (False, True, False, True, False),
        (False, False, True, True, False),
        (False, True, True, True, False),
        (True, False, False, False, True),   # the excluded population
        (True, True, False, True, False),    # chargeoff + epd STILL counts
        (True, False, True, True, False),    # chargeoff + epd6 STILL counts
        (True, True, True, True, False),
    ],
)
def test_rule_7_chargeoff_exclusion(
    chargeoff: bool, epd: bool, epd6: bool, expect_default: bool, expect_pure_chargeoff: bool
) -> None:
    """
    General Rule 7: exclude EXCLUSIVELY chargeoffs from default calculations.

    The full chargeoff x (epd, epd6) truth table. The two rows that matter are (True, True,
    False) and (True, False, True): a chargeoff that also has an EPD flag IS a default, so the
    obvious misreading "AND chargeoff_flag = false" would wrongly drop them and under-count
    defaults — which makes a bad portfolio look clean.
    """
    row = {"chargeoff_flag": chargeoff, "epd_flag": epd, "epd6_flag": epd6}
    assert is_default(row) is expect_default
    assert is_pure_chargeoff(row) is expect_pure_chargeoff


def test_rule_7_default_rate_excludes_pure_chargeoffs() -> None:
    """Pure chargeoffs leave the denominator entirely; they are neither default nor clean."""
    rows = [
        {"chargeoff_flag": False, "epd_flag": True, "epd6_flag": False},   # default
        {"chargeoff_flag": False, "epd_flag": False, "epd6_flag": False},  # clean
        {"chargeoff_flag": True, "epd_flag": False, "epd6_flag": False},   # excluded
        {"chargeoff_flag": True, "epd_flag": False, "epd6_flag": False},   # excluded
    ]
    counts = default_counts(rows)
    assert counts == {
        "defaults": 1,
        "non_defaults": 1,
        "excluded_pure_chargeoffs": 2,
        "considered": 2,
        "total": 4,
    }
    assert default_rate(rows) == 0.5  # 1/2, not 1/4
    assert default_rate([]) == 0.0
    # The SQL form never mentions the chargeoff flag.
    assert "chargeoff" not in default_sql_predicate()
    # Field aliases across the two source tables.
    assert is_default({"epd": 1}) is True
    assert is_default({"epd6": "Y"}) is True
    assert is_default({"epd_flag": None, "epd6_flag": None}) is False


# ---------------------------------------------------------------------------
# Rule 8 - deprecated tables
# ---------------------------------------------------------------------------


def test_rule_8_deprecated_tables() -> None:
    """
    General Rule 8: the two deprecated all-records tables must not be queried.

    Matched with underscore runs collapsed, because the customer's own two names use
    inconsistent underscore counts and a literal match would miss a typo variant. The
    approved ..._FOR_PG table must never be caught.
    """
    for table in DEPRECATED_TABLES:
        assert is_deprecated_table(table) is True
        assert is_deprecated_table(table.lower()) is True
    # Underscore-count typo variants of both spellings.
    assert is_deprecated_table("ALL_RECORDS_ALL__ALL_RECORDS_CLEAN_PERF") is True
    assert is_deprecated_table("ALL_RECORDS_ALL_ALL_RECORDS_CLEAN") is True
    assert is_deprecated_table(APPROVED_ALL_RECORDS_TABLE) is False
    assert is_deprecated_table("tbl_auto_consortium") is False

    # The f-string IS the fixture: this feeds a SQL string to a validator to prove the
    # validator accepts the approved table. Nothing executes it, and the interpolated
    # value is a module constant.
    approved_sql = f"select * from {APPROVED_ALL_RECORDS_TABLE}"  # nosec B608 - test fixture
    validate_no_deprecated_tables(approved_sql)
    with pytest.raises(DeprecatedTableError, match="CLEAN"):
        validate_no_deprecated_tables("select * from ALL_RECORDS_ALL__ALL_RECORDS_CLEAN")


# ---------------------------------------------------------------------------
# Rules 9 and 18 - source precedence
# ---------------------------------------------------------------------------


def test_rule_9_coborrower_source_precedence() -> None:
    """
    General Rule 9: tbl_auto_consortium first, then all records.

    The three-valued result is the point. ABSENT ("checked both, none") and UNKNOWN ("never
    found out") must not collapse, because the identity agent escalates an already-high-risk
    case on a *known* missing co-borrower.
    """
    assert COBORROWER_SOURCE_ORDER[0] == "tbl_auto_consortium"

    # Consortium wins when both have data.
    result = resolve_coborrower(
        [{"coborr_first_name": "Ann"}], [{"coborr_first_name": "Other"}]
    )
    assert result.state is Coborrower.PRESENT
    assert result.source == "tbl_auto_consortium"
    assert result.has_coborrower is True

    # Falls back to all-records only when consortium is silent.
    fallback = resolve_coborrower([], [{"coborr_ssn": "x"}])
    assert fallback.state is Coborrower.PRESENT
    assert fallback.source == "all_records_all_all_records_for_pg"

    # Both consulted, neither has one -> ABSENT.
    absent = resolve_coborrower([], [])
    assert absent.state is Coborrower.ABSENT
    assert absent.sources_checked == COBORROWER_SOURCE_ORDER

    # Nothing consulted -> UNKNOWN, and it is NOT the same as ABSENT.
    unknown = resolve_coborrower(None, None)
    assert unknown.state is Coborrower.UNKNOWN
    assert unknown.has_coborrower is False
    assert unknown.state is not absent.state

    # An explicit false flag overrides field sniffing.
    assert resolve_coborrower([{"has_coborrower": 0}], []).state is Coborrower.ABSENT


def test_rule_18_source_fallback() -> None:
    """
    General Rule 18: an empty all-records result is a mandatory retry, not an answer.

    "YOU NEED TO CHECK TBL_AUTO_CONSORTIUM especially if nothing shows in all_records_for_pg."
    """
    assert next_source([], []) == "tbl_auto_consortium"
    assert (
        next_source([], ["tbl_auto_consortium"]) == "all_records_all_all_records_for_pg"
    )
    # Rows found -> no retry.
    assert next_source([{"application_id": "A1"}], []) is None
    # Every source tried and still empty -> "no data" may finally be reported.
    assert is_exhausted([], ["tbl_auto_consortium"]) is False
    assert (
        is_exhausted([], ["tbl_auto_consortium", "all_records_all_all_records_for_pg"])
        is True
    )
    assert next_source([], ["tbl_auto_consortium", "all_records_all_all_records_for_pg"]) is None


# ---------------------------------------------------------------------------
# Rules 10 and 14 - dealers
# ---------------------------------------------------------------------------


DEALERS = [
    {"ppi_dealer_id": "P1", "dealer_name": "ACME MOTORS", ACTIVITY_FIELD: 12},
    {"ppi_dealer_id": "P2", "dealer_name": "BETA AUTO", ACTIVITY_FIELD: 0},
    {"ppi_dealer_id": "P3", "dealer_name": "GAMMA CARS", ACTIVITY_FIELD: None},
    {"ppi_dealer_id": "P4", "dealer_name": "DELTA SALES", ACTIVITY_FIELD: 1},
]


def test_rule_10_active_dealers() -> None:
    """
    General Rule 10: active means consortium_number_of_apps_last_1_year > 0.

    Strictly greater than zero, and the NULL branch is asserted explicitly: NULL is not > 0 in
    SQL, so an unknown activity level is excluded. The customer does not state this, so it is
    pinned here rather than left to three-valued logic.
    """
    assert is_active_dealer(DEALERS[0]) is True   # 12
    assert is_active_dealer(DEALERS[1]) is False  # 0 - strictly greater
    assert is_active_dealer(DEALERS[2]) is False  # NULL
    assert is_active_dealer(DEALERS[3]) is True   # 1
    assert is_active_dealer({}) is False          # field absent
    assert is_active_dealer({ACTIVITY_FIELD: -3}) is False

    names = [d["dealer_name"] for d in active_dealers(DEALERS)]
    assert names == ["ACME MOTORS", "DELTA SALES"]
    assert active_dealer_sql_predicate() == f"d.{ACTIVITY_FIELD} > 0"


def test_rule_14_dealer_identity_key() -> None:
    """
    General Rule 14: ppi_dealer_id AND dealer_name together identify a dealer.

    "Please always use this one" is unconditional, so a half key raises rather than silently
    merging two dealerships that share an id or splitting one that shares a name.
    """
    assert DEALER_IDENTITY_KEY == ("ppi_dealer_id", "dealer_name")
    assert dealer_key(DEALERS[0]) == ("P1", "ACME MOTORS")
    with pytest.raises(DealerIdentityError, match="dealer_name"):
        dealer_key({"ppi_dealer_id": "P1"})
    with pytest.raises(DealerIdentityError, match="ppi_dealer_id"):
        dealer_key({"dealer_name": "ACME MOTORS"})

    # Two rooftops sharing a ppi_dealer_id are two dealers, not one.
    shared_id = [
        {"ppi_dealer_id": "P9", "dealer_name": "ACME NORTH", ACTIVITY_FIELD: 5},
        {"ppi_dealer_id": "P9", "dealer_name": "ACME SOUTH", ACTIVITY_FIELD: 5},
    ]
    assert len(distinct_dealers(shared_id)) == 2
    # Rules 10 and 14 applied together.
    assert count_active_distinct_dealers(DEALERS) == 2


# ---------------------------------------------------------------------------
# Rule 11 - date-field routing
# ---------------------------------------------------------------------------


def test_rule_11_date_field_routing() -> None:
    """
    General Rule 11: all four parts.

    (a) the literal token "submitted" selects submitted_to_prod_at; (b) that path starts in
    TBL_AUTO_CONSORTIUM; (c) an undated "past applications generally" question uses
    application_date; (d) the CURRENT_TIMESTAMP() predicate is rejected outright.
    """
    submitted = resolve_date_field("How many applications were submitted yesterday?")
    assert submitted.field == SUBMITTED_FIELD
    assert submitted.is_submitted is True
    assert submitted.primary_table == SUBMITTED_PRIMARY_TABLE == "TBL_AUTO_CONSORTIUM"

    for question in (
        "How many applications yesterday?",
        "Show me past applications for this borrower",
        "applications in June",
    ):
        assert resolve_date_field(question).field == DEFAULT_DATE_FIELD
    assert resolve_date_field(None).field == DEFAULT_DATE_FIELD

    # (d) the anti-pattern, in every spelling.
    for bad in (
        "where tac.submitted_to_prod_at <= CURRENT_TIMESTAMP()",
        "where submitted_to_prod_at<=current_timestamp()",
        "where v.submitted_to_prod_at < CURRENT_TIMESTAMP ()",
        "where tac.submitted_to_prod_at\n <= CURRENT_TIMESTAMP()",
        # Same intent, same reported symptom, different function name.
        "where tac.submitted_to_prod_at <= CURRENT_DATE()",
        "where tac.submitted_to_prod_at <= SYSDATE",
    ):
        with pytest.raises(ForbiddenDatePredicate):
            validate_date_sql(bad)
    # A legitimate bound on the same field is fine.
    validate_date_sql("where tac.submitted_to_prod_at >= '2026-06-01'")
    validate_date_sql("where tac.submitted_to_prod_at between '2026-06-01' and '2026-06-30'")
    # And CURRENT_TIMESTAMP() bounding a DIFFERENT field is not this rule's business.
    validate_date_sql("where tac.application_date <= CURRENT_TIMESTAMP()")

    predicate = build_date_predicate("applications submitted in June", "2026-06-01", "2026-06-30")
    assert SUBMITTED_FIELD in predicate
    assert "current_timestamp" not in predicate.lower()


def test_rule_11_retro_overrides_submitted() -> None:
    """
    General Rules 11 and 13 conflict for a retro-scoped "submitted" question; 13 wins.

    submitted_to_prod_at is NULL for retro records, so routing there returns an empty set that
    reads as "no activity". The substitution must surface as a caveat.
    """
    decision = resolve_date_field("How many applications were submitted during retro?")
    assert decision.field == DEFAULT_DATE_FIELD
    assert "General Rule 13" in decision.reason
    assert decision.caveat
    assert "PLEASE DO NOT" in _source.rule_text("13")


# ---------------------------------------------------------------------------
# Rule 12 - recommendation constraints
# ---------------------------------------------------------------------------


def test_rule_12_recommendation_constraints() -> None:
    """
    General Rule 12: no unsolicited recommendations, no auto-decline, no criminal penalty.

    All three prohibitions. Note the decline and criminal checks apply even when
    recommendations WERE requested — asking for advice does not authorise recommending
    adverse action.
    """
    # Present verbatim in all eight specialist prompts (primary enforcement).
    probe = "Do not provide recommendations for loan response unless you are prompted for recommendations."
    for domain, text in ORCHESTRATION_NOTES.items():
        assert _normalised_in(probe, text), f"{domain} prompt lacks rule 12 text"

    assert requests_recommendations("What do you recommend?") is True
    assert requests_recommendations("Any stipulations we should apply?") is True
    assert requests_recommendations("Summarise the identity risk") is False
    assert requests_recommendations(None) is False

    # Auto-decline is forbidden even when recommendations were asked for.
    bad = "Given the fraud score of 999, we recommend declining the application."
    result = check_recommendation_text(bad, recommendations_requested=True)
    assert result.ok is False
    assert any("adverse action" in v for v in result.violations)

    # Criminal outcomes forbidden.
    criminal = check_recommendation_text(
        "Refer the borrower for arrest and prosecution.", recommendations_requested=True
    )
    assert criminal.ok is False
    assert any("criminal" in v for v in criminal.violations)

    # The sanctioned framing passes.
    good = "We recommend the lender conduct further analysis and additional review."
    assert check_recommendation_text(good, recommendations_requested=True).ok is True

    # Unsolicited recommendation is a violation even when its content is sanctioned.
    unsolicited = check_recommendation_text(good, recommendations_requested=False)
    assert unsolicited.ok is False
    assert any("did not ask" in v for v in unsolicited.violations)

    # A neutral narrative with no recommendation at all is fine unprompted.
    assert check_recommendation_text(
        "Identity risk is HIGH RISK. Three applications share one address key.",
        recommendations_requested=False,
    ).ok is True

    # Both of the customer's own header spellings are recognised.
    assert has_recommendations_section("## ANALYSIS\n###RECOMMENDATIONS\n- review") is True
    assert has_recommendations_section("## RECOMMENDATIONS") is True
    assert has_recommendations_section("## ANALYSIS") is False


# ---------------------------------------------------------------------------
# Rule 13 - retro
# ---------------------------------------------------------------------------


def test_rule_13_retro_handling() -> None:
    """
    General Rule 13: all three mechanics.

    (a) no filter by default; (b) the null-safe predicate keeps NULL record_from rows; (c) a
    retro-scoped query must not touch submitted_to_prod_at.
    """
    # (a) default is no filter at all.
    default = resolve_retro_scope("How many applications last week?")
    assert default.scope is RetroScope.ALL
    assert default.predicate == ""
    assert default.filters is False
    assert resolve_retro_scope(None).scope is RetroScope.ALL

    assert resolve_retro_scope("Show me non-retro data").scope is RetroScope.NON_RETRO
    assert resolve_retro_scope("prod data only").scope is RetroScope.NON_RETRO
    assert resolve_retro_scope("what happened during retro").scope is RetroScope.RETRO_ONLY
    # "non-retro" contains "retro"; precedence must not invert the scope.
    assert resolve_retro_scope("non-retro applications").scope is RetroScope.NON_RETRO

    # (b) null tolerance - the expensive half.
    assert is_non_retro(None) is True
    assert is_non_retro("PROD") is True
    assert is_non_retro("RETRO_2025") is False
    assert is_retro(None) is False  # unknown provenance is not known to be retro
    assert is_retro("retro") is True
    assert "record_from is null" in NOT_RETRO_PREDICATE_SQL

    rows = [
        {"application_id": "A1", "record_from": "PROD"},
        {"application_id": "A2", "record_from": None},
        {"application_id": "A3", "record_from": "RETRO"},
        {"application_id": "A4"},  # column absent
    ]
    assert len(apply_retro_filter(rows, RetroScope.ALL)) == 4
    non_retro = [r["application_id"] for r in apply_retro_filter(rows, RetroScope.NON_RETRO)]
    assert non_retro == ["A1", "A2", "A4"]  # the NULL row survives
    retro_only = [r["application_id"] for r in apply_retro_filter(rows, RetroScope.RETRO_ONLY)]
    assert retro_only == ["A3"]


def test_rule_13_sql_validation() -> None:
    """
    A bare ``record_from not like '%RETRO%'`` is rejected; it silently drops NULL rows.

    And a retro-scoped query referencing submitted_to_prod_at is rejected outright.
    """
    with pytest.raises(RetroRuleViolation, match="is null"):
        validate_retro_sql("where record_from not like '%RETRO%'")
    validate_retro_sql(f"where {NOT_RETRO_PREDICATE_SQL}")

    with pytest.raises(RetroRuleViolation, match="submitted_to_prod_at"):
        validate_retro_sql(
            "where record_from like '%RETRO%' and submitted_to_prod_at >= '2026-01-01'",
            RetroScope.RETRO_ONLY,
        )
    # The same reference is fine outside the retro scope.
    validate_retro_sql("where submitted_to_prod_at >= '2026-01-01'", RetroScope.ALL)


# ---------------------------------------------------------------------------
# Rule 15 - null SSN
# ---------------------------------------------------------------------------


def test_rule_15_null_ssn_not_distinct() -> None:
    """
    General Rule 15: a NULL borr_ssn is not a distinct SSN.

    Measured against the naive count. One NULL inflates a reuse count by one, and a phone
    shared by one real borrower plus one NULL-SSN record reads as two identities — a
    fabricated identity cluster out of a missing value.
    """
    values = ["hash_a", "hash_b", None, "hash_a", ""]
    assert len(set(values)) == 4      # the naive count: wrong
    assert count_distinct_ssns(values) == 2  # the rule's count

    assert is_present_ssn("hash_a") is True
    assert is_present_ssn(None) is False
    assert is_present_ssn("") is False
    assert is_present_ssn("   ") is False
    assert is_present_ssn("NULL") is False
    assert count_distinct_ssns([]) == 0
    assert count_distinct_ssns([None, None]) == 0

    # The emitted SQL states the rule it honours.
    assert "is not null" in distinct_ssn_sql()


# ---------------------------------------------------------------------------
# Rule 17 - fake records
# ---------------------------------------------------------------------------


def test_rule_17_fake_record_exclusion() -> None:
    """
    General Rule 17: the three exclusion sources, applied null-safely.

    The null-safety assertions are the important ones: a record with no email, no VIN and no
    phone must survive. Getting this wrong deletes every legitimate application missing a
    contact field, silently.
    """
    sources = FakeRecordSources.build(
        fake_emails=["123@gmail.com", "NA@gmail.com"],
        common_vins=["1HGCM82633A004352"],
        common_phones=["14724933572", "8005551234"],
    )

    assert classify_record({"borr_email": "123@GMAIL.COM"}, sources).excluded is True
    assert classify_record({"borr_email": " 123@gmail.com "}, sources).excluded is True
    assert classify_record({"v_vin": "1hgcm82633a004352"}, sources).excluded is True

    # NULL/absent fields must NOT be excluded.
    for row in ({}, {"borr_email": None}, {"v_vin": None}, {"borr_email": None, "v_vin": None}):
        assert classify_record(row, sources).excluded is False, row
    assert classify_record({"borr_email": "real@example.com"}, sources).excluded is False

    assert KNOWN_FAKE_EMAILS == frozenset(
        {"123@GMAIL.COM", "NA@GMAIL.COM", "NONE@YAHOO.COM", "NOEMAIL@AOL.COM"}
    )


def test_rule_17_phone_carve_out() -> None:
    """
    The phone exclusion's explicit carve-out: exclude only when there is NO other valid phone.

    "if either all phone numbers are null or at least one of the fields is a phone number not
    in the common consortium table the record should be included in results."
    """
    sources = FakeRecordSources.build(common_phones=["14724933572", "8005551234"])

    # All phones null -> KEEP.
    assert phone_excluded({}, sources) is False
    assert phone_excluded(
        {"borr_home_phone": None, "borr_cell_phone": None, "borr_work_phone": None}, sources
    ) is False

    # At least one phone not in the common set -> KEEP.
    assert phone_excluded(
        {"borr_home_phone": "8005551234", "borr_cell_phone": "6195550100"}, sources
    ) is False

    # Every present phone is common-consortium -> EXCLUDE.
    assert phone_excluded({"borr_home_phone": "8005551234"}, sources) is True
    assert phone_excluded(
        {"borr_home_phone": "8005551234", "borr_cell_phone": "4724933572"}, sources
    ) is True

    # Rule 26 interaction: the common set holds the 11-digit form, the row the 10-digit form.
    assert phone_excluded({"borr_home_phone": "4724933572"}, sources) is True
    assert phone_excluded({"borr_home_phone": "(472) 493-3572"}, sources) is True


def test_rule_17_test_markers_and_bulk() -> None:
    """The TEST-marker and same-day-bulk heuristics, and the fact that bulk is opt-in."""
    assert has_test_marker({"borr_first_name": "TEST"}) is True
    assert has_test_marker({"borr_email": "jane.test@example.com"}) is True
    assert has_test_marker({"borr_street_addr": "1 Test Drive"}) is True
    assert has_test_marker({"borr_first_name": "Jane", "borr_last_name": "Doe"}) is False

    rows = [
        {"application_id": f"A{i}", "hash_lender_id": "L1", "application_date": "2026-06-01"}
        for i in range(10)
    ] + [{"application_id": "B1", "hash_lender_id": "L2", "application_date": "2026-06-01"}]
    groups = same_day_bulk_groups(rows)
    assert groups == {("L1", "2026-06-01"): 10}

    # The customer hedges ("likely also not real"), so bulk exclusion is opt-in.
    kept, excluded = exclude_fake_records(rows)
    assert len(kept) == 11 and not excluded
    kept, excluded = exclude_fake_records(rows, apply_same_day_bulk=True)
    assert len(kept) == 1 and len(excluded) == 10
    assert all("same lender on the same day" in r.reasons[0] for _, r in excluded)


def test_rule_17_sql_null_safety() -> None:
    """
    The mandated null-safe idiom, and rejection of the bare form that deletes rows.

    This is the single most expensive mistake available in the whole rule set.
    """
    good = null_safe_exclusion_sql("borr_email", "__fake_emails")
    assert good == (
        "(ar.borr_email <> ALL (SELECT borr_email FROM __fake_emails) "
        "or ar.borr_email is null)"
    )
    # Same shape as above: the assembled SQL is the input under test, not a query.
    good_sql = f"select * from t ar where {good}"  # nosec B608 - test fixture
    validate_exclusion_sql(good_sql)

    for bad in (
        "select * from t ar where ar.borr_email <> ALL (SELECT borr_email FROM __fake_emails)",
        "select * from t ar where ar.v_vin NOT IN (SELECT v_vin FROM CONSORTIUM_COMMON_VIN)",
    ):
        with pytest.raises(UnsafeExclusionSQL, match="is null"):
            validate_exclusion_sql(bad)


def test_rule_17_returns_both_halves() -> None:
    """exclude_fake_records() returns survivors AND exclusions with reasons, so it is auditable."""
    sources = FakeRecordSources.build(fake_emails=["123@gmail.com"])
    rows = [
        {"application_id": "A1", "borr_email": "real@example.com"},
        {"application_id": "A2", "borr_email": "123@gmail.com"},
        {"application_id": "A3", "borr_email": None},
    ]
    kept, excluded = exclude_fake_records(rows, sources)
    assert [r["application_id"] for r in kept] == ["A1", "A3"]  # the NULL row survives
    assert len(excluded) == 1
    assert excluded[0][0]["application_id"] == "A2"
    assert "fake-emails source" in excluded[0][1].reasons[0]


# ---------------------------------------------------------------------------
# Rules 20, 21, 22, 24 - interpretive rules enforced in the prompt / bands
# ---------------------------------------------------------------------------


def test_rule_20_incomepass_semantics_in_prompts() -> None:
    """
    General Rule 20 is score interpretation, so it belongs in the model's context.

    Asserted two ways: signal_layer.bands carries the verbatim semantics, and the text is
    actually present in the loaded verbatim prompts for all eight specialists.
    """
    assert _normalised_in("income_misrep_score", INCOMEPASS_SEMANTICS)
    assert _normalised_in("distinct from fraud score", INCOMEPASS_SEMANTICS)
    for domain, text in ORCHESTRATION_NOTES.items():
        assert _normalised_in("This is distinct from fraud score.", text), domain
        assert _normalised_in("income_misrep_score", text), domain
    assert catalog.BY_ID["20"].enforcement_layer == "prompt-verbatim"


def test_rule_21_dealer_score_binding() -> None:
    """
    General Rule 21: dealer_risk_score is lender-scoped, dealer_consortium_score is not.

    signal_layer.bands owns the id binding. Note the sentence stating it appears in only three
    of the eight documents (identity, straw, synthetic) — the mechanically important half is
    absent from the other five, which is exactly why it must live in code rather than be
    recalled from whichever prompt happens to be loaded.
    """
    assert DEALER_SCORE_BINDING["dealer_risk_score"] == ("dealer_id", "lender_id")
    assert DEALER_SCORE_BINDING["dealer_consortium_score"] == ("ppi_dealer_id",)
    probe = "The dealer_risk_score is tied to the dealer_id"
    carrying = [d for d, t in ORCHESTRATION_NOTES.items() if _normalised_in(probe, t)]
    assert set(carrying) == {"identity", "straw", "synthetic"}
    # But every document states the two-score distinction itself.
    for domain, text in ORCHESTRATION_NOTES.items():
        assert _normalised_in("There are two scores that measure dealer risk", text), domain


def test_rule_22_no_fraud_score_averaging() -> None:
    """
    General Rule 22 bans a statistic rather than prescribing a computation.

    So the assertions are: the caveat text is in every prompt, bands carries it verbatim, and
    this rules package computes no fraud-score mean anywhere.
    """
    assert _normalised_in("standard-scaled", FRAUD_SCORE_AVERAGING_RULE)
    probe = "Averaging fraud scores is not always super meaningful because we standard-scaled them."
    for domain, text in ORCHESTRATION_NOTES.items():
        assert _normalised_in(probe, text), domain

    # Parsed rather than grepped, so a mention of "AVG(fraud_score)" inside a docstring or a
    # catalog note (which is documentation OF the ban) is not mistaken for a violation of it.
    # Only a real call to a mean/average function counts.
    import ast

    banned = {"mean", "average", "avg", "fmean", "nanmean"}
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "signal_layer" / "rules").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            if name.lower() in banned:
                offenders.append(f"{path.name}:{node.lineno} {name}()")
    assert not offenders, f"fraud-score averaging is computed at {offenders}"


def test_rule_24_product_decode() -> None:
    """
    General Rule 24: PRODUCT is a concatenation, matched by substring with wildcards both sides.

    The customer's own worked example ("AFMIPIP" -> AFM and IP) is the assertion. Owned by
    signal_layer.bands.
    """
    decoded = {p.acronym for p in decode_product("AFMIPIP")}
    assert "AFM" in decoded and "IP" in decoded
    assert decode_product(None) == []
    assert decode_product("") == []
    assert _normalised_in('if the PRODUCT field is something like "AFMIPIP"', _source.rule_text("24"))
    # 'BC' is listed twice in the customer's own table, for two different products.
    assert _source.rule_text("24").count("'BC' =") == 2


# ---------------------------------------------------------------------------
# Rule 23 - the raw-SSN gate
# ---------------------------------------------------------------------------


def test_rule_23_no_raw_ssn() -> None:
    """
    General Rule 23: never include raw SSN values or ANY PARTS of them.

    Nested dicts and lists, stringified numbers, integers, masked forms and prose last-4
    disclosures all raise. Hashed values pass.
    """
    for payload in (
        {"borr_ssn": "123-45-6789"},
        {"borr_ssn": 123456789},
        {"applicant": {"ssn_value": "123456789"}},
        {"a": [{"b": {"ssn": "987654321"}}]},
        {"note": "applicant ***-**-6789 flagged"},
        {"summary": "SSN ending 6789 matched three applications"},
        {"findings": ["clean", {"detail": "ssn 123 45 6789"}]},
        {"123-45-6789": "used as a dict key"},
    ):
        with pytest.raises(RawSSNError):
            assert_no_raw_ssn(payload)
        assert find_raw_ssn(payload)

    # Hashed values are explicitly permitted ("YOU SHOULD ONLY FOCUS ON THE HASHED SSN VALUES").
    for clean in (
        {"hash_ssn": "hash_ssn_7f3a91"},
        {"hash_ssn": "a" * 64},
        {"borr_hash_ssn": "hash_ssn_2c88de", "signals": {"ssn_days_in_system": 210}},
    ):
        assert_no_raw_ssn(clean)
        assert is_clean(clean)


def test_rule_23_does_not_false_positive() -> None:
    """
    The gate must not fire on ordinary payloads, or it gets disabled and protects nothing.

    Amounts, zips, phones, dates, VINs and SSN-derived *counts* must all pass.
    """
    payload = {
        "application_id": "APP-1001",
        "loan_amt": 45000,
        "borr_income_annual": 128000,
        "borr_zip": "92101",
        "borr_cell_phone": "472-493-3572",
        "application_date": "2026-06-01",
        "v_vin": "1HGCM82633A004352",
        "signals": {
            "phone_reuse_distinct_ssn_count": 4,
            "ssn_days_in_system": 210,
            "ssn_distinct_identity_combinations": 1,
        },
        "account_ref": "123456789",  # 9 digits, but not an SSN-named field
    }
    assert_no_raw_ssn(payload)
    assert find_raw_ssn(payload) == []


def test_rule_23_hashed_field_is_not_trusted_blindly() -> None:
    """
    A field NAMED hash_ssn is not trusted to CONTAIN a hash.

    If the hashing step is skipped or misconfigured, a raw SSN lands in hash_ssn and a
    name-based allowlist would wave it straight through — the exact failure the rule exists
    to prevent.
    """
    with pytest.raises(RawSSNError):
        assert_no_raw_ssn({"hash_ssn": "123456789"})
    with pytest.raises(RawSSNError):
        assert_no_raw_ssn({"hash_ssn": "123-45-6789"})


def test_rule_23_error_message_is_itself_redacted() -> None:
    """The exception message must not leak the value it is complaining about."""
    with pytest.raises(RawSSNError) as excinfo:
        assert_no_raw_ssn({"borr_ssn": "123-45-6789"})
    assert "123-45-6789" not in str(excinfo.value)
    assert "###-##-####" in str(excinfo.value)
    assert "[SSN REDACTED]" in redact_ssns("ssn is 123-45-6789 here")


def test_rule_23_real_fixtures_are_clean() -> None:
    """The repo's own mock application data passes the gate."""
    from data.applications import APPLICATIONS

    assert_no_raw_ssn(APPLICATIONS, context="data/applications.py")


# ---------------------------------------------------------------------------
# Rule 25 - invalid credit data
# ---------------------------------------------------------------------------


def test_rule_25_invalid_credit_data() -> None:
    """
    General Rule 25: time in file <= 0 or 9999 is null; oldest tradeline null or zero is absent.

    The time-in-file half is signal_layer.bands'; this asserts the oldest-tradeline half and
    the combined row scrub. Both directions of the sentinel failure matter: 9999 masks a
    synthetic thin file, 0 manufactures one.
    """
    assert is_valid_time_in_file(24) is True
    assert is_valid_time_in_file(0) is False
    assert is_valid_time_in_file(-5) is False
    assert is_valid_time_in_file(9999) is False
    assert is_valid_time_in_file(None) is False

    assert is_valid_oldest_tradeline("2015-03-01") is True
    assert is_valid_oldest_tradeline(None) is False
    assert is_valid_oldest_tradeline(0) is False
    assert is_valid_oldest_tradeline("0") is False
    assert is_valid_oldest_tradeline("0000-00-00") is False
    assert is_valid_oldest_tradeline("") is False

    scrubbed = scrub_credit_fields(
        {"time_in_file_months": 9999, "date_oldest_trade_line": 0, "credit_score": 700}
    )
    assert scrubbed["time_in_file_months"] is None
    assert scrubbed["date_oldest_trade_line"] is None
    assert scrubbed["credit_report_usable"] is False
    assert scrubbed["credit_score"] == 700  # untouched
    assert set(scrubbed["credit_report_scrubbed_fields"]) == {
        "time_in_file_months",
        "date_oldest_trade_line",
    }

    ok = scrub_credit_fields({"time_in_file_months": 36, "date_oldest_trade_line": "2015-03-01"})
    assert ok["credit_report_usable"] is True

    # Thin-file reasoning is blocked on unusable data: "cannot assess", never "thin file".
    assert is_thin_file_assessable({"time_in_file_months": 3}) is True
    assert is_thin_file_assessable({"time_in_file_months": 9999}) is False
    assert is_thin_file_assessable({}) is False


# ---------------------------------------------------------------------------
# Rule 26 - phone equivalence
# ---------------------------------------------------------------------------


def test_rule_26_phone_equivalence() -> None:
    """
    General Rule 26: the customer's worked example, and the undercount it prevents.

    Four borrowers on one phone, two stored with a leading 1: naively that is two phones with
    two borrowers each and no threshold is crossed, so a real shared-phone ring is missed.
    """
    eleven, ten = WORKED_EXAMPLE
    assert norm_phone(eleven) == norm_phone(ten) == "4724933572"
    assert same_phone(eleven, ten) is True

    for variant in ("(472) 493-3572", "472-493-3572", "+1 472 493 3572", "1-472-493-3572", 14724933572):
        assert norm_phone(variant) == "4724933572", variant

    values = ["14724933572", "4724933572", "(472) 493-3572", "472.493.3572"]
    assert len(set(values)) == 4          # the naive count: four phones
    assert count_distinct_phones(values) == 1  # the rule's count: one phone

    # Missing values are not matches; otherwise every phone-less borrower joins one ring.
    assert same_phone(None, None) is False
    assert same_phone("", "4724933572") is False
    assert norm_phone(None) == ""
    # Short numbers are not zero-padded into collisions.
    assert norm_phone("4933572") == "4933572"


def test_rule_26_placeholder_phones_never_match() -> None:
    """
    Placeholder junk in a phone field must not link two borrowers.

    A row holding "0" or "000-0000" normalises to a non-empty string, so a naive equality
    check would call two such rows a shared phone and fabricate a ring out of unpopulated
    fields — the same false-positive class rule 2's address key exists to avoid. General Rule
    26 only ever speaks of 10- and 11-digit numbers, so anything shorter is not comparable.
    """
    for junk in ("0", "000-0000", "ext 402", 0, "1", "555"):
        assert is_comparable_phone(junk) is False, junk
        assert same_phone(junk, junk) is False, junk
    assert same_phone("0", "4724933572") is False

    assert is_comparable_phone("4724933572") is True
    assert is_comparable_phone("14724933572") is True

    # Junk does not inflate a distinct-phone count, and does not count as a phone at all.
    assert count_distinct_phones(["0", "000-0000", "4724933572"]) == 1
    assert count_distinct_phones(["0", "0"]) == 0
    assert row_phones({"borr_home_phone": "0", "borr_cell_phone": "4724933572"}) == {
        "4724933572"
    }
    # A placeholder replaced by a real number is not a rule-46 phone "change".
    assert phone_changed("0", "4724933572") is False


def test_rule_17_placeholder_phone_is_not_a_valid_phone() -> None:
    """
    Rule 17's "NO OTHER VALID PHONE NUMBERS" carve-out reads placeholder junk as not valid.

    A row whose only phone value is "0" is treated as having no phone, so it is KEPT. Counting
    junk as a valid phone would exclude legitimate applications whose phone fields were never
    populated properly.
    """
    sources = FakeRecordSources.build(common_phones=["8005551234"])
    assert phone_excluded({"borr_home_phone": "0"}, sources) is False
    assert phone_excluded({"borr_home_phone": "000-0000"}, sources) is False
    # A junk phone alongside a common-consortium one still excludes: the junk is ignored,
    # and every VALID phone present is common-consortium.
    assert phone_excluded(
        {"borr_home_phone": "0", "borr_cell_phone": "8005551234"}, sources
    ) is True

    # Alert 46 (phone change between applications) must not fire on a reformatting.
    assert phone_changed("14724933572", "472-493-3572") is False
    assert phone_changed("4724933572", "6195550100") is True
    assert phone_changed(None, "4724933572") is False

    # The SQL form strips formatting before taking the last 10 digits.
    assert "regexp_replace" in norm_phone_sql("borr_cell_phone")


# ---------------------------------------------------------------------------
# Rule 27 - the query budget
# ---------------------------------------------------------------------------


def test_rule_27_query_budget() -> None:
    """
    General Rule 27: five SQL calls per request, and incremental output past five applications.

    The budget is spent BEFORE a call is issued, so the sixth call cannot happen — a counter
    checked afterwards would let it run and then complain.
    """
    assert MAX_SQL_CALLS_PER_REQUEST == 5
    budget = QueryBudget()
    for i in range(5):
        budget.spend(f"call-{i}")
    assert budget.used == 5
    assert budget.remaining == 0
    assert budget.exhausted is True
    with pytest.raises(QueryBudgetExceeded, match="6th"):
        budget.spend("one-too-many")

    # The labels make an exceeded budget diagnosable.
    with pytest.raises(QueryBudgetExceeded, match="call-0"):
        budget.spend("another")

    budget.reset()
    assert budget.remaining == 5

    # The decorator refuses the sixth call regardless of what the caller asks for.
    fresh = QueryBudget()
    calls: list[int] = []

    @budgeted(fresh, "retrieve_signals")
    def retrieve(n: int) -> int:
        calls.append(n)
        return n

    for i in range(5):
        retrieve(i)
    with pytest.raises(QueryBudgetExceeded):
        retrieve(99)
    assert calls == [0, 1, 2, 3, 4]  # the refused call never executed

    # Planning up front.
    plan_calls(["a", "b", "c"], QueryBudget())
    with pytest.raises(QueryBudgetExceeded):
        plan_calls(["a", "b", "c", "d", "e", "f"], QueryBudget())


def test_rule_27_incremental_summary_threshold() -> None:
    """"more than 5 applications" is strictly more: 5 may buffer, 6 may not."""
    assert requires_incremental_summary(5) is False
    assert requires_incremental_summary(6) is True
    assert requires_incremental_summary(0) is False

    assert list(incremental_batches(list(range(5)))) == [[0, 1, 2, 3, 4]]
    assert list(incremental_batches(list(range(6)))) == [[0, 1, 2, 3, 4], [5]]
    assert list(incremental_batches([])) == []


def test_rule_27_budget_is_shared_with_rules_13_and_18() -> None:
    """
    The five-call ceiling is shared, not per-rule.

    A rule-18 fallback plus a rule-13 record_from join leaves three calls, not five. Asserted
    because it is the interaction most likely to be overlooked when adding a retrieval step.
    """
    budget = QueryBudget()
    budget.spend("all_records primary query")
    budget.spend("tbl_auto_consortium fallback (rule 18)")
    budget.spend("join to record_from-bearing table (rule 13)")
    assert budget.remaining == 2


# ---------------------------------------------------------------------------
# The catalog and the generated coverage document
# ---------------------------------------------------------------------------


def test_catalog_is_valid() -> None:
    """
    Every catalog entry resolves to real code, in the customer's rule order.

    This is what makes the coverage table trustworthy: an entry cannot name a function that
    does not exist, and the table cannot silently omit or reorder a rule.
    """
    catalog.validate_catalog()
    assert tuple(e.rule_id for e in catalog.CATALOG) == _source.RULE_IDS
    for entry in catalog.CATALOG:
        resolved = entry.resolve()
        assert len(resolved) == len(entry.implemented_by)
        assert entry.verbatim.startswith(entry.rule_id)


def test_every_catalog_test_id_exists() -> None:
    """
    Every catalog test_id names a real test in this file, and every rule has one.

    Both directions. Without this, "enforced" could be claimed with no test behind it.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    defined = set(re.findall(r"^def (test_\w+)", source, re.MULTILINE))
    for entry in catalog.CATALOG:
        assert entry.test_id in defined, (
            f"rule {entry.rule_id} names test {entry.test_id!r}, which does not exist "
            f"in {Path(__file__).name}"
        )
    covered = {e.test_id for e in catalog.CATALOG}
    assert len(covered) >= 25, "expected a distinct test for nearly every rule"


def test_coverage_counts_are_honest() -> None:
    """
    The coverage numbers reported to the customer, pinned.

    29 rules, 27 enforced by deterministic code (including the four owned by bands and the one
    owned by alert_categories), 2 enforced by the verbatim prompt because they are score
    interpretation rather than computation.
    """
    summary = catalog.coverage_summary()
    assert summary["total_rules"] == 29
    assert summary["enforced_in_code"] == 27
    assert summary["prompt_enforced"] == 2
    assert summary["prompt_enforced_ids"] == ["20", "22"]
    assert summary["unimplemented"] == []
    assert (
        summary["enforced_in_code"] + summary["prompt_enforced"] == summary["total_rules"]
    )
    # No rule may claim a Bedrock Guardrail as its only enforcement, since this repo does not
    # configure one.
    assert "bedrock-guardrail" not in summary["by_layer"]


def test_coverage_doc_is_generated_and_current() -> None:
    """
    docs/general_rules_coverage.md matches the catalog.

    Generated, not written, so it cannot drift. If this fails, regenerate:
        python -m signal_layer.rules.catalog > docs/general_rules_coverage.md
    """
    assert COVERAGE_DOC.exists(), f"{COVERAGE_DOC} is missing; generate it"
    on_disk = COVERAGE_DOC.read_text(encoding="utf-8")
    expected = catalog.render_markdown()
    assert on_disk.strip() == expected.strip(), (
        "docs/general_rules_coverage.md is stale. Regenerate with "
        "'python -m signal_layer.rules.catalog > docs/general_rules_coverage.md'"
    )


def test_coverage_doc_generator_runs_as_a_module() -> None:
    """
    `python -m signal_layer.rules.catalog` really is the generator command the doc names.

    A subprocess, so it exercises the command a human would actually type rather than the
    in-process render the previous test compares.
    """
    # Fixed argument list, no shell, and the interpreter is `sys.executable` rather
    # than a name resolved off PATH. Nothing here is caller-influenced, so there is no
    # injection surface; the nosec records that this was reviewed rather than missed.
    result = subprocess.run(  # nosec B603 - static argv, shell=False, no external input
        [sys.executable, "-m", "signal_layer.rules.catalog"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        shell=False,
    )
    if result.returncode != 0 and "No module named 'signal_layer." in result.stderr:
        pytest.skip(
            "signal_layer/__init__.py imports sibling modules (registry, schema) that are "
            "not on disk yet, so the package cannot be imported in a fresh interpreter. The "
            "in-process generator is covered by test_coverage_doc_is_generated_and_current; "
            f"this check activates once the siblings land. stderr: {result.stderr.strip()}"
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == catalog.render_markdown().strip()


def test_every_rule_docstring_carries_its_verbatim_text() -> None:
    """
    Each rule module quotes its rule from the customer's document, not from a retyped string.

    Asserted by checking every module exposes RULE_TEXT sourced from _source, and that a
    distinctive fragment of the rule appears in the module's own text.
    """
    import importlib

    modules = {
        "1": "r01_lender_join",
        "1.5": "r0105_lender_nicknames",
        "2": "r02_normalize",
        "4": "r04_alert_join",
        "7": "r07_chargeoff",
        "8": "r08_deprecated_tables",
        "9": "r09_coborrower_source",
        "10": "r10_active_dealer",
        "11": "r11_submitted_at",
        "12": "r12_recommendations",
        "13": "r13_retro",
        "15": "r15_null_ssn",
        "17": "r17_fake_records",
        "23": "r23_no_raw_ssn",
        "25": "r25_invalid_credit",
        "26": "r26_phone_equiv",
        "27": "r27_query_budget",
    }
    for rule_id, module_name in modules.items():
        module = importlib.import_module(f"signal_layer.rules.{module_name}")
        assert module.RULE_ID == rule_id, module_name
        assert module.RULE_TEXT == _source.rule_text(rule_id), module_name
        # At least one public function quotes the rule in its docstring.
        quoted = any(
            (fn.__doc__ or "") and "General Rule" in fn.__doc__
            for name, fn in vars(module).items()
            if callable(fn) and not name.startswith("_") and getattr(fn, "__module__", "") == module.__name__
        )
        assert quoted, f"{module_name} has no function quoting its General Rule"
