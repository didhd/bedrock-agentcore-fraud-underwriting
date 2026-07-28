"""
Guards on the tail analysis, which exists to explain a 75-second run to an executive.

WHY THESE TESTS: the tail analysis is the artifact most exposed to the failure mode this
repo already shipped once -- a number that reads as measured but was reasoned into
existence. Its specific temptations are different from the benchmark's, so the guards are
different:

  1. A cap sweep that quotes a latency saving from a call that TRUNCATED. A truncated call
     returns sooner because it gave up; counting it converts a lost fraud dimension into a
     performance win. ``_cap_latency_effect`` must exclude those cells.
  2. A verbosity conclusion drawn from a correlation that two stall outliers inflated or
     destroyed. The pooled r over all calls is 0.85 and over the same calls without the two
     stalls is 0.98; reporting either alone misrepresents the sample.
  3. A "retries caused the tail" claim, or its opposite, asserted rather than computed from
     the wall-clock-minus-latencyMs gap.
  4. A correlation or percentile printed over a sample too small to support it.
  5. A band extractor that silently mis-parses a specialist heading, which would make the
     sweep's quality gate report agreement it never verified.
  6. A fan-out floor that does not equal the slowest specialist, i.e. an arithmetic error in
     the one decomposition the "which fix matters" argument rests on.

Offline only: every test reads the recorded run or a hand-built fixture. No AWS call, no
Bedrock inference, no spend.

Run:  MOCK_MODE=1 python -m pytest tests/test_tail_analysis.py -q
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

from evals import tail_analysis as ta  # noqa: E402

BENCH_PATH = REPO_ROOT / "evals" / "results" / "pipeline_14x3_us-east-1.json"
ANALYSIS_PATH = REPO_ROOT / "evals" / "results" / "tail_analysis.json"
MARKDOWN_PATH = REPO_ROOT / "evals" / "results" / "tail_analysis.md"


@pytest.fixture(scope="module")
def report() -> dict:
    return ta.load_report(BENCH_PATH)


@pytest.fixture(scope="module")
def runs(report: dict) -> list[ta.RunRecord]:
    return ta.parse_runs(report)


@pytest.fixture(scope="module")
def analysis() -> dict:
    if not ANALYSIS_PATH.exists():
        pytest.skip(f"{ANALYSIS_PATH} not generated yet")
    return json.loads(ANALYSIS_PATH.read_text())


def cell(
    *,
    app: str = "APP-1004",
    domain: str = "rings",
    max_tokens: int = 4096,
    wall: float = 20.0,
    out: int | None = 1500,
    chars: int | None = 4000,
    band: str | None = "HIGH RISK",
    truncated: bool = False,
    error: str | None = None,
    cost: float | None = 0.05,
    input_tokens: int | None = 1700,
) -> dict:
    """One sweep cell, in the shape ``run_cap_sweep`` produces."""
    return {
        "application_id": app,
        "domain": domain,
        "max_tokens": max_tokens,
        "wall_seconds": wall,
        "output_tokens": out,
        "input_tokens": input_tokens,
        "analysis_chars": chars,
        "band": band,
        "band_snippet": None,
        "stop_reason": "max_tokens" if truncated else "end_turn",
        "truncated": truncated,
        "error": error,
        "cost_usd": cost,
    }


# ---------------------------------------------------------------------------
# 1. A truncated call is never counted as a latency win
# ---------------------------------------------------------------------------


def test_truncated_cell_is_excluded_from_the_latency_comparison():
    """The load-bearing guard: finishing early by giving up is not being faster.

    The control takes 30s and the capped cell "finishes" in 5s because it truncated. If that
    cell reached the latency statistics it would read as a 25s saving.
    """
    rows = [
        cell(max_tokens=4096, wall=30.0),
        cell(max_tokens=1024, wall=5.0, out=None, chars=None, band=None, truncated=True, cost=None),
    ]
    effect = ta._cap_latency_effect(rows, control_cap=4096)

    assert effect["n_comparable_cells"] == 0, "a truncated cell must not be comparable"
    assert effect["n_excluded_because_they_truncated"] == 1
    assert effect["delta_seconds"]["n"] == 0
    # And no summary statistic may exist for it.
    assert effect["delta_seconds"]["mean"] is None
    assert effect["n_faster_than_control"] == 0


def test_completed_cell_is_compared_and_direction_is_recorded():
    rows = [
        cell(max_tokens=4096, wall=30.0),
        cell(max_tokens=2048, wall=22.0, out=2000),
        cell(max_tokens=3072, wall=34.0, out=2500),
    ]
    effect = ta._cap_latency_effect(rows, control_cap=4096)

    assert effect["n_comparable_cells"] == 2
    assert effect["n_faster_than_control"] == 1
    assert effect["n_slower_than_control"] == 1
    deltas = {c["max_tokens"]: c["delta_seconds"] for c in effect["comparable_cells"]}
    assert deltas[2048] == pytest.approx(-8.0)
    assert deltas[3072] == pytest.approx(4.0)


def test_a_cell_whose_control_truncated_is_not_compared():
    """Without a valid control there is no baseline, so there is no delta to report."""
    rows = [
        cell(max_tokens=4096, wall=30.0, out=None, band=None, truncated=True, cost=None),
        cell(max_tokens=2048, wall=10.0),
    ]
    effect = ta._cap_latency_effect(rows, control_cap=4096)
    assert effect["n_comparable_cells"] == 0
    assert effect["comparable_cells"] == []


def test_truncated_call_is_reported_as_a_cost_not_a_saving():
    """A truncated call spends wall clock and money and returns no analysis."""
    rows = [
        cell(max_tokens=4096, wall=22.0),
        cell(max_tokens=1024, wall=15.0, out=None, chars=None, band=None, truncated=True, cost=None),
        cell(max_tokens=1536, wall=18.0, out=None, chars=None, band=None, truncated=True, cost=None),
    ]
    trunc = ta._truncation_cost(rows)

    assert trunc["n_truncated"] == 2
    assert trunc["n_completed"] == 1
    assert trunc["total_wall_seconds_burned_for_no_analysis"] == pytest.approx(33.0)
    # Real money, bounded from the cap that triggered the exception.
    assert trunc["bounded_cost_of_truncated_calls_usd"] > 0
    for entry in trunc["per_truncated_call"]:
        assert entry["analysis_returned"] is False
        assert "output tokens assumed = cap" in entry["cost_basis"]


def test_failure_boundary_reports_the_lowest_all_clear_cap_not_the_lowest_that_worked_once():
    """A cap that truncated on ANY application is not a safe cap.

    This is the non-monotonic case the live sweep actually produced: 1536 completed on one
    application and truncated on another. Reporting 1536 as safe would be the whole error.
    """
    rows = [
        cell(app="APP-1004", max_tokens=1536, wall=20.0, out=None, band=None, truncated=True, cost=None),
        cell(app="APP-1013", max_tokens=1536, wall=14.0, out=1400),
        cell(app="APP-1004", max_tokens=3072, wall=30.0, out=2200),
        cell(app="APP-1013", max_tokens=3072, wall=25.0, out=1800),
    ]
    boundary = ta._failure_boundary(rows)["per_domain"]["rings"]

    assert 1536 in boundary["caps_that_truncated"]
    assert 1536 in boundary["caps_that_completed"], "the overlap is the finding, keep both"
    assert boundary["lowest_cap_that_completed_every_cell"] == 3072


# ---------------------------------------------------------------------------
# 2. The verbosity conclusion carries both fits
# ---------------------------------------------------------------------------


def test_stalls_are_separated_from_the_verbosity_fit(runs):
    """Both fits must be present, and the stall-excluded one must be materially tighter.

    If only one r were reported, the tail would be attributed to the wrong mechanism: the
    all-calls fit understates how linear generation is, and the stall-excluded fit alone
    would hide that two calls do not obey it.
    """
    calls = ta.all_calls(runs)
    fits = ta.latency_vs_output(calls)

    all_calls_fit = fits["pooled_all_calls"]
    excl = fits["pooled_excluding_stalls"]
    assert all_calls_fit["r_squared"] is not None
    assert excl["r_squared"] is not None
    assert excl["n"] < all_calls_fit["n"], "stalls must actually be removed"
    assert excl["r_squared"] > all_calls_fit["r_squared"], (
        "removing the stalls must tighten the fit; if it does not, the stall threshold is "
        "not selecting the anomaly it claims to"
    )
    # A slope in ms per token has to be positive and physically plausible.
    assert 1.0 < excl["slope"] < 100.0
    assert excl["marginal_output_tokens_per_second"] > 0


def test_stall_discriminator_is_tokens_per_second_not_wall_clock_alone(runs):
    """A stall must be slow PER TOKEN, otherwise it is merely a verbose call."""
    st = ta.stalls(ta.all_calls(runs))
    healthy_p50 = st["healthy_call_output_tokens_per_second"]["p50"]

    assert st["n_stalls"] >= 1
    for row in st["stalls"]:
        assert row["output_tokens_per_second"] < healthy_p50 / 2, (
            "a call flagged as a stall must generate far slower per token than a healthy "
            "call, or the label is describing verbosity"
        )


def test_prompt_cap_contrast_uses_the_real_prompt_text():
    """The capped/uncapped split must match the customer's verbatim prompts on disk.

    If bustout or rings ever gains a character cap, the structural claim changes and this
    test is where that is noticed.
    """
    from prompts.loader import SPECIALIST_PROMPTS

    for domain, cap in ta.PROMPT_CHARACTER_CAP.items():
        text = SPECIALIST_PROMPTS[domain]
        has_cap = "4000 characters" in text
        assert has_cap == (cap is not None), (
            f"{domain}: PROMPT_CHARACTER_CAP says {cap!r} but the verbatim prompt "
            f"{'does' if has_cap else 'does not'} carry a 4000-character rule"
        )


def test_uncapped_prompts_write_more_than_capped_ones(runs):
    """The structural hypothesis, checked against the measurement rather than asserted."""
    fits = ta.latency_vs_output(ta.all_calls(runs))
    contrast = fits["prompt_cap_contrast"]
    capped = contrast["capped_by_prompt"]["output_tokens"]["p50"]
    uncapped = contrast["no_cap_in_prompt"]["output_tokens"]["p50"]
    assert uncapped > capped
    # And the exceedance count must be a real count, not a placeholder string.
    for key in ("capped_by_prompt", "no_cap_in_prompt"):
        assert isinstance(contrast[key]["n_calls_over_4000_chars"], int)


def test_cap_binding_census_claims_only_what_history_can_show(runs):
    """The census counts observed output lengths; it must not imply a latency saving."""
    census = ta.cap_binding_census(ta.all_calls(runs))
    text = census["claim_boundary"].lower()
    assert "not measured" in text
    assert "latency" in text
    # The shipped 4096 must bind on nothing in this sample -- that is why it was chosen.
    row_4096 = next(r for r in census["rows"] if r["max_tokens"] == 4096)
    assert row_4096["n_calls_exceeding"] == 0
    # And a lower cap must bind on something, or the census is answering nothing.
    row_1024 = next(r for r in census["rows"] if r["max_tokens"] == 1024)
    assert row_1024["n_calls_exceeding"] > 0


def test_chars_per_token_records_the_divergence_between_the_two_constraints(runs):
    """A character cap and a token cap differ, and the report must show where."""
    ratios = ta.latency_vs_output(ta.all_calls(runs))["chars_per_output_token"]
    per_domain = ratios["per_domain"]
    assert set(per_domain) <= set(ta.DOMAIN_ORDER)
    # At least one agent must show a collapsed ratio, which is the reason the section exists.
    assert min(row["min"] for row in per_domain.values()) < 1.5
    assert "not the same constraint" in ratios["why_it_matters"]


# ---------------------------------------------------------------------------
# 3. The retry verdict is computed, and it matches the shipped strategy
# ---------------------------------------------------------------------------


def test_retry_ladder_is_read_from_the_shipped_constants():
    """The ladder in the report must be the one the code will actually execute.

    strands doubles from ``initial_delay`` and clamps at ``max_delay`` over
    ``max_attempts - 1`` retries. With 4/1/8 that is [1,2,4] = 7s, NOT the [1,2,4,8]=15s a
    reader might assume from the constants alone.
    """
    from agents.models import RETRY_INITIAL_DELAY_S, RETRY_MAX_ATTEMPTS, RETRY_MAX_DELAY_S

    config = ta._retry_strategy_config()
    assert config["max_attempts"] == RETRY_MAX_ATTEMPTS
    assert config["initial_delay_s"] == RETRY_INITIAL_DELAY_S
    assert config["max_delay_s"] == RETRY_MAX_DELAY_S
    assert len(config["backoff_ladder_s"]) == RETRY_MAX_ATTEMPTS - 1
    assert config["backoff_ladder_s"][0] == RETRY_INITIAL_DELAY_S
    assert max(config["backoff_ladder_s"]) <= RETRY_MAX_DELAY_S
    assert config["worst_case_added_wall_clock_s"] == sum(config["backoff_ladder_s"])


def test_retry_ladder_clamps_at_max_delay(monkeypatch):
    """The clamp must be applied, not just present in the constants.

    With the shipped 4/1/8 the ladder is [1,2,4] and never reaches the ceiling, so the clamp
    is untested by the real config. Forcing more attempts is the only way to prove the
    reported ladder would still be right if someone raised ``RETRY_MAX_ATTEMPTS`` -- an
    unclamped doubling would print [1,2,4,8,16,32] and overstate the worst case by 4x.
    """
    from agents import models

    monkeypatch.setattr(models, "RETRY_MAX_ATTEMPTS", 7)
    monkeypatch.setattr(models, "RETRY_INITIAL_DELAY_S", 1)
    monkeypatch.setattr(models, "RETRY_MAX_DELAY_S", 8)

    config = ta._retry_strategy_config()
    assert config["backoff_ladder_s"] == [1, 2, 4, 8, 8, 8]
    assert config["worst_case_added_wall_clock_s"] == 31


def test_retry_verdict_is_derived_from_measured_gaps_not_asserted(runs):
    evidence = ta.retry_evidence(runs)

    assert evidence["n_calls_with_both_clocks"] > 0
    gaps = evidence["unaccounted_wall_clock_ms"]
    assert gaps["p50_ms"] is not None
    # The verdict must reference the throttle count it is based on.
    assert str(evidence["throttling_exceptions"]["n_throttling_exception"]) in evidence["verdict"]
    # And it must state that only throttles are retried, which is what makes it conclusive.
    assert "ModelThrottledException" in evidence["retry_trigger"]


def test_retry_verdict_flips_when_a_throttle_is_present(runs):
    """The verdict must be a function of the data, so injecting a throttle must change it."""
    clean = ta.retry_evidence(runs)
    assert "RULED OUT" in clean["verdict"]

    poisoned = list(runs)
    victim = poisoned[0]
    throttled_call = ta.SpecialistCall(
        application_id=victim.application_id,
        repetition=victim.repetition,
        domain="rings",
        tier="heavy",
        model_id="global.anthropic.claude-opus-5",
        wall_ms=30_000.0,
        latency_ms=20_000.0,
        output_tokens=None,
        input_tokens=None,
        analysis_chars=None,
        stop_reason=None,
        error="ThrottlingException: Too many requests",
    )
    poisoned[0] = ta.RunRecord(
        application_id=victim.application_id,
        repetition=victim.repetition,
        e2e_ms=victim.e2e_ms,
        fanout_ms=victim.fanout_ms,
        synthesis_ms=victim.synthesis_ms,
        slowest_specialist=victim.slowest_specialist,
        slowest_specialist_ms=victim.slowest_specialist_ms,
        degraded_domains=("rings",),
        failure_kinds=("rings: throttled",),
        repair_attempts=0,
        validated=True,
        overall_risk_decision=victim.overall_risk_decision,
        specialists=victim.specialists + (throttled_call,),
    )
    dirty = ta.retry_evidence(poisoned)
    assert "RULED OUT" not in dirty["verdict"]
    assert dirty["throttling_exceptions"]["n_throttling_exception"] == 1


def test_the_degraded_run_is_reported_as_fast_not_slow(runs):
    """A 5xx fails fast: it costs a fraud dimension, not time. Never conflate the two."""
    evidence = ta.retry_evidence(runs)
    degraded = evidence["degraded_runs"]
    assert degraded, "the recorded run has one degraded run; it must not be dropped"
    median_e2e = sorted(r.e2e_ms for r in runs)[len(runs) // 2] / 1000.0
    for entry in degraded:
        assert entry["e2e_seconds"] < median_e2e, (
            "the 5xx-degraded run is faster than median because the failure is immediate; "
            "if this ever inverts, the failure mode changed and the narrative must too"
        )
        assert entry["failed_calls"], "the failing domains must be named, not summarised away"


# ---------------------------------------------------------------------------
# 4. The floor decomposition is arithmetically sound
# ---------------------------------------------------------------------------


def test_fanout_floor_equals_the_slowest_specialist_in_every_run(runs):
    """asyncio.gather cannot return before its slowest member. Verify, do not assume."""
    for run in runs:
        walls = [c.wall_ms for c in run.specialists]
        assert run.slowest_specialist_ms == pytest.approx(max(walls), rel=1e-6)
        assert run.fanout_ms >= run.slowest_specialist_ms, (
            f"{run.application_id} rep {run.repetition}: fan-out finished before its "
            "slowest member, which is impossible"
        )


def test_layers_sum_to_end_to_end(runs):
    """floor + orchestration + post-fan-out must reconstruct end-to-end exactly."""
    for run in runs:
        reconstructed = (
            run.slowest_specialist_ms + run.orchestration_overhead_ms + run.post_fanout_ms
        )
        assert reconstructed == pytest.approx(run.e2e_ms, rel=1e-9)


def test_orchestration_overhead_is_small_and_positive(runs):
    """The claim 'there is nothing to win in orchestration' has to be checked."""
    floor = ta.fanout_floor(runs)
    overhead = floor["orchestration_overhead_ms"]
    assert overhead["min_ms"] >= 0.0
    assert overhead["p95_ms"] < 5_000.0, (
        "if gathering ever costs seconds, orchestration IS a lever and the report's "
        "conclusion changes"
    )
    assert floor["floor_share_of_fanout_pct_median"] > 50.0


def test_single_agent_fix_ceiling_is_bounded_by_the_second_slowest_agent(runs):
    """No single-agent fix can recover more than the gap to the next agent."""
    headroom = ta.fanout_floor(runs)["headroom_if_floor_were_the_second_slowest_agent"]["ms"]
    assert headroom["min_ms"] >= 0.0
    floor = ta.fanout_floor(runs)["fanout_floor_ms"]
    assert headroom["p50_ms"] < floor["p50_ms"], (
        "the recoverable headroom must be smaller than the floor itself, otherwise the "
        "second-slowest agent is being ignored"
    )


# ---------------------------------------------------------------------------
# 5. Percentiles and correlations refuse samples that cannot support them
# ---------------------------------------------------------------------------


def test_correlation_is_refused_below_the_minimum_sample():
    xs = [1.0, 2.0, 3.0]
    ys = [10.0, 20.0, 30.0]
    out = ta.summarize_correlation(xs, ys, label="tiny", x_unit="tok", y_unit="ms")
    assert out["r"] is None
    assert out["slope"] is None
    assert "refused" in out["suppressed"]
    assert str(ta.CORRELATION_MIN_SAMPLES) in out["suppressed"]


def test_correlation_is_reported_at_or_above_the_minimum_sample():
    n = ta.CORRELATION_MIN_SAMPLES
    xs = [float(i) for i in range(n)]
    ys = [3.0 * i + 7.0 for i in range(n)]
    out = ta.summarize_correlation(xs, ys, label="line", x_unit="tok", y_unit="ms")
    assert out["suppressed"] is None
    assert out["r"] == pytest.approx(1.0)
    assert out["slope"] == pytest.approx(3.0)
    assert out["intercept"] == pytest.approx(7.0)


def test_zero_variance_yields_no_correlation_rather_than_a_crash():
    n = ta.CORRELATION_MIN_SAMPLES
    out = ta.summarize_correlation(
        [5.0] * n, [float(i) for i in range(n)], label="flat", x_unit="tok", y_unit="ms"
    )
    assert out["r"] is None
    assert out["slope"] is None


def test_variance_decomposition_shares_sum_to_one_hundred(runs):
    var = ta.variance_decomposition(runs)
    assert var["between_application_share_pct"] + var["within_application_share_pct"] == (
        pytest.approx(100.0, abs=0.2)
    )
    # It must refuse to call itself a significance test.
    assert "not an F test" in var["method"]


def test_variance_decomposition_detects_a_pure_within_application_signal():
    """A synthetic layout with identical application means must attribute ~0% between."""
    made = [
        ta.RunRecord(
            application_id=app,
            repetition=rep,
            e2e_ms=e2e,
            fanout_ms=e2e - 4000.0,
            synthesis_ms=4000.0,
            slowest_specialist="rings",
            slowest_specialist_ms=e2e - 5000.0,
            degraded_domains=(),
            failure_kinds=(),
            repair_attempts=0,
            validated=True,
            overall_risk_decision="LOW RISK",
            specialists=(),
        )
        for app in ("A", "B")
        for rep, e2e in enumerate((20_000.0, 30_000.0, 40_000.0), start=1)
    ]
    var = ta.variance_decomposition(made)
    assert var["between_application_share_pct"] == pytest.approx(0.0, abs=0.01)
    assert var["within_application_share_pct"] == pytest.approx(100.0, abs=0.01)


# ---------------------------------------------------------------------------
# 6. The band extractor, which the quality gate depends on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # Every shape below was emitted by a real specialist in this repo's live runs.
        ("# FRAUD RING DETECTION ANALYSIS\n\n## RISK CLASSIFICATION: **HIGH RISK**\n", "HIGH RISK"),
        ("## Fraud Ring Risk Analysis: HIGH RISK\n\n### Summary\n", "HIGH RISK"),
        ("# Fraud Ring Risk Analysis\n\n## VERDICT: **LOW RISK**\n", "LOW RISK"),
        ("# Synthetic Identity Fraud Risk Analysis\n\n**Classification: LOW RISK**\n", "LOW RISK"),
        ("# X\n\n## Assessment: **POSSIBLE RISK**\n", "POSSIBLE RISK"),
        ("## Fraud Ring Risk Analysis — POSSIBLE RISK\n", "POSSIBLE RISK"),
        ("**RISK CLASSIFICATION: POSSIBLE RISK**\n", "POSSIBLE RISK"),
    ],
)
def test_band_extractor_handles_every_measured_heading_shape(text, expected):
    assert ta.extract_specialist_band(text)["band"] == expected


def test_possible_risk_is_not_shadowed_by_a_substring_match():
    """'POSSIBLE RISK' contains no other band, but a careless pattern could match 'RISK'."""
    out = ta.extract_specialist_band("## Assessment: POSSIBLE RISK\n\nLOW RISK would be wrong.")
    assert out["band"] == "POSSIBLE RISK"


def test_band_extractor_returns_none_rather_than_guessing():
    """A missing band is a finding -- a truncated analysis lost its classification."""
    out = ta.extract_specialist_band("The analysis was cut off mid-sen")
    assert out["band"] is None
    assert out["n_band_mentions"] == 0
    assert ta.extract_specialist_band("")["band"] is None


def test_band_extractor_keeps_an_auditable_snippet():
    out = ta.extract_specialist_band("# Heading\n\n## VERDICT: **HIGH RISK**\n\nmore text")
    assert out["band"] == "HIGH RISK"
    assert "VERDICT" in out["snippet"], "a human must be able to audit the extraction"


def test_extracted_bands_are_in_the_specialist_vocabulary():
    """The specialists use LOW/POSSIBLE/HIGH, not the master's MEDIUM. Do not mix them."""
    from agents.contracts import SPECIALIST_RISK_VALUES

    for value in SPECIALIST_RISK_VALUES:
        assert ta.extract_specialist_band(f"## VERDICT: {value}")["band"] == value
    # The master's mid-band must NOT be extractable, or the two vocabularies are conflated.
    assert ta.extract_specialist_band("## VERDICT: MEDIUM RISK")["band"] is None


def test_band_agreement_counts_a_lost_band_as_a_disagreement():
    rows = [
        cell(max_tokens=4096, band="HIGH RISK"),
        cell(max_tokens=1024, band=None, truncated=True, out=None, cost=None),
        cell(max_tokens=2048, band="POSSIBLE RISK"),
        cell(max_tokens=3072, band="HIGH RISK"),
    ]
    control = [r for r in rows if r["max_tokens"] == 4096]
    agreement = ta._band_agreement(rows, control)

    assert agreement["n_comparisons"] == 4
    assert agreement["n_band_lost_entirely"] == 1
    assert agreement["n_disagreeing"] == 2
    assert agreement["n_agreeing"] == 2  # the control agrees with itself, plus 3072


# ---------------------------------------------------------------------------
# 7. The live sweep is gated, and its plan states the spend first
# ---------------------------------------------------------------------------


def test_live_sweep_requires_the_environment_gate(monkeypatch, tmp_path):
    """--live without the env gate must refuse rather than spend."""
    monkeypatch.delenv(ta.LIVE_ENV_GATE, raising=False)
    rc = ta.main(
        [
            "--report",
            str(BENCH_PATH),
            "--out",
            str(tmp_path / "out.json"),
            "--live",
            "--applications",
            "APP-1004",
            "--domains",
            "rings",
            "--caps",
            "4096",
        ]
    )
    assert rc == 2, "a live run without the gate must be refused"
    assert not (tmp_path / "out.json").exists(), "a refused run must write nothing"


def test_dry_run_states_the_spend_and_calls_nothing(capsys, tmp_path):
    rc = ta.main(
        [
            "--report",
            str(BENCH_PATH),
            "--out",
            str(tmp_path / "out.json"),
            "--dry-run",
            "--domains",
            "bustout,rings",
            "--applications",
            "APP-1004,APP-1012,APP-1013",
            "--caps",
            "1024,4096",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "12 (2 domains x 3 apps x 2 caps)" in out
    assert "worst-case spend" in out
    assert "nothing was called, nothing was spent" in out


def test_sweep_plan_prices_from_measured_priors_and_says_so():
    plan = ta.cap_sweep_plan(
        ["rings"],
        ["APP-1004"],
        [4096],
        region="us-east-1",
        prior_cost_by_domain=ta.PRIOR_COST_PER_CALL_USD,
    )
    assert "MEASURED" in plan
    assert "upper bound" in plan
    assert "quality gate" in plan


def test_prior_costs_cover_every_domain():
    """A missing domain would silently under-price the plan a human approves."""
    assert set(ta.PRIOR_COST_PER_CALL_USD) == set(ta.DOMAIN_ORDER)
    assert all(v > 0 for v in ta.PRIOR_COST_PER_CALL_USD.values())


# ---------------------------------------------------------------------------
# 8. The published artifacts are internally consistent
# ---------------------------------------------------------------------------


def test_offline_analysis_runs_end_to_end_and_writes_both_artifacts(tmp_path):
    out = tmp_path / "tail.json"
    rc = ta.main(["--report", str(BENCH_PATH), "--out", str(out)])
    assert rc == 0
    assert out.exists() and out.with_suffix(".md").exists()
    written = json.loads(out.read_text())
    assert written["schema"] == ta.SCHEMA
    for key in (
        "question_1_where_is_the_tail",
        "question_2_is_it_output_length",
        "question_3_is_it_retries_or_throttling",
        "question_4_theoretical_floor",
    ):
        assert key in written
    # Without --live there must be no cap-sweep claim at all.
    assert "live_cap_sweep" not in written
    assert "NOT RUN" in out.with_suffix(".md").read_text()


def test_published_artifacts_contain_no_absolute_developer_paths(analysis):
    """A committed artifact must not name whoever happened to run it."""
    assert not analysis["source"]["path"].startswith("/")
    assert analysis["source"]["path"] == "evals/results/pipeline_14x3_us-east-1.json"
    if MARKDOWN_PATH.exists():
        assert "/Users/" not in MARKDOWN_PATH.read_text()
    assert "/Users/" not in ANALYSIS_PATH.read_text()


def test_published_analysis_matches_the_source_run(analysis, report):
    """The artifact must describe the run it names, not a stale one."""
    assert analysis["source"]["n_runs"] == report["totals"]["runs"]
    assert analysis["source"]["n_specialist_calls"] == report["totals"]["specialist_calls"]
    assert analysis["source"]["run"]["git_sha"] == report["run"]["git_sha"]


def test_published_markdown_has_not_drifted_from_its_json(analysis):
    """The markdown is what a human reads; if it drifts from the JSON it is fiction.

    ``tests/test_bench.py`` guards this for pipeline-bench reports only, so the tail
    analysis needs its own. Re-rendering from the committed JSON must reproduce the
    committed markdown byte for byte.
    """
    if not MARKDOWN_PATH.exists():
        pytest.skip("markdown not generated yet")
    expected = ta.render_markdown(analysis)
    assert MARKDOWN_PATH.read_text().strip() == expected.strip(), (
        "tail_analysis.md has drifted from tail_analysis.json; re-render it from the "
        "committed JSON rather than editing the markdown by hand"
    )


def test_published_markdown_states_what_was_not_established():
    """The refusals are as load-bearing as the findings for this audience."""
    if not MARKDOWN_PATH.exists():
        pytest.skip("markdown not generated yet")
    text = MARKDOWN_PATH.read_text()
    assert "did NOT establish" in text
    assert "irreducible" in text.lower()
    # No p99 may appear over a 42-run sample.
    assert "p99" not in text.split("What this analysis did NOT establish")[0]


def test_published_sweep_never_reports_a_saving_from_a_truncated_cell(analysis):
    sweep = analysis.get("live_cap_sweep")
    if not sweep:
        pytest.skip("no live sweep in the published artifact")
    effect = sweep["does_a_lower_cap_shorten_the_call"]
    truncated_keys = {
        (c["application_id"], c["domain"], c["max_tokens"])
        for c in sweep["per_cell"]
        if c["truncated"] or c["error"]
    }
    for entry in effect["comparable_cells"]:
        key = (entry["application_id"], entry["domain"], entry["max_tokens"])
        assert key not in truncated_keys, f"{key} truncated but reached the latency comparison"


def test_published_sweep_records_the_failure_boundary_it_observed(analysis):
    sweep = analysis.get("live_cap_sweep")
    if not sweep:
        pytest.skip("no live sweep in the published artifact")
    for domain, row in sweep["failure_boundary"]["per_domain"].items():
        assert row["caps_tested"], f"{domain} has no caps tested"
        # The whole point of the sweep: a boundary was actually found.
        assert row["caps_that_truncated"], (
            f"{domain} truncated at no cap, so the sweep found no boundary and the report "
            "must not claim one"
        )


def test_published_sweep_cost_only_counts_calls_that_returned_usage(analysis):
    sweep = analysis.get("live_cap_sweep")
    if not sweep:
        pytest.skip("no live sweep in the published artifact")
    measured = sum(
        float(c["cost_usd"]) for c in sweep["per_cell"] if c["cost_usd"] is not None
    )
    assert sweep["total_measured_cost_usd"] == pytest.approx(measured, rel=1e-6)
    # Truncated cells must carry no measured cost, only a bounded one.
    for c in sweep["per_cell"]:
        if c["truncated"] or c["error"]:
            assert c["cost_usd"] is None
    assert sweep["truncation_is_not_a_saving"]["bounded_cost_of_truncated_calls_usd"] > 0


def test_no_mock_data_can_reach_the_analysis(runs):
    """Every call in the source run must have come from Bedrock, not MOCK_MODE."""
    report = ta.load_report(BENCH_PATH)
    assert report["workload"]["mock"] is False
    for row in report["raw_runs"]:
        for spec in row["specialists"]:
            assert spec["source"] == "bedrock", (
                "a mock specialist result in the latency sample would make every "
                "percentile in this analysis fiction"
            )
