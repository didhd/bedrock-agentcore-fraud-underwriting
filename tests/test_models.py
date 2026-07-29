"""
Guards on the per-agent model assignment, the output caps, and the cost projection.

WHY THESE TESTS EXIST. ``agents/models.py`` decides which model analyses each fraud
dimension. The first version of that table was authored by READING the customer's
prompts ("fraud rings needs multi-signal network reasoning, so give it Opus"), and
measurement contradicted it on both agents that reading placed on HEAVY. The failure
mode is therefore not a crash - it is a confident, plausible, unmeasured claim - and
these tests are shaped to catch exactly that:

  1. **An assignment whose rationale cites nothing checkable.** Every one of the nine
     must name a measurement, and where it names an artifact and an n, the artifact must
     exist on disk and actually contain that agent at that n. A rationale that only
     sounds authoritative fails.

  2. **A number in a rationale that the report does not contain.** The latency, cost and
     band figures quoted in ``TIER_RATIONALE`` are re-read out of
     ``evals/results/*.json`` and compared. This is the test that would have caught the
     figure this task actually found wrong: a previous revision claimed rings' Opus wrote
     "1.9x the output tokens (2454 vs 1314)" by taking Opus's number from APP-1012 and
     Sonnet's from APP-1004 - two different applications, so not a ratio at all.

  3. **A cost projection that holds tokens constant across a model swap.** rings moved
     from Opus 5 to Sonnet 5, and Opus wrote 2096.8 output tokens mean against Sonnet's
     1862.6 on the SAME five applications. Pricing Sonnet at Opus's token counts is
     fiction, so the projection must draw those two rows from a report of the model it
     now assigns, and must label itself a projection rather than a measurement.

  4. **A tightened output cap.** ``max_tokens`` was measured as a brevity instrument and
     failed: no latency win, 14 of 30 cells truncated non-monotonically, and 3 fraud
     bands moved. A truncated call is billed in full and returns nothing. So no cap may
     drop below its agent's measured ceiling, and the measured truncation boundary must
     stay recorded so nobody tightens it blindly.

  5. **A cap that silently follows the model tier.** Until this re-derivation rings drew
     its cap from its tier, so a tier move changed which cap it inherited - a latent
     defect for one of the only two agents ever measured to truncate.

Offline only: no Bedrock call, no AWS credentials, no network.

Run:  MOCK_MODE=1 python -m pytest tests/test_models.py -q
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents import models as m  # noqa: E402
from agents import pricing as p  # noqa: E402

#: Sonnet 5's introductory rate is live before this date and lapses on it. Every cost
#: assertion pins a date, because a rate that changes on 2026-09-01 would otherwise make
#: these tests start failing on a calendar boundary rather than on a code change.
BEFORE_INTRO_EXPIRY = dt.date(2026, 7, 28)
AFTER_INTRO_EXPIRY = dt.date(2026, 9, 1)

PIPELINE_REPORT = REPO_ROOT / "evals" / "results" / "pipeline_14x3_us-east-1.json"
TIER_QUALITY_REPORT = REPO_ROOT / "evals" / "results" / "tier_quality.json"
TIER_ANALYSES_REPORT = REPO_ROOT / "evals" / "results" / "tier_quality_analyses.json"
TAIL_REPORT = REPO_ROOT / "evals" / "results" / "tail_analysis.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


needs_pipeline = pytest.mark.skipif(
    not PIPELINE_REPORT.is_file(), reason="pipeline benchmark artifact not on disk"
)
needs_tier_quality = pytest.mark.skipif(
    not TIER_QUALITY_REPORT.is_file(), reason="tier quality artifact not on disk"
)
needs_tier_analyses = pytest.mark.skipif(
    not TIER_ANALYSES_REPORT.is_file(), reason="tier quality analyses artifact not on disk"
)
needs_tail = pytest.mark.skipif(
    not TAIL_REPORT.is_file(), reason="tail analysis artifact not on disk"
)


# ---------------------------------------------------------------------------
# The assignment itself
# ---------------------------------------------------------------------------


def test_no_agent_runs_on_opus_5_and_the_two_that_did_are_named() -> None:
    """The re-derivation's headline, asserted rather than described.

    rings and synthetic were on Opus 5 because a reading of the prompts said they needed
    heavy reasoning. Measurement said otherwise on both, so there is no HEAVY agent left.
    Both rationales must record that they MOVED, so the correction cannot be quietly
    forgotten and re-made.
    """
    assert [r for r in m.ROLES if m.tier_label(m.model_for(r)) == "heavy"] == []
    for domain in ("rings", "synthetic"):
        assert m.model_for(domain) == m.MID_MODEL
        assert "moved down from HEAVY" in m.rationale_for(domain), domain


def test_every_specialist_has_an_assignment_a_cap_and_a_rationale() -> None:
    assert set(m.SPECIALIST_MODELS) == set(m.DOMAINS)
    assert set(m.MAX_TOKENS_BY_AGENT) == set(m.DOMAINS)
    assert set(m.TIER_RATIONALE) == set(m.ROLES)
    # The synthesizer is the ninth role and needs a cap too, from its own constant.
    assert m.max_tokens_for("synthesizer") == m.SYNTHESIZER_MAX_TOKENS
    for role in m.ROLES:
        assert m.max_tokens_for(role) > 0, role
        assert len(m.rationale_for(role)) > 200, role


def test_every_rationale_names_a_measurement_and_never_only_a_prompt_reading() -> None:
    """The rule this module exists to enforce: evidence, or an admission of its absence.

    A rationale may quote the customer's prompt as corroboration, but it may not rest on
    one. Each entry must state its evidence grade and carry a concrete measured quantity
    (a wall clock, a dollar figure, or a sample count).
    """
    for role in m.ROLES:
        rationale = m.rationale_for(role)
        assert m.evidence_grade(role) != "UNRECORDED", role
        assert "n=" in rationale, f"{role} states no sample size"
        # A measured quantity, not just an adjective: a wall clock AND a dollar figure.
        assert "p50" in rationale, f"{role} quotes no measured latency"
        assert "$0.0" in rationale, f"{role} quotes no measured cost"
        # And it must name the artifact the numbers came from.
        assert "evals/results/" in rationale, f"{role} names no source artifact"


def test_the_two_unproven_agents_are_the_ones_no_report_covers() -> None:
    """An honest 'unproven' has to be true, not just modest.

    income and employment claim they were never swept. If a quality report on disk DOES
    cover them, that claim is stale and the rationale must be updated - the point of the
    label is that it tracks the evidence.
    """
    unproven = {r for r in m.ROLES if m.evidence_grade(r) == "UNPROVEN"}
    assert unproven == {"income", "employment"}
    assert unproven.isdisjoint(m.QUALITY_SWEPT_AGENTS)
    if TIER_QUALITY_REPORT.is_file():
        covered = {c["domain"] for c in _load(TIER_QUALITY_REPORT)["per_cell"]}
        assert unproven.isdisjoint(covered), (
            f"a report now covers {unproven & covered}; the UNPROVEN rationale is stale"
        )


@needs_tier_quality
def test_quality_swept_agents_are_exactly_the_ones_the_report_measured() -> None:
    report = _load(TIER_QUALITY_REPORT)
    measured = {c["domain"]: c["n_calls"] for c in report["per_cell"]}
    assert set(m.QUALITY_SWEPT_AGENTS) == set(measured)
    for domain, n in m.QUALITY_SWEPT_AGENTS.items():
        assert n == measured[domain], domain


@needs_tier_quality
def test_rings_and_synthetic_moved_for_the_reasons_the_report_records() -> None:
    """Re-read the decisive figures out of the artifact instead of trusting the prose.

    Both moves must survive a check the rationale cannot influence: same-or-better band
    agreement against the pinned expectation, and no additional judge-confirmed false
    positives.
    """
    cells = {
        (c["domain"], c["model"].split("claude-")[-1]): c
        for c in _load(TIER_QUALITY_REPORT)["per_cell"]
    }

    rings_sonnet = cells[("rings", "sonnet-5")]
    rings_opus = cells[("rings", "opus-5")]
    # Same band, same false positives -- so cost and latency are allowed to decide.
    assert rings_sonnet["band"]["agreements"] == rings_opus["band"]["agreements"] == 4
    assert rings_sonnet["judge"]["false_positive"] == rings_opus["judge"]["false_positive"] == 1
    assert rings_sonnet["measured"]["cost_usd_median"] < rings_opus["measured"]["cost_usd_median"]
    assert rings_sonnet["measured"]["wall_s_median"] < rings_opus["measured"]["wall_s_median"]

    synth_sonnet = cells[("synthetic", "sonnet-5")]
    synth_opus = cells[("synthetic", "opus-5")]
    # synthetic moved on QUALITY: strictly better band and calibration, and Opus owns the
    # sweep's only false negative.
    assert synth_sonnet["band"]["agreements"] > synth_opus["band"]["agreements"]
    assert synth_sonnet["judge"]["calibration_mean"] > synth_opus["judge"]["calibration_mean"]
    assert synth_opus["judge"]["false_negative"] == 1
    assert synth_sonnet["judge"]["false_negative"] == 0


@needs_tier_quality
def test_no_conclusion_rests_on_a_margin_below_the_judges_self_preference() -> None:
    """The judge is Opus 5 and Opus 5 is a candidate, so its own margin is suspect.

    rings is the cell where this bites: Opus's ONLY remaining advantage over Sonnet is
    +0.20 judge calibration, and the judge scored its own family +0.31 on that same axis.
    The rationale must therefore refuse the margin, and the direction must be checked
    against a metric the judge cannot influence.
    """
    report = _load(TIER_QUALITY_REPORT)
    self_pref = report["judge_self_preference"]
    cells = {
        (c["domain"], c["model"].split("claude-")[-1]): c for c in report["per_cell"]
    }
    opus_edge = (
        cells[("rings", "opus-5")]["judge"]["calibration_mean"]
        - cells[("rings", "sonnet-5")]["judge"]["calibration_mean"]
    )
    assert 0 < opus_edge < self_pref["judge_scored"]["calibration_margin"]
    assert "self-preference" in m.rationale_for("rings")

    # synthetic is the opposite case and must NOT carry the caveat as an excuse: the
    # margin there runs against the judge's own family, so bias cannot explain it.
    assert (
        cells[("synthetic", "sonnet-5")]["judge"]["calibration_mean"]
        > cells[("synthetic", "opus-5")]["judge"]["calibration_mean"]
    )


@needs_tier_analyses
def test_the_verbosity_ratio_quoted_for_rings_is_paired_by_application() -> None:
    """The specific fabrication this test exists to prevent.

    A previous revision claimed Opus wrote "1.9x the output tokens (2454 vs 1314)" on
    rings. Those two counts come from DIFFERENT applications (Opus on APP-1012, Sonnet on
    APP-1004), so their ratio measures the applications, not the models. The honest
    comparison pairs by (application, domain), and on that basis the ratio is 1.76x
    characters against 1.71x distinct evidence - which is the pair the rationale quotes.
    """
    rationale = m.rationale_for("rings")
    assert "1.76x" in rationale and "1.71x" in rationale
    assert "2454 vs 1314" not in rationale, "unpaired token counts are back"

    if TIER_QUALITY_REPORT.is_file():
        comparisons = _load(TIER_QUALITY_REPORT)["verbosity_vs_substance"]["comparisons"]
        rings = next(
            c
            for c in comparisons
            if c["scope"] == "rings" and c["longer"] == "opus-5" and c["shorter"] == "sonnet-5"
        )
        assert rings["chars_ratio_median"] == 1.76
        assert rings["evidence_ratio_median"] == 1.71
        assert rings["substance_delta_median"] == 0
        assert rings["n_paired_applications"] == m.QUALITY_SWEPT_AGENTS["rings"]

    # And the paired latency claim: Sonnet is faster on 3 of 5, not on all 5.
    analyses = _load(TIER_ANALYSES_REPORT)["analyses"]

    def by_app(model_suffix: str) -> dict[str, float]:
        return {
            r["app"]: r["wall_s"]
            for r in analyses
            if r["domain"] == "rings" and r["model"].endswith(model_suffix) and not r.get("error")
        }

    sonnet, opus = by_app("sonnet-5"), by_app("opus-5")
    shared = sorted(set(sonnet) & set(opus))
    assert len(shared) == 5
    assert sum(1 for a in shared if sonnet[a] < opus[a]) == 3
    assert "faster on 3 of 5" in m.rationale_for("rings")


@needs_tier_analyses
def test_synthetic_admits_the_latency_loss_it_accepts() -> None:
    """Not overfitting to cost, asserted where it is uncomfortable.

    On synthetic the cheaper model is also the SLOWER one - Sonnet 13.54s p50 against
    Opus's 8.51s, slower on 4 of 5 paired applications. The move is still right because
    Opus produced a false negative, but the rationale must say the latency went the wrong
    way rather than quietly presenting the swap as a win on every axis.
    """
    analyses = _load(TIER_ANALYSES_REPORT)["analyses"]

    def by_app(model_suffix: str) -> dict[str, float]:
        return {
            r["app"]: r["wall_s"]
            for r in analyses
            if r["domain"] == "synthetic"
            and r["model"].endswith(model_suffix)
            and not r.get("error")
        }

    sonnet, opus = by_app("sonnet-5"), by_app("opus-5")
    shared = sorted(set(sonnet) & set(opus))
    slower = sum(1 for a in shared if sonnet[a] > opus[a])
    assert slower == 4, slower

    rationale = m.rationale_for("synthetic")
    assert "SLOWER" in rationale
    assert "slower on 4 of 5" in rationale
    assert "false NEGATIVE" in rationale


def test_the_cheapest_option_was_refused_where_quality_argued_against_it() -> None:
    """A cost-driven table would have taken Haiku on synthetic. This one did not.

    Haiku banded 5/5 on synthetic at $0.0059 and 9.56s - cheaper AND faster than the
    Sonnet 5 now assigned. It was refused on output-contract grounds, and the rationale
    has to say so, because "we took the cheapest thing that scored well" is exactly the
    failure this assignment is supposed to avoid.
    """
    rationale = m.rationale_for("synthetic")
    assert "Haiku was NOT taken" in rationale
    assert "contract violations" in rationale
    if TIER_QUALITY_REPORT.is_file():
        cells = {
            (c["domain"], c["model"].split("claude-")[-1]): c
            for c in _load(TIER_QUALITY_REPORT)["per_cell"]
        }
        haiku = cells[("synthetic", "haiku-4-5-20251001-v1:0")]
        sonnet = cells[("synthetic", "sonnet-5")]
        # The premise: Haiku really was cheaper, faster and equal on bands.
        assert haiku["measured"]["cost_usd_median"] < sonnet["measured"]["cost_usd_median"]
        assert haiku["measured"]["wall_s_median"] < sonnet["measured"]["wall_s_median"]
        assert haiku["band"]["agreements"] == sonnet["band"]["agreements"]
        # And the reason it lost.
        assert haiku["contract"]["total_violations"] > sonnet["contract"]["total_violations"]


@needs_pipeline
def test_rings_is_named_the_critical_path_agent_because_the_benchmark_says_so() -> None:
    """The latency case for the rings move, re-read from the benchmark.

    rings owned the critical path in 25 of 42 runs. If a future benchmark changes that,
    the rationale's central claim is stale and this fails.
    """
    per = _load(PIPELINE_REPORT)["per_specialist"]
    slowest = {d: r["n_slowest_in_run"] for d, r in per.items()}
    assert max(slowest, key=lambda d: slowest[d]) == "rings"
    assert slowest["rings"] == 25
    assert "25 of 42" in m.rationale_for("rings")
    # The two LIGHT agents never set the floor, which is what makes LIGHT safe for them.
    assert slowest["dealer"] == slowest["straw"] == 0
    for domain in ("dealer", "straw"):
        assert "0 of 42" in m.rationale_for(domain), domain


# ---------------------------------------------------------------------------
# Output caps
# ---------------------------------------------------------------------------


def test_the_cap_is_a_property_of_the_agent_not_of_its_model_tier() -> None:
    """The latent defect this map removes.

    Before the re-derivation rings' cap came from ``MAX_TOKENS_BY_TIER[tier_of(model)]``,
    so changing rings' MODEL changed its truncation risk - and rings is one of only two
    agents ever measured to truncate. The per-agent map must therefore be insensitive to
    the tier: repoint an agent at LIGHT and its cap must not move.
    """
    for domain in m.DOMAINS:
        assert m.max_tokens_for(domain) == m.MAX_TOKENS_BY_AGENT[domain], domain

    original = dict(m.SPECIALIST_MODELS)
    try:
        m.SPECIALIST_MODELS["rings"] = m.LIGHT_MODEL
        assert m.tier_label(m.model_for("rings")) == "light"
        # The tier fallback WOULD have dropped it; the per-agent map does not.
        assert m.MAX_TOKENS_BY_TIER["light"] < m.MAX_TOKENS_BY_AGENT["rings"]
        assert m.max_tokens_for("rings") == m.MAX_TOKENS_BY_AGENT["rings"]
    finally:
        m.SPECIALIST_MODELS.clear()
        m.SPECIALIST_MODELS.update(original)


def test_per_agent_caps_preserve_the_shipped_effective_values() -> None:
    """Introducing the map must not change any cap in flight.

    ``agents/fanout.py`` still resolves by tier, so if the two tables disagreed the
    shipped behaviour would depend on which caller ran. They must agree for every agent
    at its currently assigned model.
    """
    for domain in m.DOMAINS:
        by_tier = m.MAX_TOKENS_BY_TIER[m.tier_label(m.model_for(domain))]
        assert m.MAX_TOKENS_BY_AGENT[domain] == by_tier, domain


@needs_pipeline
def test_no_cap_sits_below_the_largest_output_that_agent_was_measured_to_write() -> None:
    """The rule that makes a cap safe, checked against every live call on disk.

    A cap below an agent's observed maximum is a cap that has already been seen to
    truncate, and a truncated specialist is billed in full and returns nothing - fan-out
    degrades to seven of eight fraud dimensions. The margin required is deliberately
    modest (>= 1.0x) because the point is to catch a cap set below evidence, not to
    mandate a particular headroom.
    """
    observed: dict[str, int] = {}

    def bump(domain: str, tokens: int | None) -> None:
        if tokens:
            observed[domain] = max(observed.get(domain, 0), int(tokens))

    for run in _load(PIPELINE_REPORT)["raw_runs"]:
        for spec in run["specialists"]:
            if not spec.get("error"):
                bump(spec["domain"], spec["usage"]["outputTokens"])
    if TIER_ANALYSES_REPORT.is_file():
        for row in _load(TIER_ANALYSES_REPORT)["analyses"]:
            # Only the model each agent is NOW assigned. An Opus row would size the cap
            # against a model this assignment does not use.
            if not row.get("error") and row["model"] == m.model_for(row["domain"]):
                bump(row["domain"], row["out_tok"])
    if TAIL_REPORT.is_file():
        for cell in _load(TAIL_REPORT)["live_cap_sweep"]["per_cell"]:
            bump(cell["domain"], cell.get("output_tokens"))

    assert set(observed) == set(m.DOMAINS)
    for domain, largest in observed.items():
        assert m.MAX_TOKENS_BY_AGENT[domain] > largest, (
            f"{domain}: cap {m.MAX_TOKENS_BY_AGENT[domain]} is at or below the largest "
            f"measured output ({largest} tokens), which is a truncation already seen"
        )

    # Pin the ceilings the module's own docstring publishes, so the table cannot drift
    # from the artifacts it claims to be derived from. bustout is 3257 and not 3385
    # because 3385 was an Opus 5 call and no agent runs on Opus any more.
    assert observed == {
        "straw": 989,
        "dealer": 1464,
        "employment": 1647,
        "synthetic": 2737,
        "income": 2783,
        "rings": 2876,
        "bustout": 3257,
        "identity": 3582,
    }

    # The synthesizer too: its cap must clear the largest adjudication ever emitted,
    # because a truncated synthesis is invalid JSON rather than a shorter one.
    syntheses = [
        run["synthesis_usage"]["outputTokens"]
        for run in _load(PIPELINE_REPORT)["raw_runs"]
        if not run.get("synthesis_error")
    ]
    assert m.SYNTHESIZER_MAX_TOKENS > max(syntheses)


@needs_tail
def test_the_measured_truncation_boundary_is_recorded_and_not_treated_as_safe() -> None:
    """Nobody may tighten a cap without meeting the evidence that says not to.

    The sweep found the boundary is a PROBABILITY, not a threshold: both agents truncated
    AND completed at 1536 and 2048 depending on the application and the draw. So the
    recorded ``lowest_cap_every_cell_completed`` must never be mistaken for a safe value
    to ship, and the shipped cap must stay above it.
    """
    sweep = _load(TAIL_REPORT)["live_cap_sweep"]["failure_boundary"]["per_domain"]
    assert set(m.MEASURED_TRUNCATION_BOUNDARY) == set(sweep)
    for domain, recorded in m.MEASURED_TRUNCATION_BOUNDARY.items():
        measured = sweep[domain]
        assert tuple(measured["caps_that_truncated"]) == recorded["caps_observed_truncating"]
        assert (
            measured["lowest_cap_that_completed_every_cell"]
            == recorded["lowest_cap_every_cell_completed"]
        )
        # The overlap that makes this a probability: a cap that truncated somewhere also
        # completed somewhere else.
        overlap = set(measured["caps_that_truncated"]) & set(measured["caps_that_completed"])
        assert overlap, f"{domain}: expected a non-monotonic boundary"
        # And the shipped cap is strictly above the lowest cap that ever completed all.
        assert m.MAX_TOKENS_BY_AGENT[domain] > recorded["lowest_cap_every_cell_completed"]


@needs_tail
def test_lowering_a_cap_is_not_supported_by_the_sweep_that_measured_it() -> None:
    """The three criteria a cap would have to meet, and the measured result on each.

    Pinned so a future 'let us just cap it lower' change has to argue with the data:
    no latency win, truncations, and moved bands.
    """
    sweep = _load(TAIL_REPORT)["live_cap_sweep"]
    effect = sweep["does_a_lower_cap_shorten_the_call"]
    # (a) no latency win: comparable cells split evenly and the mean delta is ~zero.
    assert effect["n_comparable_cells"] == 10
    assert effect["n_faster_than_control"] == 5
    assert effect["n_slower_than_control"] == 5
    assert effect["delta_seconds"]["mean"] == pytest.approx(-0.23, abs=0.01)
    # (b) truncation, and it is not cheap: 14 cells spent wall clock and returned nothing.
    assert sweep["truncation_is_not_a_saving"]["n_truncated"] == 14
    # (c) bands moved: only 13 of 30 cells agreed with the same-session control.
    assert sweep["band_agreement"]["n_agreeing"] == 13
    assert sweep["band_agreement"]["n_comparisons"] == 30


def test_build_bedrock_model_still_resolves_a_cap_from_a_model_id_alone() -> None:
    """The tier map is the fallback for callers that have no agent name.

    The eval judge and any ad-hoc call pass a model id only, so removing the tier map
    would break them. It must stay, and it must cover every tier label the code can
    produce.
    """
    m.reset_shared_clients()
    try:
        for model_id in (m.LIGHT_MODEL, m.MID_MODEL, m.HEAVY_MODEL, m.SYNTHESIZER_MODEL):
            built = m.build_bedrock_model(model_id, region="us-east-1")
            assert built.get_config()["max_tokens"] == m.MAX_TOKENS_BY_TIER[
                m.tier_label(model_id)
            ], model_id
        # An unrecognised id falls to 'custom', which must exist.
        assert m.tier_label("global.anthropic.claude-opus-4-8") == "custom"
        assert "custom" in m.MAX_TOKENS_BY_TIER
        # An explicit per-agent cap overrides it, which is how fan-out will pass one.
        explicit = m.build_bedrock_model(
            m.MID_MODEL, region="us-east-1", max_tokens=m.max_tokens_for("rings")
        )
        assert explicit.get_config()["max_tokens"] == m.MAX_TOKENS_BY_AGENT["rings"]
    finally:
        m.reset_shared_clients()


def test_a_character_cap_is_not_a_token_cap() -> None:
    """Why no cap here is derived from the customer's '4000 characters' rule.

    Measured chars-per-output-token runs from 4.51 (dealer) down to 0.33 on identity's
    worst calls - 3,582 billed output tokens behind 1,191 characters. A cap computed as
    4000/4 = 1024 would truncate identity at a third of its own contract, and 1024
    truncated 6 of 6 cells in the live sweep. So every cap must clear that naive value by
    a wide margin, and the module must say why.
    """
    naive_from_chars = 4000 // m._CHARS_PER_TOKEN_ESTIMATE
    for domain in m.DOMAINS:
        assert m.MAX_TOKENS_BY_AGENT[domain] >= 3 * naive_from_chars, domain
    import inspect

    source = inspect.getsource(m)
    assert "A CHARACTER CAP AND A TOKEN CAP ARE NOT THE SAME CONSTRAINT" in source


# ---------------------------------------------------------------------------
# The cost projection
# ---------------------------------------------------------------------------


def test_the_projection_is_labelled_a_projection_and_names_its_confirming_run() -> None:
    """It is a sum of per-agent means, not a measured end-to-end, and must say so.

    The pipeline has not been re-run since the assignment changed, so there is no measured
    figure for this configuration. The requirement is that nobody can quote the number
    without the label and the command travelling with it.
    """
    result = m.projected_cost_per_adjudication(as_of=BEFORE_INTRO_EXPIRY)
    assert result["kind"] == "PROJECTION"
    assert "NOT been re-run" in result["not_a_measurement"]
    assert result["confirmed_by"].endswith("python -m evals.bench --pipeline --repetitions 3")
    # No percentile is offered, because a sum of means has no distribution.
    for key in result:
        assert "p50" not in key and "p95" not in key, key
    # It states which measured statistic it is comparable to, and it is the MEAN.
    assert "MEAN" in result["comparable_to"]
    assert "p50" in result["comparable_to"]  # named only to be excluded


@needs_pipeline
def test_recorded_usage_matches_the_reports_it_cites() -> None:
    """Every token count in the projection is transcribed from an artifact, exactly.

    This is the test that stops the table drifting into hand-tuned numbers. The six
    unchanged agents and the synthesizer come from the 42-run benchmark; rings and
    synthetic come from the tier sweep at the model now assigned.
    """
    report = _load(PIPELINE_REPORT)
    for role, record in m.RECORDED_USAGE_PER_CALL.items():
        if record["source"] != "evals/results/pipeline_14x3_us-east-1.json":
            continue
        if role == "synthesizer":
            measured = report["synthesis_detail"]["tokens"]
            assert record["model_id"] == report["synthesis_detail"]["model_id"]
        else:
            measured = report["per_specialist"][role]["tokens"]
            assert record["model_id"] == report["per_specialist"][role]["model_id"], role
        assert record["n"] == measured["n_calls_with_usage"], role
        for key, value in record["usage"].items():
            assert value == pytest.approx(measured["mean_per_call"][key], rel=1e-12), (
                f"{role}.{key}"
            )


@needs_tier_analyses
def test_the_two_swapped_agents_are_priced_at_the_new_models_own_tokens() -> None:
    """A model swap changes output length, so tokens cannot be held constant.

    rings on Opus wrote 2096.8 output tokens mean; on Sonnet 1862.6, on the SAME five
    applications. The projection must use the Sonnet figure, and must NOT be reproducible
    from the benchmark's Opus token counts.
    """
    analyses = _load(TIER_ANALYSES_REPORT)["analyses"]
    for domain in ("rings", "synthetic"):
        record = m.RECORDED_USAGE_PER_CALL[domain]
        assert record["source"] == "evals/results/tier_quality_analyses.json", domain
        assert record["model_id"] == m.model_for(domain) == m.MID_MODEL, domain
        rows = [
            r
            for r in analyses
            if r["domain"] == domain
            and r["model"] == record["model_id"]
            and not r.get("error")
        ]
        assert len(rows) == record["n"] == 5, domain
        assert record["usage"]["outputTokens"] == pytest.approx(
            sum(r["out_tok"] for r in rows) / len(rows), rel=1e-12
        ), domain
        assert record["usage"]["inputTokens"] == pytest.approx(
            sum(r["in_tok"] for r in rows) / len(rows), rel=1e-12
        ), domain
        # Cache reads are carried, because Bedrock's inputTokens excludes them and the
        # 0.1x cache-read rate would otherwise be priced as full input.
        assert record["usage"]["cacheReadInputTokens"] == pytest.approx(
            sum(r["cache_r"] for r in rows) / len(rows), rel=1e-12
        ), domain

    # And the negative: the old Opus token counts must not be what is priced.
    if PIPELINE_REPORT.is_file():
        old = _load(PIPELINE_REPORT)["per_specialist"]["rings"]["tokens"]["mean_per_call"]
        assert m.RECORDED_USAGE_PER_CALL["rings"]["usage"]["outputTokens"] != pytest.approx(
            old["outputTokens"], rel=1e-6
        )


def test_the_projection_equals_the_sum_of_its_rows_priced_from_the_rate_card() -> None:
    """Recompute independently rather than assert a literal.

    The projected total must be exactly the sum of ``cost_usd`` over the recorded usage,
    so a rate-card change moves the projection instead of making it wrong.
    """
    result = m.projected_cost_per_adjudication(as_of=BEFORE_INTRO_EXPIRY)
    expected = 0.0
    for role, record in m.RECORDED_USAGE_PER_CALL.items():
        row = p.cost_usd(record["model_id"], record["usage"], as_of=BEFORE_INTRO_EXPIRY)
        assert result["per_role"][role]["cost_usd"] == pytest.approx(row, rel=1e-12), role
        expected += row
    assert result["projected_usd"] == pytest.approx(expected, rel=1e-12)
    assert result["projected_usd"] == pytest.approx(
        result["projected_specialists_usd"] + result["projected_synthesizer_usd"], rel=1e-12
    )
    # The value quoted in docs/model-selection.md, pinned so the doc and the code cannot
    # drift. $0.1247/adjudication at the introductory Sonnet 5 rate.
    assert result["projected_usd"] == pytest.approx(0.12475, abs=5e-6)
    assert result["projected_specialists_usd"] == pytest.approx(0.11412, abs=5e-6)


@needs_pipeline
def test_the_same_method_reproduces_the_measured_cost_under_the_old_assignment() -> None:
    """The projection method is validated where a measured answer exists.

    Sum the benchmark's own per-agent mean usage at the OLD models and the result must
    land on the benchmark's own measured MEAN cost per adjudication. That is the check
    that the method - a sum of per-agent means - is sound, independent of whether the new
    assignment has been re-run. Tolerance is $0.0005 because the benchmark's mean is over
    41 priced runs while this sums nine per-agent means over 41-42 calls each.
    """
    report = _load(PIPELINE_REPORT)
    total = 0.0
    for domain, rec in report["per_specialist"].items():
        total += p.cost_usd(
            rec["model_id"], rec["tokens"]["mean_per_call"], as_of=BEFORE_INTRO_EXPIRY
        )
    total += p.cost_usd(
        report["synthesis_detail"]["model_id"],
        report["synthesis_detail"]["tokens"]["mean_per_call"],
        as_of=BEFORE_INTRO_EXPIRY,
    )
    measured_mean = report["cost_per_adjudication_usd"]["mean"]
    assert total == pytest.approx(measured_mean, abs=5e-4)

    # And the prior-assignment constant the projection compares against is that mean.
    prior = m.MEASURED_COST_PER_ADJUDICATION_PRIOR_ASSIGNMENT
    assert prior["mean_usd"] == pytest.approx(measured_mean, rel=1e-12)
    assert prior["p50_usd"] == pytest.approx(
        report["cost_per_adjudication_usd"]["p50"], rel=1e-12
    )
    assert prior["n_priced_runs"] == report["cost_per_adjudication_usd"]["n_priced_runs"]


def test_the_projection_reports_the_smallest_n_behind_it() -> None:
    """A projection is only as strong as its weakest row, so that row is surfaced.

    Two of the nine rows rest on 5 applications, not 42. Averaging that away would let a
    slide quote '$0.1247, n=42'.
    """
    result = m.projected_cost_per_adjudication(as_of=BEFORE_INTRO_EXPIRY)
    assert result["smallest_n"] == 5
    assert result["smallest_n"] == min(
        r["n"] for r in m.RECORDED_USAGE_PER_CALL.values()
    )
    thin = {r for r, v in result["per_role"].items() if v["n"] == 5}
    assert thin == {"rings", "synthetic"}


def test_the_projected_saving_shrinks_when_the_sonnet_promo_expires() -> None:
    """Eight of nine roles are on an introductory rate that lapses on 2026-09-01.

    The projection must move with the date, and the saving from the model change must
    SHRINK - because the new assignment is entirely on the promotional Sonnet rate while
    the old one had two rows on non-promotional Opus. A TCO slide built on today's figure
    overstates the post-expiry saving, which is what this asserts.
    """
    today = m.projected_cost_per_adjudication(as_of=BEFORE_INTRO_EXPIRY)
    lapsed = m.projected_cost_per_adjudication(as_of=AFTER_INTRO_EXPIRY)
    assert lapsed["projected_usd"] > today["projected_usd"]
    # Repriced at the same date on both sides, the change is still a saving - but a
    # materially smaller one once the promo lapses.
    assert today["delta_vs_prior_repriced_usd"] < 0
    assert lapsed["delta_vs_prior_repriced_usd"] < 0
    assert abs(lapsed["delta_vs_prior_repriced_usd"]) < abs(
        today["delta_vs_prior_repriced_usd"]
    )


def test_the_post_expiry_comparison_reprices_both_sides_not_just_one() -> None:
    """The apples-to-apples rule, which a fixed baseline constant would have broken.

    ``MEASURED_COST_PER_ADJUDICATION_PRIOR_ASSIGNMENT`` was measured on 2026-07-28, inside
    the introductory window. Differencing a post-expiry projection against it charges the
    new assignment the lapsed rate while crediting the old one the promo, which would
    manufacture a fake COST INCREASE of ~$0.021. The repriced delta must not do that, and
    the two deltas must coincide only at a date inside the window.
    """
    inside = m.projected_cost_per_adjudication(as_of=BEFORE_INTRO_EXPIRY)
    lapsed = m.projected_cost_per_adjudication(as_of=AFTER_INTRO_EXPIRY)

    # Inside the window both framings agree to within the sum-of-means tolerance.
    assert inside["delta_vs_prior_repriced_usd"] == pytest.approx(
        inside["delta_vs_prior_measured_mean_usd"], abs=1e-3
    )
    # After it they diverge, and only the naive one flips sign. This is the trap.
    assert lapsed["delta_vs_prior_measured_mean_usd"] > 0
    assert lapsed["delta_vs_prior_repriced_usd"] < 0
    assert m.MEASURED_COST_PER_ADJUDICATION_PRIOR_ASSIGNMENT["measured_as_of"] == "2026-07-28"

    # The prior side must be priced at OPUS's own tokens, not the new Sonnet counts.
    for domain in ("rings", "synthetic"):
        prior = m.RECORDED_USAGE_PER_CALL_PRIOR_ASSIGNMENT[domain]
        assert prior["model_id"] == m.HEAVY_MODEL, domain
        assert prior["usage"]["outputTokens"] != pytest.approx(
            m.RECORDED_USAGE_PER_CALL[domain]["usage"]["outputTokens"], rel=1e-6
        ), domain


@needs_pipeline
def test_the_prior_assignment_usage_matches_the_benchmark_that_measured_it() -> None:
    """Opus's own token counts, transcribed from the 42-run report rather than invented."""
    report = _load(PIPELINE_REPORT)
    for domain, record in m.RECORDED_USAGE_PER_CALL_PRIOR_ASSIGNMENT.items():
        measured = report["per_specialist"][domain]
        assert record["model_id"] == measured["model_id"], domain
        assert record["n"] == measured["tokens"]["n_calls_with_usage"], domain
        for key, value in record["usage"].items():
            assert value == pytest.approx(
                measured["tokens"]["mean_per_call"][key], rel=1e-12
            ), f"{domain}.{key}"


def test_the_projection_cannot_be_built_on_a_model_with_no_verified_rate() -> None:
    """A rate this repo never verified must raise, not be guessed at."""
    with pytest.raises(p.UnknownModelRateError):
        p.cost_usd(
            "global.anthropic.claude-opus-4-7",
            {"inputTokens": 100, "outputTokens": 50},
            as_of=BEFORE_INTRO_EXPIRY,
        )


# ---------------------------------------------------------------------------
# The customer-facing page cannot drift from the code
# ---------------------------------------------------------------------------

DOC = REPO_ROOT / "docs" / "model-selection.md"

needs_doc = pytest.mark.skipif(not DOC.is_file(), reason="docs/model-selection.md not present")


@needs_doc
def test_the_doc_states_the_assignment_the_code_actually_ships() -> None:
    """An SA takes this page to the customer, so it may not describe a stale assignment.

    The specific staleness this catches: the page used to list "synthetic, rings | Claude
    Opus 5" and describe tiering as justified from the prompt text. Both were true and both
    became false on 2026-07-28.
    """
    text = DOC.read_text()
    assert "There is no Opus 5 agent." in text
    # The retired claims must not survive anywhere on the page.
    assert "synthetic, rings | Claude Opus 5" not in text
    assert "is labelled unjustified where it does not" not in text
    # Every role and its cap appear.
    for role in m.ROLES:
        assert role in text, role
    for cap in sorted(set(m.MAX_TOKENS_BY_AGENT.values()) | {m.SYNTHESIZER_MAX_TOKENS}):
        assert str(cap) in text, cap
    # The three evidence grades are explained, because the table uses them.
    for grade in m.EVIDENCE_GRADES:
        assert grade in text, grade


@needs_doc
def test_the_doc_quotes_the_projection_the_code_computes() -> None:
    """The cost figures on the page are the ones ``projected_cost_per_adjudication`` returns.

    Pinned in both directions: the number must be in the doc, and it must be labelled a
    PROJECTION with the confirming command, so it cannot be lifted onto a slide as a
    measurement.
    """
    text = DOC.read_text()
    today = m.projected_cost_per_adjudication(as_of=BEFORE_INTRO_EXPIRY)
    lapsed = m.projected_cost_per_adjudication(as_of=AFTER_INTRO_EXPIRY)

    assert f"${today['projected_usd']:.4f}" in text
    assert f"${today['projected_specialists_usd']:.4f}" in text
    assert f"${today['prior_assignment_projected_usd']:.4f}" in text
    assert f"${lapsed['projected_usd']:.4f}" in text
    assert f"${lapsed['prior_assignment_projected_usd']:.4f}" in text

    # Labelled as a projection, with the run that would confirm it.
    assert "PROJECTION, not a measured end-to-end" in text
    assert "evals.bench --pipeline --repetitions 3" in text
    # And the measured baseline it is compared against, with its own status.
    prior = m.MEASURED_COST_PER_ADJUDICATION_PRIOR_ASSIGNMENT
    assert f"${prior['mean_usd']:.5f}" in text
    assert f"${prior['p50_usd']:.5f}" in text


@needs_doc
def test_the_doc_does_not_offer_a_percentile_the_thin_cells_cannot_support() -> None:
    """n=5 rows must show '—' for p95, not a number.

    The eval's own policy is p50 needs 5 samples and p95 needs 20. The two swept agents
    have five applications, so a p95 for them would be fabricated.
    """
    text = DOC.read_text()
    assert "22.05 s (n=5)" in text and "13.54 s (n=5)" in text
    # The row for rings carries an em-dash in the p95 column rather than a value.
    rings_row = next(line for line in text.splitlines() if line.startswith("| rings | Sonnet 5 |"))
    assert "| — |" in rings_row, rings_row
    assert "five samples do not support one" in text


@needs_doc
def test_the_doc_admits_every_unproven_thing_the_code_admits() -> None:
    """The 'what is still unproven' section must cover each UNPROVEN and HELD agent.

    An SA page that omits a gap is worse than one that never claimed coverage, because the
    customer's engineer finds it instead.
    """
    text = DOC.read_text()
    assert "## What is still unproven" in text
    unproven_section = text.split("## What is still unproven", 1)[1]
    for role in m.ROLES:
        if m.evidence_grade(role) in {"UNPROVEN", "HELD"}:
            assert role in unproven_section, role
    # The two never-judged LIGHT agents are a gap too, and must be named as one.
    for role in ("dealer", "straw"):
        assert role in unproven_section, role
    # The calibration failure no model fixed is the customer's own bar, so it ranks high.
    assert "APP-1014" in unproven_section and "APP-1004" in unproven_section
