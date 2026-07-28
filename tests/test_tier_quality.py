"""
Guards on the module that decides whether a cheaper model is good enough.

WHY THESE TESTS EXIST. ``evals/tier_quality.py`` is the only artifact that can turn
"Opus 5 on rings costs 3.6x Sonnet" into "so drop it". A recommendation to change the
model behind a fraud decision is exactly the kind of claim that must not rest on a
metric that quietly agrees with whoever wrote it, so the failure modes below are the
ones that would make this file lie persuasively:

  1. **A rule enforced against an agent whose prompt never states it.** bustout and
     rings define no title, no length cap, no emoji ban and no alert-number ban. A
     checker that applied the six-agent contract to all eight would report violations
     the customer's prompt does not contain, and would make those two agents look
     worse on every model. Every rule is therefore read out of the prompt text
     (:func:`rule_applies`) and the tests pin which agents each rule covers - notably
     that the header ban is in FOUR prompts, not six.

  2. **A band read out of the prompt's own calibration text instead of the analysis's
     verdict.** Every specialist prompt contains the string "LOW RISK", so any
     analysis that quotes its own instructions back would read as LOW. The extractor
     must find the *declaration* and must return None rather than guess.

  3. **Grounding satisfied by copying.** The signal values are already in the
     payload's signals block, so an analysis can restate them without ever looking at
     the ``_context`` row it was told to validate against. Grounding must count only
     tokens unique to the context row.

  4. **A cost or latency invented for an unmeasured cell.** A model swap changes
     output length, so a cost computed at another model's token counts is fiction.
     Rows without measured usage must contribute nothing.

  5. **A percentile printed below the sample count that supports it**, which is
     already policy in ``evals.bench`` and must not be re-implemented laxly here.

Offline only: no Bedrock call, no AWS credentials, no network.

Run:  MOCK_MODE=1 python -m pytest tests/test_tier_quality.py -q
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals import bench  # noqa: E402
from evals import tier_quality as tq  # noqa: E402
from prompts.loader import DOMAINS, SPECIALIST_PROMPTS  # noqa: E402

RESULT_PATH = REPO_ROOT / "evals" / "results" / "tier_quality.json"
UNTITLED = ("bustout", "rings")


# ---------------------------------------------------------------------------
# 1. Rules are read from the customer's prompts, never hard-coded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain", UNTITLED)
@pytest.mark.parametrize("rule", sorted(tq.RULE_MARKERS))
def test_no_rule_is_enforced_against_bustout_or_rings(domain: str, rule: str) -> None:
    """Their prompts say only "Return only the final analysis." - nothing else applies."""
    assert not tq.rule_applies(domain, rule), (
        f"{domain} does not state {rule} in its verbatim prompt; enforcing it would "
        f"invent a contract the customer never wrote"
    )


def test_the_header_ban_covers_four_agents_not_six() -> None:
    """income and synthetic never received the "no risk labels in headers" sentence.

    This is the correction the whole contract layer turns on: it is easy to assume the
    rule tracks the six agents that define a title, and it does not.
    """
    covered = [d for d in DOMAINS if tq.rule_applies(d, "no_risk_label_in_headers")]
    assert covered == ["identity", "dealer", "straw", "employment"], covered
    for domain in ("income", "synthetic"):
        assert "risk classification labels" not in SPECIALIST_PROMPTS[domain]


@pytest.mark.parametrize(
    "rule", ["no_alert_numbers", "no_emoji", "exact_title", "max_4000_chars"]
)
def test_the_six_agent_rules_cover_exactly_the_six_titled_agents(rule: str) -> None:
    covered = {d for d in DOMAINS if tq.rule_applies(d, rule)}
    assert covered == set(DOMAINS) - set(UNTITLED), (rule, sorted(covered))


def test_every_rule_quotes_the_prompt_line_it_is_measured_against() -> None:
    """A finding a reader cannot check against the prompt is an opinion."""
    for rule in tq.RULE_MARKERS:
        for domain in DOMAINS:
            if not tq.rule_applies(domain, rule):
                continue
            line = tq.rule_source_line(domain, rule)
            assert line, f"{domain}/{rule} has no source line"
            assert line in SPECIALIST_PROMPTS[domain].replace("\n", "\n"), line


def test_declared_titles_match_the_prompts_and_are_none_for_the_untitled_pair() -> None:
    assert tq.declared_title("identity") == "Identity Fraud Risk Analysis"
    assert tq.declared_title("synthetic") == "Synthetic Identity Fraud Risk Analysis"
    for domain in UNTITLED:
        assert tq.declared_title(domain) is None


# ---------------------------------------------------------------------------
# 2. Contract findings fire on real violations and stay quiet otherwise
# ---------------------------------------------------------------------------


def test_alert_numbers_are_flagged_for_a_covered_agent() -> None:
    findings = tq.contract_findings(
        "identity", "Identity Fraud Risk Analysis\n\nAlert 66 fired and Alert #4 too.", "LOW RISK"
    )
    assert "no_alert_numbers" in {f.rule for f in findings}


def test_alert_numbers_are_not_flagged_for_rings() -> None:
    """rings' prompt carries no such rule; flagging it would penalise it unfairly."""
    findings = tq.contract_findings("rings", "Alert 66 and Alert 13 fired.", "LOW RISK")
    assert "no_alert_numbers" not in {f.rule for f in findings}


def test_naming_a_finding_without_its_number_is_not_a_violation() -> None:
    findings = tq.contract_findings(
        "identity",
        "Identity Fraud Risk Analysis\n\nThe shared-phone finding is explained by the "
        "household. Two sentences suffice. Third sentence.",
        "LOW RISK",
    )
    assert "no_alert_numbers" not in {f.rule for f in findings}


def test_a_risk_band_in_a_header_is_flagged_but_in_body_prose_is_not() -> None:
    header = tq.contract_findings(
        "identity", "Identity Fraud Risk Analysis\n\n## HIGH RISK findings\n\nBody.", "HIGH RISK"
    )
    assert "no_risk_label_in_headers" in {f.rule for f in header}
    body = tq.contract_findings(
        "identity",
        "Identity Fraud Risk Analysis\n\nThe classification is HIGH RISK because two "
        "independent inconsistencies corroborate each other.",
        "HIGH RISK",
    )
    assert "no_risk_label_in_headers" not in {f.rule for f in body}


def test_a_wrong_title_is_flagged_and_the_exact_title_is_not() -> None:
    wrong = tq.contract_findings("synthetic", "# Synthetic Identity Fraud Analysis\n\nx.", "LOW RISK")
    assert "exact_title" in {f.rule for f in wrong}
    right = tq.contract_findings(
        "synthetic", "# Synthetic Identity Fraud Risk Analysis\n\nOne. Two. Three.", "LOW RISK"
    )
    assert "exact_title" not in {f.rule for f in right}


def test_an_invented_title_is_flagged_for_the_untitled_pair() -> None:
    findings = tq.contract_findings("rings", "# Fraud Ring Risk Analysis\n\nBody text.", "LOW RISK")
    assert "invented_title" in {f.rule for f in findings}


def test_the_untitled_pair_is_clean_when_it_returns_only_the_analysis() -> None:
    findings = tq.contract_findings(
        "rings",
        "Shared-identifier counts are explained by the 240-unit building and no "
        "exact-unit reuse is present, so this is LOW RISK.",
        "LOW RISK",
    )
    assert findings == []


def test_emoji_is_flagged_for_a_covered_agent_and_an_en_dash_is_not() -> None:
    emoji = tq.contract_findings("identity", "Identity Fraud Risk Analysis\n\nClear ✅", "LOW RISK")
    assert "no_emoji" in {f.rule for f in emoji}
    dash = tq.contract_findings(
        "identity", "Identity Fraud Risk Analysis\n\nClear — nothing corroborated.", "LOW RISK"
    )
    assert "no_emoji" not in {f.rule for f in dash}


def test_length_rules_are_skipped_when_the_band_could_not_be_read() -> None:
    """Guessing a band in order to apply a length rule would invent a violation."""
    long_text = "Identity Fraud Risk Analysis\n\n" + ("Sentence here. " * 40)
    findings = {f.rule for f in tq.contract_findings("identity", long_text, None)}
    assert "low_risk_3_to_5_sentences" not in findings
    assert "max_4000_chars" not in findings


def test_a_low_risk_bullet_wall_breaches_the_three_to_five_sentence_rule() -> None:
    text = "Identity Fraud Risk Analysis\n\n" + "\n".join(f"- finding {i}" for i in range(20))
    assert "low_risk_3_to_5_sentences" in {
        f.rule for f in tq.contract_findings("identity", text, "LOW RISK")
    }


def test_the_4000_character_cap_applies_only_above_low_risk() -> None:
    text = "Identity Fraud Risk Analysis\n\n" + ("word " * 1200)
    assert "max_4000_chars" in {f.rule for f in tq.contract_findings("identity", text, "HIGH RISK")}
    assert "max_4000_chars" not in {
        f.rule for f in tq.contract_findings("rings", text, "HIGH RISK")
    }


# ---------------------------------------------------------------------------
# 3. Band extraction: a declaration, never the prompt's own calibration text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("domain", DOMAINS)
def test_the_prompt_text_itself_never_reads_as_a_declared_band(domain: str) -> None:
    """Every prompt contains "LOW RISK". Echoing it back must not count as a verdict."""
    band, _ = tq.extract_declared_band(SPECIALIST_PROMPTS[domain])
    assert band is None, f"{domain}: calibration text was read as a verdict ({band})"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("RISK CLASSIFICATION: **HIGH RISK**", "HIGH RISK"),
        ("**Classification: LOW RISK**", "LOW RISK"),
        ("## Verdict: POSSIBLE RISK", "POSSIBLE RISK"),
        ("Multiple weak indicators -> LOW RISK.", "LOW RISK"),
        ("Overall: MEDIUM RISK", "POSSIBLE RISK"),
    ],
)
def test_declared_bands_are_read_from_a_declaration(text: str, expected: str) -> None:
    band, source = tq.extract_declared_band(text)
    assert band == expected
    assert source != "none"


def test_an_analysis_with_no_declaration_returns_none_rather_than_a_default() -> None:
    text = (
        "The shared phone is explained by the household and the address overlap is "
        "building-level, so nothing here is corroborated."
    )
    band, source = tq.extract_declared_band(text)
    assert band is None
    assert source == "none"


def test_a_row_with_no_readable_band_is_not_scored_as_agreeing_or_disagreeing() -> None:
    row = tq.AnalysisRow(
        app="APP-1009",
        domain="identity",
        model="global.anthropic.claude-sonnet-5",
        text="No classification is stated anywhere in this prose.",
    )
    evaluated = tq.evaluate_row(row, None)
    assert evaluated["declared_band"] is None
    assert evaluated["band_agrees"] is None


def test_medium_risk_maps_to_the_specialists_middle_band() -> None:
    assert tq.BAND_ALIASES["MEDIUM RISK"] == "POSSIBLE RISK"
    assert set(tq.BAND_ALIASES.values()) == {"LOW RISK", "POSSIBLE RISK", "HIGH RISK"}


# ---------------------------------------------------------------------------
# 4. Grounding must not be satisfiable by copying the signal block
# ---------------------------------------------------------------------------


def test_restating_the_signal_values_alone_does_not_count_as_grounded() -> None:
    """The signals are already in the payload; quoting them proves only copying."""
    from fixtures.loader import build_payload

    view = build_payload("APP-1009")["views"]["rings"]
    copied = json.dumps(view["signals"]) + " " + " ".join(view["signals"])
    grounding = tq.context_grounding("APP-1009", "rings", copied)
    assert grounding["signals_grounded_in_context"] == 0, grounding["grounded_signals"]


def test_citing_the_context_rows_own_evidence_does_count_as_grounded() -> None:
    from fixtures.loader import build_payload

    view = build_payload("APP-1009")["views"]["rings"]
    quoted = " ".join(str(c.get("example_rows", "")) for c in view["context"].values())
    grounding = tq.context_grounding("APP-1009", "rings", quoted)
    assert grounding["signals_grounded_in_context"] == grounding["signals_in_view"]
    assert grounding["grounded_fraction"] == 1.0


def test_grounding_of_empty_text_is_zero_not_an_error() -> None:
    grounding = tq.context_grounding("APP-1009", "rings", "")
    assert grounding["signals_grounded_in_context"] == 0
    assert grounding["grounded_fraction"] == 0.0


# ---------------------------------------------------------------------------
# 5. Density is a ratio, so a longer restatement scores lower
# ---------------------------------------------------------------------------


def test_repeating_the_same_findings_lowers_evidence_density() -> None:
    once = "phone_reuse_distinct_ssn_count is 5 across 4 surnames within 90 days."
    density_once = tq.evidence_density(once)
    density_thrice = tq.evidence_density(" ".join([once] * 3))
    assert density_thrice["evidence_items"] == density_once["evidence_items"]
    assert density_thrice["evidence_per_1k_chars"] < density_once["evidence_per_1k_chars"]


def test_adding_a_genuinely_new_fact_raises_the_evidence_count() -> None:
    base = tq.evidence_density("phone_reuse_distinct_ssn_count is 5.")
    more = tq.evidence_density(
        "phone_reuse_distinct_ssn_count is 5 and address_reuse_distinct_ssn_count is 96."
    )
    assert more["evidence_items"] > base["evidence_items"]


# ---------------------------------------------------------------------------
# 6. Blinding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tell", ["Claude", "haiku", "Sonnet 5", "OPUS", "anthropic", "gpt-5.6", "Bedrock"]
)
def test_model_identifiers_are_stripped_before_judging(tell: str) -> None:
    assert tell.lower() not in tq.anonymise(f"Written by {tell} for this application.").lower()


def test_anonymise_keeps_the_underwriting_content() -> None:
    text = "The 240-unit building explains 96 SSNs; only 1 is at the borrower's unit."
    assert tq.anonymise(text) == text


def test_the_blind_order_is_deterministic_and_a_permutation() -> None:
    rows = [
        tq.AnalysisRow(app=f"APP-10{i:02d}", domain="rings", model="m", text="t")
        for i in range(10)
    ]
    first = [r.app for r in tq.blind_order(rows)]
    assert first == [r.app for r in tq.blind_order(rows)]
    assert sorted(first) == sorted(r.app for r in rows)
    assert first != [r.app for r in rows], "a fixed seed must still shuffle"


def test_the_judge_scoring_its_own_family_is_declared_not_assumed_away() -> None:
    """The judge IS one of the candidates, so self-preference must be disclosed.

    The brief for this exercise described Opus 5 as "not a candidate for any
    specialist under test after this exercise". "After" is doing a lot of work
    there: during the sweep Opus 5 is one of the three tiers being measured, so the
    strongest-available judge is also grading its own analyses. That cannot be fixed
    by choosing a weaker judge, and it cannot be waved away either - so the module
    is required to carry the conflict in :data:`tier_quality.JUDGE_CONFLICT` and to
    publish it as a caveat, and :func:`tier_quality.judge_self_preference` has to
    quantify it from the scores actually given.
    """
    assert tq.JUDGE_MODEL in tq.CANDIDATE_MODELS
    assert "own" in tq.JUDGE_CONFLICT.lower() or "self" in tq.JUDGE_CONFLICT.lower()


def _judged(model: str, *, cal: int, sub: int, band: str) -> dict:
    return tq.evaluate_row(
        tq.AnalysisRow(app="APP-1009", domain="rings", model=model, text=f"Verdict: {band}."),
        {
            "verdict": {
                "band_read_from_analysis": band,
                "band_matches_expectation": band == "LOW RISK",
                "calibration_score": cal,
                "substance_score": sub,
                "false_positive_verdict": "Pass",
                "escalation_drift": "aligned",
                "padding_observed": False,
                "cites_context_fields": True,
                "contract_notes": "",
                "justification": "x",
            }
        },
    )


def test_self_preference_is_measured_and_cross_checked_without_the_judge() -> None:
    """A judge-only margin with no deterministic margin behind it is preference."""
    rows = [
        _judged("global.anthropic.claude-opus-5", cal=5, sub=5, band="LOW RISK"),
        _judged("global.anthropic.claude-sonnet-5", cal=3, sub=3, band="LOW RISK"),
        _judged("global.anthropic.claude-haiku-4-5", cal=3, sub=3, band="LOW RISK"),
    ]
    bias = tq.judge_self_preference(rows)
    assert bias["assessable"] is True
    assert bias["judge_scored"]["substance_margin"] == pytest.approx(2.0)
    # Every row declared the correct band, so the deterministic metric is flat:
    # the judge's 2-point advantage for its own family is not corroborated.
    assert bias["deterministic_cross_check"]["band_agreement_margin"] == pytest.approx(0.0)


def test_self_preference_reports_unassessable_when_only_one_family_was_judged() -> None:
    rows = [_judged("global.anthropic.claude-opus-5", cal=5, sub=5, band="LOW RISK")]
    bias = tq.judge_self_preference(rows)
    assert bias["assessable"] is False
    assert bias["conflict"] == tq.JUDGE_CONFLICT


def test_the_conflict_is_published_as_a_caveat(report: dict) -> None:
    assert tq.JUDGE_CONFLICT in report["caveats"]
    assert report["judge_self_preference"]["judge_model"] == tq.JUDGE_MODEL


# ---------------------------------------------------------------------------
# 7. No fabricated cost, and percentiles obey the repo's sample-count policy
# ---------------------------------------------------------------------------


def test_a_row_with_no_measured_usage_reports_no_cost() -> None:
    row = tq.AnalysisRow(app="APP-1009", domain="rings", model="m", text="Verdict: LOW RISK.")
    evaluated = tq.evaluate_row(row, None)
    assert evaluated["cost_usd"] is None
    assert evaluated["usage"] is None


def test_cell_cost_and_latency_medians_are_none_when_nothing_was_measured() -> None:
    rows = [
        tq.evaluate_row(
            tq.AnalysisRow(app=app, domain="rings", model="m", text="Verdict: LOW RISK."), None
        )
        for app in ("APP-1009", "APP-1011")
    ]
    cell = tq.aggregate(rows)[0]
    assert cell["measured"]["cost_usd_median"] is None
    assert cell["measured"]["wall_s_median"] is None


def test_the_spend_estimate_refuses_to_price_a_domain_it_has_no_measured_tokens_for() -> None:
    estimate = tq.spend_estimate(
        [("rings", "global.anthropic.claude-opus-5", "APP-1004")], 0, measured_tokens={}
    )
    assert estimate["specialist_calls_unpriced"] == 1
    assert estimate["per_call"][0]["usd"] is None


def test_the_spend_estimate_says_it_is_a_ceiling_not_a_token_prediction() -> None:
    estimate = tq.spend_estimate([], 1, measured_tokens=tq.MEASURED_MEAN_TOKENS)
    assert "NOT a prediction" in estimate["basis"]


def test_a_two_sample_cell_reports_a_refusal_instead_of_a_p95() -> None:
    rows = [
        tq.evaluate_row(
            tq.AnalysisRow(
                app=app,
                domain="rings",
                model="m",
                text="Verdict: LOW RISK.",
                wall_s=10.0,
                usage={"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0},
            ),
            None,
        )
        for app in ("APP-1009", "APP-1011")
    ]
    summary = tq.aggregate(rows)[0]["measured"]["wall_s"]
    assert summary["p95"] is None
    assert "refused" in summary["suppressed"]["p95"]
    assert summary["p50"] is None, "5 samples are required for a p50 in this repo"


def test_the_percentile_policy_is_the_repo_wide_one() -> None:
    assert bench.PERCENTILE_MIN_SAMPLES == {"p50": 5, "p95": 20, "p99": 100}


# ---------------------------------------------------------------------------
# 8. The verdict layer refuses to over-claim on a small sample
# ---------------------------------------------------------------------------


def _cell(domain: str, model: str, *, apps: int, agree: int, fp: int, cal: float) -> dict:
    return {
        "domain": domain,
        "model": model,
        "n_calls": apps,
        "n_usable": apps,
        "n_failed": 0,
        "band": {
            "n_with_readable_band": apps,
            "n_unreadable_band": 0,
            "agreements": agree,
            "agreement_rate": agree / apps,
            "disagreements": [],
            "extractor_judge_disagreements": [],
        },
        "contract": {"total_violations": 0, "rows_clean": apps, "by_rule": {}},
        "judge": {
            "n_judged": apps,
            "calibration_mean": cal,
            "calibration_min": cal,
            "substance_mean": cal,
            "escalates": 0,
            "dismisses": 0,
            "false_positive": fp,
            "false_negative": 0,
            "padding_observed": 0,
            "cites_context_fields": apps,
        },
        "measured": {
            "wall_s_median": 10.0,
            "wall_s": {},
            "cost_usd_median": 0.01,
            "out_tok_median": 100,
            "chars_median": 1000,
            "evidence_per_1k_chars_median": 5.0,
            "distinct_evidence_items_median": 5,
            "grounded_fraction_median": 0.5,
        },
    }


def test_three_applications_is_reported_as_too_small_even_when_the_cheap_model_wins() -> None:
    cells = [
        _cell("bustout", "global.anthropic.claude-sonnet-5", apps=3, agree=1, fp=2, cal=2.5),
        _cell("bustout", "global.anthropic.claude-haiku-4-5", apps=3, agree=3, fp=0, cal=4.0),
    ]
    verdict = tq.per_agent_verdict(cells)[0]
    assert "TOO SMALL" in verdict["verdict"], verdict["verdict"]
    assert "DEFENSIBLE on this sample" not in verdict["verdict"]


def test_a_cheaper_model_that_commits_more_false_positives_is_not_defensible() -> None:
    cells = [
        _cell("rings", "global.anthropic.claude-opus-5", apps=5, agree=5, fp=0, cal=4.5),
        _cell("rings", "global.anthropic.claude-haiku-4-5", apps=5, agree=5, fp=3, cal=4.5),
    ]
    verdict = tq.per_agent_verdict(cells)[0]
    assert verdict["verdict"].startswith("NOT SHOWN"), verdict["verdict"]


def test_a_cheaper_model_matching_the_incumbent_at_five_apps_is_defensible() -> None:
    cells = [
        _cell("rings", "global.anthropic.claude-opus-5", apps=5, agree=4, fp=1, cal=4.2),
        _cell("rings", "global.anthropic.claude-sonnet-5", apps=5, agree=4, fp=1, cal=4.0),
    ]
    verdict = tq.per_agent_verdict(cells)[0]
    assert verdict["verdict"].startswith("DEFENSIBLE"), verdict["verdict"]
    assert "sonnet-5" in verdict["verdict"]


def test_an_agent_already_on_the_cheapest_tier_makes_no_claim() -> None:
    cells = [_cell("straw", "global.anthropic.claude-haiku-4-5", apps=5, agree=5, fp=0, cal=5.0)]
    verdict = tq.per_agent_verdict(cells)[0]
    assert "NO CHEAPER TIER" in verdict["verdict"]


def test_every_verdict_names_its_sample_size() -> None:
    cells = [
        _cell("rings", "global.anthropic.claude-opus-5", apps=5, agree=4, fp=1, cal=4.2),
        _cell("rings", "global.anthropic.claude-haiku-4-5", apps=5, agree=4, fp=1, cal=3.4),
    ]
    for verdict in tq.per_agent_verdict(cells):
        assert "application(s) per cell" in verdict["sample_note"]


# ---------------------------------------------------------------------------
# 9. The verbosity comparison is paired, so it cannot be an artifact of easier apps
# ---------------------------------------------------------------------------


def test_verbosity_comparison_only_pairs_the_same_application_and_domain() -> None:
    rows = [
        tq.evaluate_row(
            tq.AnalysisRow(
                app=app,
                domain="rings",
                model=model,
                text=f"Verdict: LOW RISK. {'padding ' * pad}",
            ),
            None,
        )
        for app, model, pad in (
            ("APP-1009", "global.anthropic.claude-opus-5", 40),
            ("APP-1009", "global.anthropic.claude-sonnet-5", 10),
            ("APP-1011", "global.anthropic.claude-opus-5", 40),
        )
    ]
    comparisons = tq.verbosity_vs_substance(rows)["comparisons"]
    opus_sonnet = [c for c in comparisons if c["longer"] == "opus-5" and c["shorter"] == "sonnet-5"]
    assert opus_sonnet, comparisons
    # APP-1011 has no sonnet counterpart, so exactly one pair may be compared.
    assert all(c["n_paired_applications"] == 1 for c in opus_sonnet)


def test_pure_padding_shows_a_chars_ratio_above_the_evidence_ratio() -> None:
    fact = "phone_reuse_distinct_ssn_count is 5 across 4 surnames. Verdict: LOW RISK."
    rows = [
        tq.evaluate_row(
            tq.AnalysisRow(app="APP-1009", domain="rings", model=model, text=text), None
        )
        for model, text in (
            ("global.anthropic.claude-opus-5", fact + " " + "Restated with no new facts. " * 12),
            ("global.anthropic.claude-sonnet-5", fact),
        )
    ]
    pooled = [
        c
        for c in tq.verbosity_vs_substance(rows)["comparisons"]
        if c["scope"] == "ALL AGENTS POOLED" and c["longer"] == "opus-5"
    ][0]
    assert pooled["chars_ratio_median"] > pooled["evidence_ratio_median"]


def test_the_comparison_is_reported_per_agent_and_not_only_pooled() -> None:
    """Pooling four agents can cancel the one agent that motivated the question."""
    rows = [
        tq.evaluate_row(
            tq.AnalysisRow(app="APP-1009", domain=domain, model=model, text="Verdict: LOW RISK."),
            None,
        )
        for domain in ("rings", "synthetic")
        for model in ("global.anthropic.claude-opus-5", "global.anthropic.claude-sonnet-5")
    ]
    scopes = {c["scope"] for c in tq.verbosity_vs_substance(rows)["comparisons"]}
    assert {"ALL AGENTS POOLED", "rings", "synthetic"} <= scopes


# ---------------------------------------------------------------------------
# 10. Reuse and the verdict cache: paid-for text is never silently discarded
# ---------------------------------------------------------------------------


def test_reuse_roundtrips_an_analysis_row_without_losing_its_measurements(tmp_path) -> None:
    row = tq.AnalysisRow(
        app="APP-1004",
        domain="rings",
        model="global.anthropic.claude-opus-5",
        text="Verdict: HIGH RISK.",
        wall_s=32.81,
        usage={
            "inputTokens": 1532,
            "outputTokens": 2454,
            "cacheReadInputTokens": 1175,
            "cacheWriteInputTokens": 0,
        },
        cost_usd=0.0696,
    )
    path = tmp_path / "analyses.json"
    path.write_text(json.dumps([row.to_reuse_json()]), encoding="utf-8")
    loaded = tq.load_reuse_file(path)[0]
    assert loaded.app == row.app
    assert loaded.model == row.model
    assert loaded.text == row.text
    assert loaded.usage == row.usage
    assert loaded.cost_usd == row.cost_usd
    assert loaded.source.startswith("reused:")


def test_reuse_skips_rows_that_carry_no_text() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(
            [
                {"app": "APP-1004", "domain": "rings", "model": "opus-5", "text": ""},
                {"app": "APP-1004", "domain": "rings", "model": "opus-5", "text": "Verdict: LOW RISK."},
            ],
            handle,
        )
        name = handle.name
    try:
        assert len(tq.load_reuse_file(name)) == 1
    finally:
        os.unlink(name)


def test_short_model_labels_from_an_older_sweep_expand_to_full_ids() -> None:
    assert tq._expand_model_id("opus-5") == "global.anthropic.claude-opus-5"
    assert tq._expand_model_id("haiku-4-5-20251001-v1:0") in tq.CANDIDATE_MODELS


def test_the_judge_output_cap_is_above_the_size_that_truncated_a_verdict() -> None:
    """MEASURED: 2048 lost 12 of 48 verdicts to MaxTokensReachedException."""
    assert tq.JUDGE_MAX_TOKENS > 2048


# ---------------------------------------------------------------------------
# 11. The published result document says what it measured and what it refused
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report() -> dict:
    if not RESULT_PATH.is_file():
        pytest.skip(f"no committed result at {RESULT_PATH}")
    return json.loads(RESULT_PATH.read_text(encoding="utf-8"))


def test_the_report_records_the_blinding_and_the_judge(report: dict) -> None:
    assert report["run"]["judge_model"] == tq.JUDGE_MODEL
    assert "shuffled" in report["run"]["blinding"]
    assert report["run"]["shuffle_seed"] == tq.SHUFFLE_SEED


def test_the_report_records_actual_calls_made_and_reuse(report: dict) -> None:
    live = report["live_calls"]
    assert "specialist_calls_made" in live
    assert "judge_calls_made" in live
    assert live["reused_analyses"] >= 18, "the existing 18 analyses must be reused, not re-run"


def test_every_judged_analysis_in_the_report_has_a_pinned_expectation(report: dict) -> None:
    from fixtures.loader import expected_for

    for row in report["per_analysis"]:
        pinned = expected_for(row["app"])["domains"][row["domain"]]
        assert row["expected_band"] == pinned["band"]
        assert row["requirement_id"] == pinned["requirement_id"]
        assert row["customer_rule"] == pinned["customer_rule"]


def test_no_reported_cost_exists_without_reported_usage(report: dict) -> None:
    for row in report["per_analysis"]:
        if row["cost_usd"] is not None:
            assert row["usage"], f"{row['domain']}/{row['model']}/{row['app']} priced with no usage"


def test_the_report_carries_a_verdict_for_every_swept_agent(report: dict) -> None:
    swept = {cell["domain"] for cell in report["per_cell"]}
    assert {v["domain"] for v in report["per_agent_verdict"]} == swept
    for verdict in report["per_agent_verdict"]:
        assert verdict["verdict"], verdict


def test_the_report_states_its_caveats_including_the_percentile_refusal(report: dict) -> None:
    joined = " ".join(report["caveats"]).lower()
    assert "p95 needs 20" in joined
    assert "no cost is modelled" in joined


def test_the_contract_scope_in_the_report_matches_the_prompts_on_disk(report: dict) -> None:
    """The published scope must be re-derivable from the verbatim prompts, not pasted."""
    for rule, info in report["contract_scope"]["rules"].items():
        assert info["applies_to"] == [d for d in DOMAINS if tq.rule_applies(d, rule)]
