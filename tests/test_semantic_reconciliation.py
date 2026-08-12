"""The customer's authoritative rules vs. our implementation.

`signal_layer/rules/` was written from the nine agent prompts, before the semantic model
existed. The model's `module_custom_instructions.sql_generation` is the authoritative list, so
these tests pin the agreement found on 2026-08-12: every numeric set and boundary matched, on
all 25 rules.

They exist to catch DRIFT IN EITHER DIRECTION. If a later delivery changes a rule, or if someone
edits our bands or alert categories, one of these fails and names what moved. Findings are
recorded in `docs/semantic-model-reconciliation.md`.

No customer rule text or column name appears here. Assertions are on counts, sets of integers,
and numeric boundaries.
"""

from __future__ import annotations

import re

import pytest

from signal_layer.alert_categories import CATEGORY_ALERTS
from signal_layer.bands import FRAUD_SCORE_BANDS
from signal_layer.semantic import available, load_model

pytestmark = pytest.mark.skipif(
    not available(),
    reason="the semantic model is customer confidential and gitignored; absent in a clone",
)


@pytest.fixture(scope="module")
def rules() -> dict[str, str]:
    return {r.number: r.text for r in load_model().sql_generation_rules}


@pytest.fixture(scope="module")
def customer_categories(rules) -> dict[str, set[int]]:
    """Rule 3's category → alert-id sets, parsed out of the rule text.

    Parsed rather than transcribed, so this test cannot drift from the delivered file and does
    not itself become a place where customer data is committed.
    """
    parsed: dict[str, set[int]] = {}
    for line in rules["3"].split("\n"):
        match = re.match(r"\s*([A-Za-z][A-Za-z \-]*?)\s*Alerts?\s*:\s*([\d,\s]+)", line)
        if match:
            parsed[match.group(1).strip()] = {int(x) for x in re.findall(r"\d+", match.group(2))}
    return parsed


#: Their category label -> ours. Only spelling differs; the sets are identical.
_LABELS = {"Identity-related": "Identity"}


class TestAlertCategories:
    """Rule 3. A mismatch here silently changes which specialist owns which alert."""

    def test_ten_categories_on_both_sides(self, customer_categories) -> None:
        assert len(customer_categories) == 10
        assert len(CATEGORY_ALERTS) == 10

    def test_every_category_matches_member_for_member(self, customer_categories) -> None:
        problems = []
        for label, expected in customer_categories.items():
            ours = set(CATEGORY_ALERTS.get(_LABELS.get(label, label), []))
            if ours != expected:
                problems.append(
                    f"{label}: {len(expected - ours)} of theirs missing, "
                    f"{len(ours - expected)} of ours extra"
                )
        assert not problems, "; ".join(problems)

    def test_distinct_id_count_is_148(self, customer_categories) -> None:
        # Settles a contradiction in CLAUDE.md, which said 166 in one place and 148 in another.
        # 166 is the MEMBERSHIP count: 18 ids belong to more than one category.
        distinct = set().union(*customer_categories.values())
        assert len(distinct) == 148
        memberships = sum(len(v) for v in customer_categories.values())
        assert memberships == 166
        assert memberships - len(distinct) == 18

    def test_our_distinct_count_agrees(self) -> None:
        ours = set().union(*(set(v) for v in CATEGORY_ALERTS.values()))
        assert len(ours) == 148


class TestFraudScoreBands:
    """Rule 6. A boundary off by one changes every adjudication in that band."""

    def test_boundaries_match_the_rule(self, rules) -> None:
        text = " ".join(rules["6"].split())
        # Pull the numbers out of their sentence rather than trusting a transcription.
        ranges = [(int(a), int(b)) for a, b in re.findall(r"(\d+)\s*-\s*(\d+)", text)]
        assert (0, 299) in ranges
        assert (300, 699) in ranges
        assert (700, 998) in ranges
        assert FRAUD_SCORE_BANDS["low"] == (0, 299)
        assert FRAUD_SCORE_BANDS["medium"] == (300, 699)
        assert FRAUD_SCORE_BANDS["high"] == (700, 998)

    def test_999_is_its_own_band(self, rules) -> None:
        # Not a boundary but a signal: it means an accelerator fired. Folding it into `high`
        # would lose the customer's strongest single indicator.
        assert FRAUD_SCORE_BANDS["very high"] == (999, 999)
        assert "999" in rules["6"]

    def test_the_bands_are_contiguous_and_cover_0_to_999(self) -> None:
        ordered = sorted(FRAUD_SCORE_BANDS.values())
        assert ordered[0][0] == 0
        assert ordered[-1][1] == 999
        for (_, end), (start, _) in zip(ordered, ordered[1:]):
            assert start == end + 1, f"gap or overlap at {end}/{start}"


class TestRuleCoverage:
    def test_twenty_five_rules_numbered_without_gaps(self, rules) -> None:
        assert sorted(rules, key=lambda n: float(n)) == [str(n) for n in range(1, 26)]

    @pytest.mark.parametrize("number", [str(n) for n in range(1, 26)])
    def test_every_rule_has_substantial_text(self, rules, number) -> None:
        # A rule parsed down to a fragment would pass a "present" check while enforcing nothing.
        assert len(rules[number].strip()) > 40, f"rule {number} parsed too short"

    def test_rule_one_absorbed_its_sub_point(self, rules) -> None:
        assert "1.5" in rules["1"]


class TestOpenConflict:
    """Rule 12 vs the customer's own adjudication contract. Documented, not resolved."""

    def test_the_contract_still_offers_a_decline_value(self, rules) -> None:
        from typing import get_args

        from agents.contracts import Adjudication

        values = get_args(Adjudication.model_fields["recommendation_decision"].annotation)
        assert any("DECLINE" in v for v in values), (
            "the contract no longer offers DECLINE. If that was deliberate, update "
            "docs/semantic-model-reconciliation.md -- it records this as an OPEN question for "
            "the customer, not a decision for us."
        )
        # Their rule 12 is about what an agent RECOMMENDS in prose; the enum is the structured
        # field. Both are the customer's artefacts and the conflict is theirs to settle.
        assert "decline" in rules["12"].lower()
