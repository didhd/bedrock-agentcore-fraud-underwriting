"""Guards for the risk-band parser and the application table's field lookup.

Both bugs these cover were SILENT. The band parser returned None for a complete,
correct analysis and the UI rendered an empty chip; the table lookup returned None for
three columns and the UI rendered em dashes -- which is this UI's honest marker for
"not measured", so the output looked deliberate. Neither raised, neither failed a test,
and both were found only by reading live output.

Every string below is taken from a real specialist response or a real fixture, not
invented, so a future "simplification" of either function has to explain the actual
data rather than a hypothetical.
"""

from __future__ import annotations

import json

import pytest

from app.runtime_support import parse_risk_band, risk_band_form
from fixtures.loader import list_application_ids

# ---------------------------------------------------------------------------
# The band parser
# ---------------------------------------------------------------------------

#: Verbatim from a live run of the employment specialist (2,579 characters). Its band
#: came back None before the labelled form was accepted.
LIVE_EMPLOYMENT = (
    "# Employment Fraud Risk Analysis\n\n"
    "**Risk Level: HIGH**\n\n"
    "This application presents strong, corroborated evidence of a fabricated employer "
    "combined with coordinated application activity, rather than an isolated data gap."
)

#: Verbatim from the same run's identity specialist. It mentions HIGH twice -- once
#: about a credit score, once about a signal category -- and never classifies itself.
#: None is the CORRECT answer here, and this is the case that forbids loosening the
#: parser to match a bare adjective.
LIVE_IDENTITY_NO_BAND = (
    "# Identity Fraud Risk Analysis\n\n"
    "**Classification rationale: Multiple independent, corroborated identity "
    "manipulation indicators.**\n\n"
    "6 credit inquiries in 2 weeks paired with a high (771) score - a combination "
    "explicitly flagged as high-risk regardless of score quality."
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This is HIGH RISK because the SSN resolves to three identities.", "HIGH RISK"),
        ("Conclusion: LOW RISK. Context explains every signal.", "LOW RISK"),
        ("Assessed as POSSIBLE RISK pending verification.", "POSSIBLE RISK"),
    ],
)
def test_contract_phrase_is_recognised(text, expected):
    """The customer's own phrasing is the primary form and stays primary."""
    assert parse_risk_band(text) == expected
    assert risk_band_form(text) == "contract"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (LIVE_EMPLOYMENT, "HIGH RISK"),
        ("Risk: POSSIBLE\n\nThe employer could not be verified.", "POSSIBLE RISK"),
        ("Overall Risk Rating: LOW", "LOW RISK"),
        ("Risk Classification: **POSSIBLE**", "POSSIBLE RISK"),
        ("Risk Level - HIGH", "HIGH RISK"),
    ],
)
def test_labelled_form_is_recognised(text, expected):
    """A correct verdict in the wrong shape is still a verdict."""
    assert parse_risk_band(text) == expected
    assert risk_band_form(text) == "labelled"


def test_an_analysis_that_never_classifies_itself_returns_none():
    """None is a finding, not a parser failure, and must not become a guess."""
    assert parse_risk_band(LIVE_IDENTITY_NO_BAND) is None
    assert risk_band_form(LIVE_IDENTITY_NO_BAND) is None


def test_high_risk_prose_does_not_override_a_low_verdict():
    """The anti-false-positive case, and the reason the label is required.

    The customer's calibration expects a specialist to name the escalation it
    considered and then argue it away. A parser that matched a bare "high-risk"
    anywhere -- or that scanned in severity order rather than document order -- would
    report the opposite of what this analysis concludes.
    """
    text = (
        "The address overlap could indicate a HIGH-RISK coordinated ring, but context "
        "resolves all six SSNs to one household with nine years of tenure.\n\n"
        "Risk Level: LOW"
    )
    assert parse_risk_band(text) == "LOW RISK"


def test_severity_order_is_not_used():
    """First named wins, not most severe.

    ui/server.py once scanned HIGH -> POSSIBLE -> LOW and would return HIGH for this.
    Both parsers now share one implementation; this pins the semantics that survived.
    """
    text = "Conclusion: LOW RISK. A naive reading might have called this HIGH RISK."
    assert parse_risk_band(text) == "LOW RISK"


def test_ui_server_and_runtime_agree():
    """Two parsers that disagree is the defect; this fails if they diverge again."""
    ui_server = pytest.importorskip("ui.server")
    for text in (
        LIVE_EMPLOYMENT,
        LIVE_IDENTITY_NO_BAND,
        "Conclusion: LOW RISK. A naive reading might have called this HIGH RISK.",
        "Risk Classification: **POSSIBLE**",
    ):
        assert ui_server._parse_band(text) == parse_risk_band(text)


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_empty_input_is_none_not_an_exception(empty):
    assert parse_risk_band(empty) is None
    assert risk_band_form(empty) is None


# ---------------------------------------------------------------------------
# The application table's field lookup
# ---------------------------------------------------------------------------

#: Columns that were em dashes for all 14 fixtures because the lookup never searched
#: the record's ``application`` block. Every one of them is present in every fixture.
REQUIRED_TABLE_FIELDS = ("loan_amount", "vehicle", "fraud_score", "ltv_pct", "credit_score")


def test_application_table_columns_are_populated_for_every_fixture():
    """No fixture may render an em dash in a column whose value is in the record.

    An em dash is this UI's marker for "not measured", so a lookup bug here does not
    look like a bug -- it looks like missing data. That is precisely why it survived.
    """
    ui_server = pytest.importorskip("ui.server")
    rows = json.loads(ui_server.applications().body)
    assert len(rows) == len(list(list_application_ids()))

    missing: dict[str, list[str]] = {}
    for row in rows:
        for field in REQUIRED_TABLE_FIELDS:
            if row.get(field) is None:
                missing.setdefault(field, []).append(row["application_id"])
    assert not missing, f"null table fields: {missing}"


def test_fraud_score_and_dealer_score_are_not_the_same_field():
    """They come from different places and must not be conflated.

    ``fraud_score`` is on the application record; ``dealer_consortium_score`` is a
    signal. Reading one where the other was meant would put a plausible number in the
    wrong column, which is unreadable rather than obviously wrong.
    """
    ui_server = pytest.importorskip("ui.server")
    rows = {r["application_id"]: r for r in json.loads(ui_server.applications().body)}
    row = rows["APP-1001"]
    assert row["fraud_score"] == 142
    assert row["dealer_consortium_score"] == 310
    assert row["fraud_score"] != row["dealer_consortium_score"]


def test_the_application_block_wins_over_a_same_named_signal():
    """Precedence is application -> record -> signals, and the order is deliberate.

    A signal sharing a name with an application field is a derived quantity; the
    application block is the source record.
    """
    ui_server = pytest.importorskip("ui.server")
    record = {
        "application": {"fraud_score": 142},
        "signals": {"fraud_score": 999},
    }
    assert ui_server._application_field(record, "fraud_score") == 142


def test_a_flat_legacy_record_still_resolves():
    """data/applications.py is flat. Fixing the nested shape must not break it."""
    ui_server = pytest.importorskip("ui.server")
    assert ui_server._application_field({"loan_amount": 24500}, "loan_amount") == 24500
