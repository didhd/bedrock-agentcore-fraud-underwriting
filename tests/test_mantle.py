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
"""

from __future__ import annotations

import pytest

from agents import mantle


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
