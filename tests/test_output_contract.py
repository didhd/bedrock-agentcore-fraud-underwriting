"""
Tests for the output-contract checker.

The checker exists because the tier-quality sweep found that 24 of 24 bustout and rings
analyses invented a title their prompt never defines, across all three model tiers. These
tests pin that detection, and — more importantly — pin the fact that the eight prompts do
NOT share one contract. An earlier revision assumed six agents banned risk labels in
headers; only four do. Over-enforcing would report violations the customer never asked
for, which is its own kind of infidelity.

Every rule assertion below is checked against the verbatim prompt text rather than against
a table in the test, so a customer prompt update moves both together.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents import output_contract as oc
from agents.fanout import ANALYSIS_TITLES
from prompts.loader import DOMAINS, SPECIALIST_PROMPTS

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- the contract is derived from her text, not declared here ------------------


def test_titles_agree_with_the_fanout_contract():
    """Two modules name the titles; they must not drift apart."""
    assert oc.TITLES == dict(ANALYSIS_TITLES)


def test_every_stated_title_really_appears_in_its_prompt():
    for domain, title in oc.TITLES.items():
        if title is None:
            continue
        assert title in SPECIALIST_PROMPTS[domain], domain


def test_bustout_and_rings_define_no_title_and_say_so():
    """Their prompts end with 'Return only the final analysis', not with a Title line."""
    for domain in ("bustout", "rings"):
        assert oc.TITLES[domain] is None
        assert "Title:" not in SPECIALIST_PROMPTS[domain]
        assert "return only" in SPECIALIST_PROMPTS[domain].lower()


def test_the_no_risk_labels_rule_is_in_exactly_four_prompts():
    """MEASURED CORRECTION: four, not six.

    identity, dealer, straw and employment carry the sentence; income and synthetic never
    received it, and bustout/rings are unconstrained. Enforcing it on all eight would
    invent a rule.
    """
    with_rule = {d for d in DOMAINS if oc.RULES[d]["no_risk_labels_in_headers"]}
    assert with_rule == {"identity", "dealer", "straw", "employment"}


def test_bustout_and_rings_are_unconstrained_on_every_rule():
    for domain in ("bustout", "rings"):
        assert not any(oc.RULES[domain].values()), oc.RULES[domain]
        report = oc.check(domain, "anything at all")
        assert set(report.not_applicable) == set(oc.RULES[domain])


def test_the_six_titled_agents_all_carry_the_4000_char_cap():
    capped = {d for d in DOMAINS if oc.RULES[d]["char_cap"]}
    titled = {d for d, t in oc.TITLES.items() if t is not None}
    assert capped == titled


# --- detection ----------------------------------------------------------------


def test_an_invented_title_on_rings_is_flagged():
    report = oc.check("rings", "# Fraud Ring Risk Analysis\n\nThe signals are explainable.")
    assert [v.rule for v in report.violations] == ["title_invented"]
    assert "Return only the final analysis." in report.violations[0].prompt_basis


@pytest.mark.parametrize(
    "first_line",
    [
        "# FRAUD RING DETECTION ANALYSIS",
        "## Fraud Ring Risk Analysis: HIGH RISK",
        "# RINGS FRAUD RISK ANALYSIS",
        "## Fraud Ring Risk Analysis — Application (hash_ssn_5c8b17)",
        "Fraud Ring Risk Analysis",
    ],
)
def test_every_invented_title_spelling_observed_live_is_caught(first_line):
    """These are the real opening lines from the measured sweep, not invented examples."""
    report = oc.check("rings", f"{first_line}\n\nBody text follows here.")
    assert "title_invented" in {v.rule for v in report.violations}, first_line


def test_a_genuine_opening_sentence_is_not_mistaken_for_a_title():
    """The checker must not manufacture a violation out of ordinary prose."""
    body = (
        "Shared identifiers on this application are consistent with a dense multi-unit "
        "address and do not indicate coordinated activity."
    )
    assert oc.check("rings", body).ok


def test_a_missing_title_on_a_titled_agent_is_flagged():
    report = oc.check("identity", "The identity signals are isolated and explainable.")
    assert "title_missing" in {v.rule for v in report.violations}


def test_the_correct_title_on_a_titled_agent_is_clean():
    text = "Identity Fraud Risk Analysis\n\nSignals are isolated and explainable."
    assert oc.check("identity", text).ok


def test_char_cap_is_only_enforced_where_the_prompt_states_it():
    long_text = "Identity Fraud Risk Analysis\n\n" + ("x" * (oc.CHAR_CAP + 1))
    assert "char_cap_exceeded" in {v.rule for v in oc.check("identity", long_text).violations}
    # rings has no cap, so the same length is not a violation there
    assert "char_cap_exceeded" not in {
        v.rule for v in oc.check("rings", "x" * (oc.CHAR_CAP + 1)).violations
    }


def test_alert_numbers_are_flagged_only_where_forbidden():
    text = "Identity Fraud Risk Analysis\n\nAlert 44 fired on this application."
    assert "alert_number_cited" in {v.rule for v in oc.check("identity", text).violations}
    assert "alert_number_cited" not in {
        v.rule for v in oc.check("rings", "Alert 44 fired.").violations
    }


def test_a_risk_label_in_a_heading_is_flagged_for_the_four_agents_that_ban_it():
    text = "Identity Fraud Risk Analysis\n\n## Findings — HIGH RISK\n\nBody."
    assert "risk_label_in_header" in {v.rule for v in oc.check("identity", text).violations}
    # income never received that sentence
    income = "Income Fraud Risk Analysis\n\n## Findings — HIGH RISK\n\nBody."
    assert "risk_label_in_header" not in {v.rule for v in oc.check("income", income).violations}


def test_an_emoji_is_flagged_where_banned_and_a_dash_is_not():
    banned = "Identity Fraud Risk Analysis\n\nClean \U0001f600"
    assert "emoji_present" in {v.rule for v in oc.check("identity", banned).violations}
    fine = "Identity Fraud Risk Analysis\n\nClean — nothing material, per the context field."
    assert "emoji_present" not in {v.rule for v in oc.check("identity", fine).violations}


def test_an_identifier_in_a_heading_is_reported_as_an_observation():
    report = oc.check("rings", "# Fraud Ring Risk Analysis — Application (hash_ssn_ff02aa)\n\nBody.")
    rules = {v.rule for v in report.violations}
    assert "identifier_in_heading" in rules
    basis = next(v.prompt_basis for v in report.violations if v.rule == "identifier_in_heading")
    assert "not a stated prompt rule" in basis


def test_an_unknown_domain_raises_rather_than_passing_silently():
    with pytest.raises(KeyError):
        oc.check("not_a_domain", "text")


# --- stripping is presentation, never a fix -----------------------------------


def test_stripping_removes_an_invented_title_but_leaves_a_defined_one():
    stripped = oc.strip_invented_title("rings", "# Fraud Ring Risk Analysis\n\nBody text.")
    assert stripped.startswith("Body text.")
    keep = "Identity Fraud Risk Analysis\n\nBody text."
    assert oc.strip_invented_title("identity", keep) == keep


def test_stripping_does_not_suppress_the_violation():
    """The report must still name it: the customer needs to know."""
    text = "# Fraud Ring Risk Analysis\n\nBody."
    assert not oc.check("rings", text).ok
    oc.strip_invented_title("rings", text)
    assert not oc.check("rings", text).ok


# --- against the real measured corpus -----------------------------------------

_CORPUS = REPO_ROOT / "evals" / "results" / "tier_quality_analyses.json"


@pytest.mark.skipif(not _CORPUS.is_file(), reason="tier-quality corpus not present")
def test_the_measured_corpus_reproduces_the_24_of_24_invented_titles():
    """Regression against real data: every bustout/rings analysis invented a title.

    This is the finding the module exists for, so it is asserted against the actual
    measured analyses rather than against a fixture written to agree with the code.
    """
    rows = json.loads(_CORPUS.read_text())
    rows = rows if isinstance(rows, list) else rows.get("analyses", [])
    untitled = [r for r in rows if r.get("domain") in ("bustout", "rings")]
    assert untitled, "corpus carries no bustout/rings analyses"
    flagged = [
        r
        for r in untitled
        if "title_invented" in {v.rule for v in oc.check(r["domain"], r.get("text") or "").violations}
    ]
    assert len(flagged) == len(untitled), (
        f"{len(flagged)}/{len(untitled)} flagged; the measured sweep found 24 of 24 "
        "inventing a title, so anything less means the checker regressed"
    )


@pytest.mark.skipif(not _CORPUS.is_file(), reason="tier-quality corpus not present")
def test_summarize_reports_both_clean_and_violating_domains():
    rows = json.loads(_CORPUS.read_text())
    rows = rows if isinstance(rows, list) else rows.get("analyses", [])
    analyses = {r["domain"]: (r.get("text") or "") for r in rows if r.get("domain") in DOMAINS}
    summary = oc.summarize(oc.check_all(analyses))
    assert set(summary["domains_checked"]) <= set(DOMAINS)
    assert summary["total_violations"] >= 1
    assert set(summary["domains_clean"]).isdisjoint(summary["domains_violating"])
