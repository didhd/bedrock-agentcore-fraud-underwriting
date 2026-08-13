"""What can and cannot be computed from a raw application table.

These numbers go into a customer conversation, so a test keeps them honest. The classification
lives in `signal_layer/derive/contract.py`; this pins it.

The important assertion is `test_the_supplied_signals_are_named_individually`: "we cannot compute
this one" has to be a statement about a specific signal with a reason, never a guess from its
spelling. A pattern rule would classify `dealer_consortium_epd_rate_last_24mo` as an aggregation
because it ends in a window, and it is not one.
"""

from __future__ import annotations

import pytest

from signal_layer.derive.contract import (
    COLUMN_CONTRACT,
    NEEDS_CONFIRMATION,
    NOT_DERIVABLE,
    Origin,
    coverage,
    origin_of,
)
from signal_layer.registry import SIGNAL_NAMES


class TestCoverage:
    """Measured on the registry as it stands. A change here is a real change."""

    def test_headline_numbers(self) -> None:
        c = coverage()
        assert c["registry_signals"] == 60
        assert c["derivable"] == 53
        assert c["must_be_supplied"] == 7
        assert c["derivable"] + c["must_be_supplied"] == c["registry_signals"]

    def test_every_signal_is_classified(self) -> None:
        for signal in SIGNAL_NAMES:
            assert isinstance(origin_of(signal), Origin)

    def test_the_column_contract_is_short_enough_to_ask_for(self) -> None:
        # The point of the contract is that it turns "send us your schema" into a list someone
        # can answer in one message. If it grows past ~20 roles it has stopped being that.
        required = [r for r in COLUMN_CONTRACT if not r.optional]
        assert len(required) == 14
        assert len(COLUMN_CONTRACT) <= 20

    def test_every_requirement_says_what_breaks_without_it(self) -> None:
        for requirement in COLUMN_CONTRACT:
            assert requirement.needed_for, requirement.role
            assert len(requirement.description) > 10, requirement.role


class TestNotDerivable:
    def test_the_supplied_signals_are_named_individually(self) -> None:
        """Each carries a reason, and the reason is about that signal.

        `NOT_DERIVABLE` is consulted before any pattern rule for exactly this reason.
        """
        for signal, (origin, reason) in NOT_DERIVABLE.items():
            assert signal in SIGNAL_NAMES, f"{signal} is not a registry signal"
            assert origin in (Origin.MODEL, Origin.REFERENCE, Origin.BUREAU)
            assert len(reason) > 60, f"{signal}: the reason is too thin to act on"

    def test_a_window_suffix_does_not_make_something_an_aggregation(self) -> None:
        # The specific misclassification this ordering prevents.
        assert origin_of("dealer_consortium_epd_rate_last_24mo") is Origin.MODEL

    def test_the_two_dealer_scores_are_both_model_outputs(self) -> None:
        # Their rule 21 keys them on different identifiers and neither is an aggregation over
        # one lender's applications.
        assert origin_of("dealer_consortium_score") is Origin.MODEL
        assert origin_of("dealer_risk_score") is Origin.MODEL

    def test_nothing_supplied_is_also_claimed_derivable(self) -> None:
        c = coverage()
        assert not set(c["supplied_signals"]) & set(c["derivable_signals"])


class TestNeedsConfirmation:
    def test_every_entry_is_a_real_signal_with_a_real_question(self) -> None:
        for signal, question in NEEDS_CONFIRMATION.items():
            assert signal in SIGNAL_NAMES, signal
            assert "?" in question or "not" in question, (
                f"{signal}: an entry here must state the AMBIGUITY, not just that it is hard"
            )

    def test_the_velocity_windows_are_flagged(self) -> None:
        # A wrong window produces a plausible number and moves a fraud band silently, which is
        # worse than an error. These must never be quietly assumed.
        assert "ssn_apps_last_7d" in NEEDS_CONFIRMATION
        assert "ssn_apps_last_30d" in NEEDS_CONFIRMATION

    def test_a_flagged_signal_with_no_customer_threshold_says_so(self) -> None:
        """`ssn_apps_last_7d` has an EMPTY interpretation in the registry.

        So there is no customer sentence to read a threshold off, and the entry has to say that
        rather than leaving someone to discover it.
        """
        from signal_layer.registry import SIGNAL_SPEC

        assert not (SIGNAL_SPEC["ssn_apps_last_7d"].interpretation or "").strip()
        assert "no threshold sentence" in NEEDS_CONFIRMATION["ssn_apps_last_7d"]


def test_the_contract_names_no_customer_column() -> None:
    """The derivations are written against LOGICAL roles; their schema stays out of git.

    Checked against the semantic model's real column names when it is available, for the same
    reason `tools/usecases` is: this file is committed and their schema is not.
    """
    import pathlib

    from signal_layer.semantic import available, load_model

    if not available():
        pytest.skip("cannot derive the token list without the semantic model")

    model = load_model()
    # EXCLUDE our own vocabulary. Their schema and ours overlap on obvious generic names --
    # application_id, credit_score, employer_name -- and several registry SIGNAL names are also
    # column names there. Those are not leaks: they are already in committed code and were not
    # learned from the customer. A real leak is a distinctive compound identifier that could only
    # have come from their schema.
    ours = {n.lower() for n in SIGNAL_NAMES} | {r.role.lower() for r in COLUMN_CONTRACT}
    tokens = {
        column.name.lower()
        for table in model.tables.values()
        for column in list(table.dimensions) + list(table.facts)
        if len(column.name) >= 14 and column.name.lower() not in ours
    }
    source = (
        pathlib.Path(__file__).resolve().parents[1] / "signal_layer" / "derive" / "contract.py"
    ).read_text().lower()
    # Blank out OUR OWN signal names before scanning. Several of their column names are
    # SUBSTRINGS of ours -- `consortium_epd_rate` sits inside the registry signal
    # `dealer_consortium_epd_rate_last_24mo` -- so a plain substring search reports our own
    # vocabulary as a leak and the test stops meaning anything.
    for name in sorted(SIGNAL_NAMES, key=len, reverse=True):
        source = source.replace(name.lower(), " ")

    leaked = sorted(t for t in tokens if t in source)
    assert not leaked, f"customer column names in a committed file: {leaked[:8]}"
