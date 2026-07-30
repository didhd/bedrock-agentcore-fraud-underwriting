"""
Contract tests for the bedrock-mantle path (OpenAI GPT-5.x on Amazon Bedrock).

Every test here is offline. The point is not to prove the endpoint works -- that was
verified live and the measurements are recorded in CLAUDE.md -- but to pin the things
that were WRONG on the way there and would silently regress:

  * the endpoint host is ``bedrock-mantle.{region}.api.aws``; the ``.amazonaws.com``
    form does not resolve and looks like an auth failure
  * cache-WRITE tokens must be priced at 1.25x input for 5.6. Dropping that term
    under-reported a measured synthesis by 62%
  * Responses ``input_tokens`` includes cached tokens; the Converse convention does
    not, so a cached token must be billed once, not twice
  * Sol is not available in us-west-2
  * a GPT id must never be priced off the Claude rate card
  * the synthesizer region must come from the MEASUREMENT, not from AWS_REGION. With
    AWS_REGION=us-west-2 the old resolver sent every synthesis to the 5.02s region
    instead of the 4.61s one, silently
  * the third-party short-prompt readings must stay labelled as third-party: they rank
    us-west-2 first for Luna, which is the opposite of our measurement on the real
    prompt, and they must never select a region
"""

from __future__ import annotations

import datetime as _dt

import pytest

from agents import mantle


@pytest.fixture(autouse=True)
def _clean_region_env(monkeypatch):
    """Region resolution reads the environment, so no test may inherit the shell's.

    This host runs with AWS_REGION=us-west-2, which is exactly the value the measured
    default must ignore -- so leaving it set in one direction or the other would make
    these assertions accidental.
    """
    monkeypatch.delenv("BEDROCK_MANTLE_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    mantle.reset_region_log()
    yield
    mantle.reset_region_log()


# --- routing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("openai.gpt-5.6-luna", True),
        ("openai.gpt-5.6-terra", True),
        ("openai.gpt-5.6-sol", True),
        ("openai.gpt-5.5", True),
        ("global.anthropic.claude-sonnet-5", False),
        ("us.anthropic.claude-opus-5", False),
        ("openai.gpt-oss-120b-1:0", False),
    ],
)
def test_only_gpt5_ids_route_to_mantle(model_id, expected):
    """gpt-oss models ARE on Converse; only the GPT-5.x family needs the mantle path."""
    assert mantle.is_mantle_model(model_id) is expected


def test_sol_is_not_available_in_us_west_2():
    """Sol is us-east-1/us-east-2 only; asking for us-west-2 must not silently 404."""
    assert "us-west-2" not in mantle.MANTLE_REGIONS["openai.gpt-5.6-sol"]
    assert mantle.resolve_mantle_region("openai.gpt-5.6-sol", "us-west-2") in (
        "us-east-1",
        "us-east-2",
    )


def test_luna_and_terra_keep_a_requested_supported_region():
    for model_id in ("openai.gpt-5.6-luna", "openai.gpt-5.6-terra"):
        assert mantle.resolve_mantle_region(model_id, "us-west-2") == "us-west-2"
        assert mantle.resolve_mantle_region(model_id, "us-east-1") == "us-east-1"


def test_there_is_no_global_or_us_prefix():
    """Unlike the Claude ids, mantle ids are bare: no cross-region inference exists."""
    for model_id in mantle.MANTLE_RATES_PER_MTOK:
        assert not model_id.startswith(("us.", "global.", "eu."))


# --- pricing -----------------------------------------------------------------


def test_cache_write_is_priced_at_1_25x_input_for_5_6():
    """THE REGRESSION THIS FILE EXISTS FOR.

    A measured Luna synthesis reported inputTokens=2, cacheWriteInputTokens=6740,
    outputTokens=866. Dropping the write term reported $0.00572 for a $0.01499 call.
    """
    usage = {
        "inputTokens": 2,
        "outputTokens": 866,
        "cacheReadInputTokens": 0,
        "cacheWriteInputTokens": 6740,
    }
    cost = mantle.cost_usd("openai.gpt-5.6-luna", usage)
    rate_in, _, rate_out = mantle.rates_for("openai.gpt-5.6-luna")
    expected = (2 * rate_in + 6740 * rate_in * 1.25 + 866 * rate_out) / 1_000_000
    assert cost == pytest.approx(expected)
    # And it must be materially more than the write-free figure.
    write_free = (2 * rate_in + 866 * rate_out) / 1_000_000
    assert cost > write_free * 2


def test_gpt_5_4_and_5_5_have_no_cache_write_charge():
    """They cache automatically with no separate write price."""
    for model_id in ("openai.gpt-5.4", "openai.gpt-5.5"):
        assert mantle.CACHE_WRITE_MULTIPLIER[model_id] == 0.0
        usage = {"inputTokens": 10, "outputTokens": 10, "cacheWriteInputTokens": 100_000}
        rate_in, _, rate_out = mantle.rates_for(model_id)
        assert mantle.cost_usd(model_id, usage) == pytest.approx(
            (10 * rate_in + 10 * rate_out) / 1_000_000
        )


def test_cache_read_is_a_90_percent_discount():
    for model_id, (rate_in, rate_cache, _) in mantle.MANTLE_RATES_PER_MTOK.items():
        assert rate_cache == pytest.approx(rate_in * 0.10, rel=0.02), model_id


def test_warm_call_is_cheaper_than_the_cold_one_that_populated_it():
    cold = {"inputTokens": 2, "outputTokens": 800, "cacheWriteInputTokens": 6740}
    warm = {"inputTokens": 2, "outputTokens": 800, "cacheReadInputTokens": 6740}
    assert mantle.cost_usd("openai.gpt-5.6-luna", warm) < mantle.cost_usd(
        "openai.gpt-5.6-luna", cold
    )


def test_missing_usage_yields_none_not_a_fabricated_figure():
    assert mantle.cost_usd("openai.gpt-5.6-luna", None) is None
    assert mantle.cost_usd("openai.gpt-5.6-luna", {}) is None


def test_an_unpriced_model_raises_rather_than_guessing():
    with pytest.raises(mantle.MantleUnavailable):
        mantle.rates_for("openai.gpt-9.9-imaginary")


def test_luna_is_the_cheapest_and_sol_the_most_expensive():
    rates = {m: r[0] for m, r in mantle.MANTLE_RATES_PER_MTOK.items()}
    assert rates["openai.gpt-5.6-luna"] < rates["openai.gpt-5.6-terra"]
    assert rates["openai.gpt-5.6-terra"] < rates["openai.gpt-5.6-sol"]


# --- usage normalisation -----------------------------------------------------


class _Details:
    def __init__(self, cached=0, written=0):
        self.cached_tokens = cached
        self.cache_write_tokens = written


class _Usage:
    def __init__(self, total_in, out, cached=0, written=0):
        self.input_tokens = total_in
        self.output_tokens = out
        self.input_tokens_details = _Details(cached, written)


class _Response:
    def __init__(self, usage):
        self.usage = usage
        self.output_text = "{}"


def test_input_tokens_are_made_disjoint_so_a_cached_token_is_billed_once():
    """Responses input_tokens INCLUDES cached; Converse does not. Normalise to Converse."""
    normalized = mantle._usage(_Response(_Usage(4508, 100, cached=3193)))
    assert normalized["inputTokens"] == 4508 - 3193
    assert normalized["cacheReadInputTokens"] == 3193
    # The three input classes must reconcile to the API's total.
    assert (
        normalized["inputTokens"]
        + normalized["cacheReadInputTokens"]
        + normalized["cacheWriteInputTokens"]
    ) == 4508


def test_normalisation_never_returns_a_negative_input_count():
    normalized = mantle._usage(_Response(_Usage(100, 10, cached=80, written=80)))
    assert normalized["inputTokens"] == 0


def test_absent_usage_is_none():
    assert mantle._usage(_Response(None)) is None


# --- effort ------------------------------------------------------------------


def test_effort_values_match_the_documented_set():
    assert mantle.MANTLE_EFFORTS == ("none", "low", "medium", "high", "xhigh", "max")
    assert mantle.DEFAULT_EFFORT == "low"


def test_an_invalid_effort_is_refused_before_any_network_call():
    with pytest.raises(mantle.MantleUnavailable):
        mantle.complete(
            "openai.gpt-5.6-luna", "hello", max_output_tokens=16, effort="turbo"
        )


# --- integration with the synthesizer ---------------------------------------


def test_synthesize_prices_a_gpt_synthesizer_off_the_mantle_card(monkeypatch):
    """A GPT id must not be priced by agents.pricing, which only knows Claude rates."""
    import agents.synthesize as S

    monkeypatch.setattr(S, "SYNTHESIZER_MODEL", "openai.gpt-5.6-luna", raising=False)
    usage = {"inputTokens": 2, "outputTokens": 866, "cacheWriteInputTokens": 6740}
    assert S._synth_cost(object(), usage) == pytest.approx(
        mantle.cost_usd("openai.gpt-5.6-luna", usage)
    )


# --- the recorded measurement matrix -----------------------------------------


def test_every_recorded_cell_carries_an_n_and_a_date():
    """A cell without a sample count and a date is an assertion, not a measurement."""
    assert mantle.MEASURED_SYNTHESIS_LATENCY, "the matrix must not be empty"
    for model_id, regions in mantle.MEASURED_SYNTHESIS_LATENCY.items():
        assert regions, f"{model_id} has an empty region map"
        for region, sample in regions.items():
            where = f"{model_id}/{region}"
            assert sample.n >= mantle.MIN_TRIALS_FOR_A_RANKING, where
            assert isinstance(sample.measured_on, _dt.date), where
            assert sample.prompt_chars == mantle.MEASURED_PROMPT_CHARS, where
            assert sample.effort in mantle.MANTLE_EFFORTS, where


def test_no_recorded_region_is_one_the_model_does_not_support():
    """A measurement of a region the model is not served in would route calls to a 404."""
    for model_id, regions in mantle.MEASURED_SYNTHESIS_LATENCY.items():
        available = mantle.MANTLE_REGIONS[model_id]
        for region in regions:
            assert region in available, f"{model_id} is not served in {region}"


def test_sol_has_no_us_west_2_row_because_it_is_not_offered_there():
    assert "us-west-2" not in mantle.MEASURED_SYNTHESIS_LATENCY["openai.gpt-5.6-sol"]


def test_recorded_numbers_are_internally_consistent():
    """TTFT cannot exceed total, and throughput must reconcile with total to within the
    slack that independent column-wise medians allow.

    MEASURED PROPERTY OF THE DATA, not a tolerance chosen to make a test pass: the four
    p50 columns are separate medians over the trials, so ``tps_p50`` equals
    ``output_tokens_p50 / total_p50_s`` exactly only when one trial is the median in
    every column. Verified against /tmp/bench/matrix.json, that holds exactly for
    luna/us-east-1, luna/us-west-2 and sol/us-east-1, and the worst disagreement across
    all eight cells is luna/us-east-2 at 0.895x. A transcription slip of one digit moves
    a cell far outside 15%, which is what this catches.
    """
    for model_id, regions in mantle.MEASURED_SYNTHESIS_LATENCY.items():
        for region, s in regions.items():
            where = f"{model_id}/{region}"
            assert 0 < s.ttft_p50_s < s.total_p50_s, where
            assert s.tps_p50 > 0 and s.output_tokens_p50 > 0, where
            assert s.tps_p50 == pytest.approx(
                s.output_tokens_p50 / s.total_p50_s, rel=0.15
            ), where


def test_the_matrix_records_a_verdict_so_latency_is_not_bought_with_a_bad_answer():
    """A fast call that failed the 21-key contract is not a fast synthesis."""
    verdicts = {
        s.verdict
        for regions in mantle.MEASURED_SYNTHESIS_LATENCY.values()
        for s in regions.values()
    }
    assert verdicts == {"HIGH RISK/DECLINE"}


def test_the_matrix_states_the_prompt_size_it_was_measured_on():
    """The whole trap is quoting this ranking for a different prompt size."""
    assert mantle.MEASURED_PROMPT_CHARS == 31_343
    rendered = mantle.render_recorded_matrix()
    assert "31343" in rendered
    assert "n=3" in rendered


def test_the_recorded_matrix_is_not_silently_stale():
    """Past STALE_AFTER_DAYS the renderer must warn rather than present it as current."""
    assert mantle.matrix_age_days(mantle.MEASURED_ON) == 0
    fresh = mantle.render_recorded_matrix(mantle.MEASURED_ON)
    assert "WARNING" not in fresh
    old = mantle.render_recorded_matrix(
        mantle.MEASURED_ON + _dt.timedelta(days=mantle.STALE_AFTER_DAYS + 1)
    )
    assert "WARNING" in old and "Re-measure" in old


def test_a_thin_cell_is_excluded_from_the_ranking_rather_than_trusted(monkeypatch):
    """A single-sample cell must not be allowed to win the ranking.

    MIN_TRIALS_FOR_A_RANKING is the guard. Injecting a blazing n=1 cell proves it is
    enforced rather than merely declared -- without this, lowering the constant to 1
    changed nothing observable.
    """
    luna = dict(mantle.MEASURED_SYNTHESIS_LATENCY["openai.gpt-5.6-luna"])
    luna["us-east-2"] = mantle.LatencySample(
        0.5, 0.1, 1600.0, 800, 1, mantle.MEASURED_ON, mantle.MEASURED_PROMPT_CHARS,
        "low", "HIGH RISK/DECLINE",
    )
    monkeypatch.setitem(mantle.MEASURED_SYNTHESIS_LATENCY, "openai.gpt-5.6-luna", luna)
    preference = mantle.measured_region_preference("openai.gpt-5.6-luna")
    assert "us-east-2" not in preference, "an n=1 cell must not enter the ranking at all"
    assert preference[0] == "us-east-1"
    assert mantle.resolve_mantle_region("openai.gpt-5.6-luna") == "us-east-1"


def test_unmeasured_variants_are_absent_rather_than_guessed():
    """5.4/5.5 are priced and region-mapped but were never measured on this prompt."""
    for model_id in ("openai.gpt-5.4", "openai.gpt-5.5"):
        assert model_id in mantle.MANTLE_RATES_PER_MTOK
        assert model_id not in mantle.MEASURED_SYNTHESIS_LATENCY
        assert mantle.measured_region_preference(model_id) == ()
        assert mantle.measured_best_region(model_id) is None


# --- the default region is the measured one ----------------------------------


def test_the_default_region_for_the_default_synthesizer_is_the_measured_best():
    """us-east-1 at 4.61s p50, not us-west-2 at 5.02s. THIS IS THE POINT OF THE FILE."""
    from agents.models import DEFAULT_SYNTHESIZER_MODEL

    assert mantle.is_mantle_model(DEFAULT_SYNTHESIZER_MODEL)
    assert mantle.measured_best_region(DEFAULT_SYNTHESIZER_MODEL) == "us-east-1"
    assert mantle.resolve_mantle_region(DEFAULT_SYNTHESIZER_MODEL) == "us-east-1"

    samples = mantle.MEASURED_SYNTHESIS_LATENCY[DEFAULT_SYNTHESIZER_MODEL]
    assert samples["us-east-1"].total_p50_s < samples["us-west-2"].total_p50_s


def test_the_preference_order_is_the_measured_total_latency_order():
    assert mantle.measured_region_preference("openai.gpt-5.6-luna") == (
        "us-east-1",
        "us-west-2",
        "us-east-2",
    )
    assert mantle.measured_region_preference("openai.gpt-5.6-terra") == (
        "us-east-2",
        "us-east-1",
        "us-west-2",
    )
    assert mantle.measured_region_preference("openai.gpt-5.6-sol") == (
        "us-east-1",
        "us-east-2",
    )


def test_ranking_on_throughput_would_pick_a_different_region():
    """The ranking key is load-bearing, and this is the axis on which it actually is.

    MEASURED PROPERTY: ordering our cells by TTFT gives the same answer as ordering by
    total for all three variants, so those two keys are indistinguishable on this data.
    Ordering by tokens/s does NOT -- Luna's us-west-2 has the higher throughput
    (180.0 vs 176.0) yet the worse total (5.02s vs 4.61s), because it also has 1.5x the
    TTFT. Throughput is exactly the axis the third-party short-prompt probe ranks on, and
    it is the wrong one for a single non-streamed adjudication.
    """
    luna = mantle.MEASURED_SYNTHESIS_LATENCY["openai.gpt-5.6-luna"]
    by_tps = sorted(luna, key=lambda r: -luna[r].tps_p50)
    by_total = list(mantle.measured_region_preference("openai.gpt-5.6-luna"))
    assert by_tps[0] == "us-west-2"
    assert by_total[0] == "us-east-1"
    assert by_tps != by_total, "if these agreed, the ranking key would not matter"
    # ...and the code follows total, not throughput.
    assert mantle.resolve_mantle_region("openai.gpt-5.6-luna") == "us-east-1"


def test_terra_ranks_us_east_2_first_which_luna_does_not():
    """The ranking is per-model. A single global 'fastest region' would be wrong."""
    assert mantle.measured_best_region("openai.gpt-5.6-terra") == "us-east-2"
    assert mantle.measured_best_region("openai.gpt-5.6-luna") == "us-east-1"


def test_aws_region_does_not_choose_the_mantle_region(monkeypatch):
    """The regression: AWS_REGION=us-west-2 must NOT drag the synthesizer to us-west-2.

    A mantle call is in-region only with no cross-region profile, so co-locating it with
    the Converse specialists buys nothing and measurably costs 0.41s p50 (n=3 each).
    """
    for deployed in ("us-west-2", "us-east-2", "eu-west-1"):
        monkeypatch.setenv("AWS_REGION", deployed)
        monkeypatch.setenv("AWS_DEFAULT_REGION", deployed)
        mantle.reset_region_log()
        assert mantle.resolve_mantle_region("openai.gpt-5.6-luna") == "us-east-1"


def test_bedrock_mantle_region_overrides_the_measurement(monkeypatch):
    """An operator with a data-residency rule, or who disagrees, must be able to win."""
    monkeypatch.setenv("BEDROCK_MANTLE_REGION", "us-west-2")
    assert mantle.resolve_mantle_region("openai.gpt-5.6-luna") == "us-west-2"
    _, why = mantle.region_choice("openai.gpt-5.6-luna")
    assert "BEDROCK_MANTLE_REGION" in why


def test_an_explicit_argument_beats_the_env_override(monkeypatch):
    monkeypatch.setenv("BEDROCK_MANTLE_REGION", "us-west-2")
    assert mantle.resolve_mantle_region("openai.gpt-5.6-luna", "us-east-2") == "us-east-2"


def test_an_unsupported_override_still_falls_back_to_an_available_region(monkeypatch):
    """Availability beats both the override and the measurement: a 404 helps nobody."""
    monkeypatch.setenv("BEDROCK_MANTLE_REGION", "us-west-2")
    resolved, why = mantle.region_choice("openai.gpt-5.6-sol")
    assert resolved in mantle.MANTLE_REGIONS["openai.gpt-5.6-sol"]
    assert resolved == "us-east-1"  # the measured-best AVAILABLE region, not merely first
    assert "not served" in why


def test_an_unsupported_explicit_argument_falls_back_too():
    resolved, why = mantle.region_choice("openai.gpt-5.6-sol", "us-west-2")
    assert resolved == "us-east-1"
    assert "us-west-2" in why and "not served" in why


def test_the_availability_fallback_lands_on_the_MEASURED_best_not_the_declared_first(
    monkeypatch,
):
    """Sol cannot show this: its declared-first region is also its measured-best one.

    Terra can. Its declared order starts at us-east-1 (6.72s p50) but its measured-best is
    us-east-2 (6.29s p50), so shrinking its availability to those two and asking for a
    third region separates "fall back to available[0]" from "fall back to the fastest
    available region". Without this, that distinction is untested -- verified by mutation.
    """
    monkeypatch.setitem(
        mantle.MANTLE_REGIONS, "openai.gpt-5.6-terra", ("us-east-1", "us-east-2")
    )
    declared_first = mantle.MANTLE_REGIONS["openai.gpt-5.6-terra"][0]
    resolved, why = mantle.region_choice("openai.gpt-5.6-terra", "us-west-2")
    assert declared_first == "us-east-1"
    assert resolved == "us-east-2", "fell back to the declared-first, not the fastest"
    assert "not served" in why


def test_an_unmeasured_model_uses_declared_availability_not_an_invented_ranking():
    resolved, why = mantle.region_choice("openai.gpt-5.5")
    assert resolved == mantle.MANTLE_REGIONS["openai.gpt-5.5"][0]
    assert "no latency measurement recorded" in why


def test_a_wholly_unknown_model_falls_back_to_the_documented_default():
    resolved, why = mantle.region_choice("openai.gpt-9.9-imaginary")
    assert resolved == mantle.DEFAULT_MANTLE_REGION == "us-east-1"
    assert "no measurement and no availability list" in why


def test_the_region_choice_is_logged_once_with_its_reason(caplog):
    """A deliberate choice that is never stated is indistinguishable from an accident."""
    import logging

    mantle.reset_region_log()
    with caplog.at_level(logging.INFO, logger="pp.mantle"):
        for _ in range(5):
            mantle.resolve_mantle_region("openai.gpt-5.6-luna")
    lines = [r for r in caplog.records if r.name == "pp.mantle"]
    assert len(lines) == 1, "the reason must be logged once, not once per adjudication"
    message = lines[0].getMessage()
    assert "us-east-1" in message and "4.61" in message and "n=3" in message


def test_every_measured_model_resolves_to_a_region_it_is_served_in():
    for model_id in mantle.MEASURED_SYNTHESIS_LATENCY:
        assert mantle.resolve_mantle_region(model_id) in mantle.MANTLE_REGIONS[model_id]


# --- the third-party corroboration -------------------------------------------


def test_the_third_party_probe_is_labelled_as_not_ours():
    label = mantle.THIRD_PARTY_PROBE_SOURCE
    assert "THIRD-PARTY" in label
    assert "not measured by this repo" in label
    rendered = mantle.render_third_party_probe()
    assert rendered.startswith("THIRD-PARTY")
    assert "not measured by this repo" in rendered


def test_the_third_party_probe_never_selects_a_region():
    """It ranks us-west-2 first for Luna. If it fed routing, we would deploy the wrong one."""
    luna_third_party = mantle.THIRD_PARTY_SHORT_PROMPT_PROBE["openai.gpt-5.6-luna"]
    by_ttft = sorted(luna_third_party, key=lambda r: luna_third_party[r][0])
    assert by_ttft[0] == "us-west-2"
    # ...and yet the code picks us-east-1, from OUR measurement.
    assert mantle.measured_best_region("openai.gpt-5.6-luna") == "us-east-1"
    assert mantle.resolve_mantle_region("openai.gpt-5.6-luna") == "us-east-1"


def test_the_divergence_is_documented_not_hidden():
    rendered = mantle.render_third_party_probe()
    assert "AGREES" in rendered and "DIVERGES" in rendered
    assert "us-west-2" in rendered and "us-east-1" in rendered
    assert "time-to-first-token" in rendered


def test_where_the_two_datasets_agree(model_id="openai.gpt-5.6-luna"):
    """us-east-2 is the worst region for Luna in BOTH datasets. Real corroboration."""
    ours = mantle.MEASURED_SYNTHESIS_LATENCY[model_id]
    theirs = mantle.THIRD_PARTY_SHORT_PROMPT_PROBE[model_id]
    assert max(ours, key=lambda r: ours[r].total_p50_s) == "us-east-2"
    assert max(theirs, key=lambda r: theirs[r][0]) == "us-east-2"

    # And Sol's us-east-2 is catastrophic in both.
    sol_ours = mantle.MEASURED_SYNTHESIS_LATENCY["openai.gpt-5.6-sol"]
    sol_theirs = mantle.THIRD_PARTY_SHORT_PROMPT_PROBE["openai.gpt-5.6-sol"]
    assert sol_ours["us-east-2"].total_p50_s > 2 * sol_ours["us-east-1"].total_p50_s
    assert sol_theirs["us-east-2"][0] > 2 * sol_theirs["us-east-1"][0]

    # And Sol has the highest TTFT of the three variants in both.
    highest_ours = max(
        mantle.MEASURED_SYNTHESIS_LATENCY,
        key=lambda m: min(s.ttft_p50_s for s in mantle.MEASURED_SYNTHESIS_LATENCY[m].values()),
    )
    highest_theirs = max(
        mantle.THIRD_PARTY_SHORT_PROMPT_PROBE,
        key=lambda m: min(v[0] for v in mantle.THIRD_PARTY_SHORT_PROMPT_PROBE[m].values()),
    )
    assert highest_ours == highest_theirs == "openai.gpt-5.6-sol"


def test_the_third_party_probe_only_names_supported_regions():
    for model_id, regions in mantle.THIRD_PARTY_SHORT_PROMPT_PROBE.items():
        for region in regions:
            assert region in mantle.MANTLE_REGIONS[model_id], f"{model_id}/{region}"


# --- re-measurement ----------------------------------------------------------


def test_re_measurement_is_double_gated(monkeypatch):
    monkeypatch.delenv(mantle.MEASURE_ENV_GATE, raising=False)
    assert "not requested" in mantle.measure_gate_error(False)
    assert mantle.MEASURE_ENV_GATE in mantle.measure_gate_error(True)
    monkeypatch.setenv(mantle.MEASURE_ENV_GATE, "1")
    assert mantle.measure_gate_error(True) is None
    assert mantle.measure_gate_error(False) is not None


def test_the_cli_refuses_to_measure_without_the_env_gate(monkeypatch, capsys):
    monkeypatch.delenv(mantle.MEASURE_ENV_GATE, raising=False)
    assert mantle.main(["--measure"]) == 2
    assert "refusing to measure" in capsys.readouterr().out


def test_the_cli_with_no_flags_prints_the_table_and_spends_nothing(capsys):
    assert mantle.main([]) == 0
    out = capsys.readouterr().out
    assert "MEASURED synthesis latency" in out
    assert "THIRD-PARTY" in out
    assert "default synthesizer region: us-east-1" in out
    assert "no spend" in out


def test_a_short_prompt_is_flagged_as_not_comparable():
    """THE TRAP, asserted. A 200-char prompt cannot be diffed against a 31,343-char one."""
    comparable, note = mantle.prompt_comparability(200)
    assert comparable is False
    assert "NOT comparable" in note
    comparable, note = mantle.prompt_comparability(mantle.MEASURED_PROMPT_CHARS)
    assert comparable is True and "comparable" in note
    # A modest drift is still comparable.
    assert mantle.prompt_comparability(int(mantle.MEASURED_PROMPT_CHARS * 1.1))[0] is True
    assert mantle.prompt_comparability(int(mantle.MEASURED_PROMPT_CHARS * 2))[0] is False


def _fake_call(latency_by_region, *, valid=True):
    """A measure_once stand-in: deterministic timings, no network."""

    def call(model_id, region, prompt, *, effort="low", max_output_tokens=8192):
        total = latency_by_region[(model_id, region)]
        return {
            "model_id": model_id,
            "region": region,
            "ok": True,
            "total_s": total,
            "ttft_s": total * 0.15,
            "tps": 800 / total,
            "output_tokens": 800,
            "usage": {"inputTokens": 7800, "outputTokens": 800},
            "cost_usd": 0.0123,
            "valid": valid,
            "keys": 21 if valid else 0,
            "verdict": "HIGH RISK/DECLINE" if valid else "INVALID JSON",
            "effort": effort,
        }

    return call


def _recorded_latencies():
    return {
        (model_id, region): sample.total_p50_s
        for model_id, regions in mantle.MEASURED_SYNTHESIS_LATENCY.items()
        for region, sample in regions.items()
    }


def test_re_measuring_the_recorded_numbers_diffs_as_unchanged():
    prompt = "x" * mantle.MEASURED_PROMPT_CHARS
    measured = mantle.measure_matrix(prompt, call=_fake_call(_recorded_latencies()))
    assert measured["comparable_with_recorded"] is True
    diff = mantle.diff_against_recorded(measured)
    assert diff and {e["status"] for e in diff} == {"unchanged"}
    rendered = mantle.render_matrix_diff(measured, diff)
    assert "UNCHANGED" in rendered
    assert "CHANGED" not in rendered.replace("UNCHANGED", "")


def test_a_ranking_change_is_reported_not_absorbed():
    """If us-west-2 overtakes us-east-1, the default in the code is stale and must say so."""
    latencies = _recorded_latencies()
    latencies[("openai.gpt-5.6-luna", "us-east-1")] = 9.0
    measured = mantle.measure_matrix(
        "x" * mantle.MEASURED_PROMPT_CHARS,
        models=("openai.gpt-5.6-luna",),
        call=_fake_call(latencies),
    )
    diff = mantle.diff_against_recorded(measured)
    statuses = {(e["region"], e["status"]) for e in diff if e["model_id"].endswith("luna")}
    assert ("us-east-1", "slower") in statuses
    rendered = mantle.render_matrix_diff(measured, diff)
    assert "ranking openai.gpt-5.6-luna" in rendered
    assert "CHANGED" in rendered


def test_recorded_cells_that_were_not_re_measured_are_reported_missing():
    measured = mantle.measure_matrix(
        "x" * mantle.MEASURED_PROMPT_CHARS,
        models=("openai.gpt-5.6-luna",),
        call=_fake_call(_recorded_latencies()),
    )
    diff = mantle.diff_against_recorded(measured)
    missing = {e["model_id"] for e in diff if e["status"] == "missing"}
    assert missing == {"openai.gpt-5.6-terra", "openai.gpt-5.6-sol"}


def test_a_newly_available_region_shows_up_as_new_not_as_invisible(monkeypatch):
    monkeypatch.setitem(
        mantle.MANTLE_REGIONS, "openai.gpt-5.6-sol", ("us-east-1", "us-east-2", "us-west-2")
    )
    latencies = _recorded_latencies()
    latencies[("openai.gpt-5.6-sol", "us-west-2")] = 8.0
    measured = mantle.measure_matrix(
        "x" * mantle.MEASURED_PROMPT_CHARS,
        models=("openai.gpt-5.6-sol",),
        call=_fake_call(latencies),
    )
    diff = mantle.diff_against_recorded(measured)
    assert any(e["region"] == "us-west-2" and e["status"] == "new" for e in diff)


def test_a_cell_whose_every_trial_failed_is_reported_failed_not_fast():
    def failing(model_id, region, prompt, *, effort="low", max_output_tokens=8192):
        return {"model_id": model_id, "region": region, "ok": False, "error": "AccessDenied"}

    measured = mantle.measure_matrix(
        "x" * mantle.MEASURED_PROMPT_CHARS,
        models=("openai.gpt-5.6-luna",),
        call=failing,
    )
    for row in measured["rows"]:
        assert row["ok"] is False
        assert row["n"] == 0
        assert "total_p50_s" not in row  # no fabricated latency for a call that failed
    diff = mantle.diff_against_recorded(measured)
    assert {e["status"] for e in diff if e["model_id"].endswith("luna")} == {"failed"}


def test_the_measurement_records_how_many_calls_passed_the_21_key_contract():
    """Latency of an invalid synthesis is not a useful number, so the count travels."""
    measured = mantle.measure_matrix(
        "x" * mantle.MEASURED_PROMPT_CHARS,
        models=("openai.gpt-5.6-luna",),
        call=_fake_call(_recorded_latencies(), valid=False),
    )
    for row in measured["rows"]:
        assert row["valid"] == 0
        assert row["verdicts"] == []
    assert any("21-key contract" in c for c in measured["caveats"])


def test_the_measurement_states_its_sample_count_and_that_totals_are_streamed():
    measured = mantle.measure_matrix(
        "x" * mantle.MEASURED_PROMPT_CHARS,
        models=("openai.gpt-5.6-luna",),
        trials=4,
        call=_fake_call(_recorded_latencies()),
    )
    assert measured["trials"] == 4
    assert all(row["n"] == 4 for row in measured["rows"])
    assert measured["streamed"] is True
    assert any("STREAMED" in c for c in measured["caveats"])
    assert "n=4" in mantle.render_matrix_diff(measured, mantle.diff_against_recorded(measured))


def test_missing_ttft_is_none_rather_than_zero():
    """A TTFT that was never observed is unknown, not instantaneous."""

    def no_ttft(model_id, region, prompt, *, effort="low", max_output_tokens=8192):
        return {
            "model_id": model_id, "region": region, "ok": True, "total_s": 5.0,
            "ttft_s": None, "tps": None, "output_tokens": 0, "usage": None,
            "cost_usd": None, "valid": False, "keys": 0, "verdict": "x", "effort": effort,
        }

    measured = mantle.measure_matrix(
        "x" * mantle.MEASURED_PROMPT_CHARS, models=("openai.gpt-5.6-luna",), call=no_ttft
    )
    for row in measured["rows"]:
        assert row["ttft_p50_s"] is None
        assert row["tps_p50"] is None
        assert row["cost_usd_total"] is None


def test_the_spend_estimate_is_an_upper_bound_that_says_so():
    estimate = mantle.estimate_measure_spend("x" * mantle.MEASURED_PROMPT_CHARS)
    # 3 luna regions + 3 terra regions + 2 sol regions, times 3 trials.
    assert estimate["total_calls"] == (3 + 3 + 2) * mantle.MEASURED_TRIALS == 24
    assert estimate["total_upper_bound_usd"] > 0
    assert "UPPER BOUND" in estimate["basis"]
    assert "ESTIMATED" in estimate["basis"]
    assert set(estimate["per_model"]) == set(mantle.MEASURE_DEFAULT_MODELS)


def test_the_spend_estimate_over_estimates_a_real_measured_call():
    """It must never come in UNDER what a call actually cost. $0.245/application is real."""
    estimate = mantle.estimate_measure_spend(
        "x" * mantle.MEASURED_PROMPT_CHARS, models=("openai.gpt-5.6-luna",), trials=1
    )
    per_call = estimate["total_upper_bound_usd"] / estimate["total_calls"]
    # A measured Luna synthesis: inputTokens=2, cacheWrite=6740, output=866 => $0.01499.
    actual = mantle.cost_usd(
        "openai.gpt-5.6-luna",
        {"inputTokens": 2, "outputTokens": 866, "cacheWriteInputTokens": 6740},
    )
    assert per_call > actual


def test_the_cli_dry_run_prints_the_plan_and_calls_nothing(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(mantle.MEASURE_ENV_GATE, "1")
    monkeypatch.setattr(
        mantle, "measure_matrix", lambda *a, **k: pytest.fail("dry-run must not measure")
    )
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("x" * mantle.MEASURED_PROMPT_CHARS, encoding="utf-8")
    assert mantle.main(["--measure", "--dry-run", "--prompt-file", str(prompt_file)]) == 0
    out = capsys.readouterr().out
    assert "24 calls" in out
    assert "nothing was called, no spend" in out


def test_the_cli_refuses_to_measure_an_unpriced_model(monkeypatch, capsys):
    monkeypatch.setenv(mantle.MEASURE_ENV_GATE, "1")
    assert mantle.main(["--measure", "--model", "openai.gpt-9.9-imaginary"]) == 2
    assert "unpriced" in capsys.readouterr().out


@pytest.mark.parametrize("trials", [0, 1, 2])
def test_the_cli_refuses_a_trial_count_too_small_to_rank(monkeypatch, capsys, trials):
    """Spending money on a table the region choice would then discard is worse than a refusal."""
    monkeypatch.setenv(mantle.MEASURE_ENV_GATE, "1")
    monkeypatch.setattr(
        mantle, "measure_matrix", lambda *a, **k: pytest.fail("must not measure")
    )
    assert mantle.main(["--measure", "--trials", str(trials)]) == 2
    out = capsys.readouterr().out
    assert f"--trials {trials}" in out
    assert str(mantle.MIN_TRIALS_FOR_A_RANKING) in out


def test_measure_defaults_cover_exactly_the_measured_variants():
    assert set(mantle.MEASURE_DEFAULT_MODELS) == set(mantle.MEASURED_SYNTHESIS_LATENCY)


# --- the customer-facing doc must not drift from the code --------------------


def _model_selection_doc() -> str:
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "docs" / "model-selection.md"
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def _doc_latency_rows() -> dict[tuple[str, str], list[str]]:
    """Parse the doc's measured-latency table into {(variant, region): cells}.

    Parsed rather than substring-searched on purpose: a plain ``"4.61 s" in doc`` check
    passes even after the table cell drifts, because the same figure also appears in the
    surrounding prose. Verified by mutation -- changing the table cell alone did not fail
    the substring form of this test.
    """
    rows: dict[tuple[str, str], list[str]] = {}
    for line in _model_selection_doc().splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("*") for c in line.strip("|").split("|")]
        if len(cells) != 6:
            continue
        variant, region = cells[0], cells[1]
        if variant in ("luna", "terra", "sol") and region.startswith("us-"):
            rows[(variant, region)] = cells
    return rows


def test_the_doc_table_quotes_every_recorded_latency_exactly():
    """An SA reads the doc, not the dict. A cell that drifts here becomes a wrong claim."""
    rows = _doc_latency_rows()
    expected = {
        (model_id.rsplit("-", 1)[-1], region)
        for model_id, regions in mantle.MEASURED_SYNTHESIS_LATENCY.items()
        for region in regions
    }
    assert set(rows) == expected, "the doc table and the recorded matrix cover different cells"

    for model_id, regions in mantle.MEASURED_SYNTHESIS_LATENCY.items():
        variant = model_id.rsplit("-", 1)[-1]
        for region, s in regions.items():
            _, _, total, ttft, tps, out_tok = rows[(variant, region)]
            where = f"{variant}/{region}"
            assert total == f"{s.total_p50_s:.2f} s", f"{where} total: {total!r}"
            assert ttft == f"{s.ttft_p50_s:.2f} s", f"{where} ttft: {ttft!r}"
            assert float(tps) == pytest.approx(s.tps_p50), f"{where} tps: {tps!r}"
            assert int(out_tok) == s.output_tokens_p50, f"{where} out: {out_tok!r}"


def test_the_doc_table_is_ordered_by_the_measured_ranking():
    """The doc must present us-east-1 first for Luna, matching what the code selects."""
    ordered = [key for key in _doc_latency_rows()]
    luna = [region for variant, region in ordered if variant == "luna"]
    assert tuple(luna) == mantle.measured_region_preference("openai.gpt-5.6-luna")
    assert luna[0] == mantle.resolve_mantle_region("openai.gpt-5.6-luna") == "us-east-1"


def test_the_doc_quotes_the_rate_card_and_the_sample_count():
    doc = _model_selection_doc()
    for model_id, (rate_in, _, rate_out) in mantle.MANTLE_RATES_PER_MTOK.items():
        if model_id not in mantle.MEASURED_SYNTHESIS_LATENCY:
            continue
        assert f"${rate_in:.2f}" in doc, model_id
        assert f"${rate_out:.2f}" in doc, model_id
    assert f"n={mantle.MEASURED_TRIALS}" in doc
    # The prompt size is written with a thousands separator for a human reader, so
    # compare against that form rather than the bare int.
    assert f"{mantle.MEASURED_PROMPT_CHARS:,}" in doc
    assert mantle.MEASURED_ON.isoformat() in doc


def test_the_doc_states_the_us_prefix_multiplier_and_that_specialists_stay_on_claude():
    from agents.models import REGIONAL_MULTIPLIER

    doc = _model_selection_doc()
    assert f"{REGIONAL_MULTIPLIER:.2f}x" in doc
    assert "eight specialists stay on Claude" in doc
    assert "not us-west-2" in doc  # Sol's regional gap
    assert mantle.MEASURE_ENV_GATE in doc
    assert "BEDROCK_MANTLE_REGION" in doc


def test_the_doc_labels_the_third_party_data_and_disclaims_reasoning_parity():
    doc = _model_selection_doc()
    assert "THIRD-PARTY" in doc
    assert "not our measurement" in doc.lower()
    # Reasoning parity is another workstream's; the doc must not claim it.
    assert "does not claim" in doc
    assert "reasoning" in doc.lower() and "calibration judge" in doc


def test_the_doc_claims_no_percentile_the_sample_count_cannot_support():
    """n=3 supports no p95 and no p99, so neither may appear as a claim about our data."""
    doc = _model_selection_doc()
    for forbidden in ("p95 of", "p99 of", "p95:", "p99:"):
        assert forbidden not in doc, forbidden


def test_the_probe_prompt_is_the_real_synthesis_prompt(monkeypatch):
    """A re-measurement must time TODAY's prompt, not a stub.

    In MOCK_MODE the fan-out returns canned analyses, so the assembled prompt is much
    shorter than production's -- which is exactly why the comparability check exists.
    """
    prompt = mantle.build_probe_prompt("APP-1004")
    assert "{identity_analysis}" not in prompt  # every placeholder substituted
    assert len(prompt) > len(__import__("prompts.loader", fromlist=["x"]).MASTER_SYNTHESIS_PROMPT)
    # And a MOCK-built prompt is honestly flagged as not comparable with the recorded run.
    assert mantle.prompt_comparability(len(prompt))[0] is False

# --- the base-URL path, which is the single easiest thing to get wrong ----------


def test_the_frontier_path_is_openai_v1_not_v1():
    """MEASURED 2026-07-28, us-east-1, all four combinations:

        /v1        + openai.gpt-oss-120b  -> 200
        /v1        + openai.gpt-5.6-luna  -> 400 "does not support the '/v1/responses' API"
        /openai/v1 + openai.gpt-oss-120b  -> 400
        /openai/v1 + openai.gpt-5.6-luna  -> 200

    The two paths are mutually exclusive, and Strands' own Mantle documentation shows
    ``/v1`` (correct for its gpt-oss example, wrong for GPT-5.6). Following it verbatim
    yields a 400 that reads like the model is unavailable rather than like the path is
    wrong, so this is pinned.
    """
    url = mantle.base_url_for("us-east-1")
    assert url == "https://bedrock-mantle.us-east-1.api.aws/openai/v1"
    assert url.endswith("/openai/v1")
    # The open-weight path is recorded, and must NOT be the one the client uses.
    open_weight = mantle.MANTLE_OPEN_WEIGHT_BASE_URL_TEMPLATE.format(region="us-east-1")
    assert open_weight == "https://bedrock-mantle.us-east-1.api.aws/v1"
    assert url != open_weight


def test_the_host_is_api_aws_not_amazonaws_com():
    """``bedrock-mantle.{region}.amazonaws.com`` does not resolve (NXDOMAIN), which looks
    exactly like an auth failure and cost real debugging time."""
    url = mantle.base_url_for("us-west-2")
    assert ".api.aws/" in url
    assert "amazonaws.com" not in url


def test_base_url_tracks_the_region_argument():
    for region in ("us-east-1", "us-east-2", "us-west-2"):
        assert mantle.base_url_for(region) == (
            f"https://bedrock-mantle.{region}.api.aws/openai/v1"
        )


def test_the_client_uses_base_url_for(monkeypatch):
    """The one client constructor must go through base_url_for, not a local f-string."""
    captured = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import sys
    import types

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = _FakeOpenAI
    fake_tokgen = types.ModuleType("aws_bedrock_token_generator")
    fake_tokgen.provide_token = lambda region=None: "tok"
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setitem(sys.modules, "aws_bedrock_token_generator", fake_tokgen)

    mantle._client("us-east-2")
    assert captured["base_url"] == mantle.base_url_for("us-east-2")


def test_luna_and_terra_are_repriced_date_aware_on_the_reprice_date():
    """The 2026-07-30 Terra/Luna reprice is date-aware.

    A call dated strictly before MANTLE_REPRICE_DATE is priced at the prior card so a
    run measured before the reprice reproduces its recorded cost; a call on or after it
    uses the current card. Sol has no prior entry and is date-invariant.
    """
    import datetime as _d

    before = mantle.MANTLE_REPRICE_DATE - _d.timedelta(days=1)
    on = mantle.MANTLE_REPRICE_DATE
    for model_id in ("openai.gpt-5.6-luna", "openai.gpt-5.6-terra"):
        assert (
            mantle.rates_for(model_id, as_of=before)
            == mantle.MANTLE_RATES_PER_MTOK_PRIOR[model_id]
        )
        assert (
            mantle.rates_for(model_id, as_of=on) == mantle.MANTLE_RATES_PER_MTOK[model_id]
        )
    # Luna dropped on both input and output at the reprice.
    old_luna = mantle.MANTLE_RATES_PER_MTOK_PRIOR["openai.gpt-5.6-luna"]
    new_luna = mantle.MANTLE_RATES_PER_MTOK["openai.gpt-5.6-luna"]
    assert new_luna[0] < old_luna[0] and new_luna[2] < old_luna[2]
    # Sol is unchanged, so it prices identically on either side of the date.
    assert mantle.rates_for("openai.gpt-5.6-sol", as_of=before) == mantle.rates_for(
        "openai.gpt-5.6-sol", as_of=on
    )
    # The same date-awareness must flow through the pricing module's cost.
    from agents import pricing

    usage = {"inputTokens": 1000, "outputTokens": 1000}
    assert pricing.cost_usd("openai.gpt-5.6-luna", usage, as_of=before) > pricing.cost_usd(
        "openai.gpt-5.6-luna", usage, as_of=on
    )
