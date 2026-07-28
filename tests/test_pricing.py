"""
Guards on the rate card, the cache-aware cost function, and the tiering claim.

Three of these tests exist specifically to stop a regression that already shipped
once:

* ``test_us_prefix_costs_exactly_1_10x_global_on_every_category`` - the previous
  revision priced ``us.*`` calls at ``global.*`` rates, under-reporting the
  customer-facing spend dashboard by 9.09%.
* ``test_min_cacheable_prefix_*`` - a ~1800-token prompt caches on Opus 5 and
  silently does not on Haiku 4.5, with no error and ``cacheWriteInputTokens=0``.
* ``test_reported_saving_equals_independently_computed_arithmetic`` and
  ``test_two_opus_cancelling_two_haiku_reports_a_zero_saving`` - the earlier
  tiering saved exactly $0.00 while the README called it the differentiator. The
  saving must always be recomputed from the rate card, never asserted.

Offline only: no AWS call, no Bedrock inference.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import models as m
from agents import pricing as p

BEFORE_INTRO_EXPIRY = dt.date(2026, 7, 27)
AFTER_INTRO_EXPIRY = dt.date(2026, 9, 1)

#: A usage shape with all four categories populated, used wherever the exact
#: mix does not matter. Not a claim about any real application.
SAMPLE_USAGE = {
    "inputTokens": 1200,
    "outputTokens": 400,
    "cacheReadInputTokens": 900,
    "cacheWriteInputTokens": 300,
}


def usage(inp: int, out: int, read: int = 0, write: int = 0) -> dict[str, int]:
    return {
        "inputTokens": inp,
        "outputTokens": out,
        "cacheReadInputTokens": read,
        "cacheWriteInputTokens": write,
    }


# ---------------------------------------------------------------------------
# Inference-profile multiplier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_id", sorted(p.BASE_RATES_PER_MTOK))
def test_us_prefix_costs_exactly_1_10x_global_on_every_category(base_id: str) -> None:
    g = p.rates_for(f"global.{base_id}", as_of=BEFORE_INTRO_EXPIRY)
    u = p.rates_for(f"us.{base_id}", as_of=BEFORE_INTRO_EXPIRY)
    for category in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
        assert u[category] == pytest.approx(g[category] * m.REGIONAL_MULTIPLIER, rel=1e-12), category
    assert m.REGIONAL_MULTIPLIER == 1.10
    assert g["regional_multiplier"] == 1.0
    assert u["regional_multiplier"] == 1.10


def test_us_prefix_cost_is_909_percent_higher_on_identical_usage() -> None:
    g = p.cost_usd(f"global.{p.BASE_SONNET_4_6}", SAMPLE_USAGE, as_of=BEFORE_INTRO_EXPIRY)
    u = p.cost_usd(f"us.{p.BASE_SONNET_4_6}", SAMPLE_USAGE, as_of=BEFORE_INTRO_EXPIRY)
    assert u == pytest.approx(g * 1.10, rel=1e-12)
    # The exact figure the previous revision was wrong by, stated as a ratio.
    assert (u - g) / u == pytest.approx(1 - 1 / 1.10, rel=1e-9)


def test_prefix_detection_is_driven_by_the_model_id_not_the_env_default() -> None:
    assert m.prefix_of("global.anthropic.claude-opus-5") == "global."
    assert m.prefix_of("us.anthropic.claude-opus-5") == "us."
    assert m.prefix_of("anthropic.claude-opus-5") == ""
    assert m.base_model_id("us.anthropic.claude-opus-5") == "anthropic.claude-opus-5"
    assert m.base_model_id("anthropic.claude-opus-5") == "anthropic.claude-opus-5"


def test_default_prefix_is_the_cheaper_global_profile() -> None:
    assert m.MODEL_PREFIX == "global."
    for model_id in (m.LIGHT_MODEL, m.MID_MODEL, m.HEAVY_MODEL, m.BASELINE_MODEL):
        assert model_id.startswith("global."), model_id


def test_unpriced_model_raises_rather_than_guessing() -> None:
    with pytest.raises(p.UnknownModelRateError):
        p.rates_for("global.anthropic.claude-opus-4-7")
    with pytest.raises(p.UnknownModelRateError):
        p.rates_for("global.anthropic.some-unreleased-model")


# ---------------------------------------------------------------------------
# Cache-aware cost
# ---------------------------------------------------------------------------


def test_total_input_is_input_plus_cache_read_plus_cache_write() -> None:
    u = usage(1000, 500, read=4000, write=2000)
    assert p.total_input_tokens(u) == 1000 + 4000 + 2000
    assert p.cost_breakdown(m.MID_MODEL, u, as_of=BEFORE_INTRO_EXPIRY)["total_input_tokens"] == 7000


def test_cost_uses_all_four_usage_categories_at_their_own_rates() -> None:
    model_id = f"global.{p.BASE_SONNET_4_6}"  # 3.00 / 15.00, no promo
    u = usage(1_000_000, 1_000_000, read=1_000_000, write=1_000_000)
    b = p.cost_breakdown(model_id, u, cache_ttl="5m", as_of=BEFORE_INTRO_EXPIRY)
    assert b["uncached_input_usd"] == pytest.approx(3.00)
    assert b["output_usd"] == pytest.approx(15.00)
    assert b["cache_read_usd"] == pytest.approx(3.00 * 0.10)
    assert b["cache_write_usd"] == pytest.approx(3.00 * 1.25)
    assert b["total_usd"] == pytest.approx(3.00 + 15.00 + 0.30 + 3.75)


def test_cache_write_1h_is_double_input_and_5m_is_1_25x() -> None:
    model_id = f"global.{p.BASE_SONNET_4_6}"
    u = usage(0, 0, write=1_000_000)
    five_min = p.cost_usd(model_id, u, cache_ttl="5m", as_of=BEFORE_INTRO_EXPIRY)
    one_hour = p.cost_usd(model_id, u, cache_ttl="1h", as_of=BEFORE_INTRO_EXPIRY)
    assert five_min == pytest.approx(3.00 * 1.25)
    assert one_hour == pytest.approx(3.00 * 2.00)
    assert one_hour > five_min


def test_a_warm_cached_call_costs_less_than_the_same_call_uncached() -> None:
    """Same 5000 input tokens: all uncached vs 4500 served from cache."""
    model_id = m.MID_MODEL
    uncached = usage(5000, 400)
    cached = usage(500, 400, read=4500)
    assert p.total_input_tokens(uncached) == p.total_input_tokens(cached)
    cold = p.cost_usd(model_id, uncached, as_of=BEFORE_INTRO_EXPIRY)
    warm = p.cost_usd(model_id, cached, as_of=BEFORE_INTRO_EXPIRY)
    assert warm < cold
    # Cache reads are 0.1x input, so the cached input line is 90% cheaper.
    in_rate = p.rates_for(model_id, as_of=BEFORE_INTRO_EXPIRY)["input"]
    assert cold - warm == pytest.approx(4500 / 1_000_000 * in_rate * 0.9, rel=1e-9)


def test_the_cache_write_call_is_more_expensive_than_uncached() -> None:
    """First call pays 1.25x on the prefix; the saving only starts on reuse."""
    model_id = m.MID_MODEL
    uncached = usage(5000, 400)
    first_write = usage(500, 400, write=4500)
    assert p.cost_usd(model_id, first_write, as_of=BEFORE_INTRO_EXPIRY) > p.cost_usd(
        model_id, uncached, as_of=BEFORE_INTRO_EXPIRY
    )


def test_missing_cache_keys_default_to_zero_and_are_never_subscripted() -> None:
    minimal = {"inputTokens": 100, "outputTokens": 50}
    counts = p.normalize_usage(minimal)
    assert counts["cacheReadInputTokens"] == 0
    assert counts["cacheWriteInputTokens"] == 0
    assert p.cost_usd(m.MID_MODEL, minimal, as_of=BEFORE_INTRO_EXPIRY) > 0


def test_absent_or_incomplete_usage_never_yields_a_fabricated_number() -> None:
    for bad in (None, {}, {"inputTokens": 10}, {"outputTokens": 10}):
        with pytest.raises(p.MissingUsageError):
            p.cost_usd(m.MID_MODEL, bad or {})
        assert p.cost_usd_or_none(m.MID_MODEL, bad) is None
    assert not hasattr(p, "NOMINAL_TOKENS")
    assert not hasattr(p, "nominal_tokens")


def test_cost_usd_has_no_two_integer_token_overload() -> None:
    """The old `cost_usd(model, in, out)` signature cannot express cached input."""
    with pytest.raises(TypeError):
        p.cost_usd(m.MID_MODEL, 1_000_000, 1_000_000)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Sonnet 5 introductory pricing
# ---------------------------------------------------------------------------


def test_sonnet_5_intro_rate_is_flagged_with_its_expiry() -> None:
    r = p.rates_for(f"global.{p.BASE_SONNET_5}", as_of=BEFORE_INTRO_EXPIRY)
    assert (r["input"], r["output"]) == (2.00, 10.00)
    assert r["introductory_pricing"] is True
    assert r["introductory_expiry"] == "2026-09-01"
    assert (r["post_introductory_input"], r["post_introductory_output"]) == (3.00, 15.00)


def test_sonnet_5_reverts_to_standard_rate_on_the_expiry_date() -> None:
    r = p.rates_for(f"global.{p.BASE_SONNET_5}", as_of=AFTER_INTRO_EXPIRY)
    assert (r["input"], r["output"]) == (3.00, 15.00)
    assert r["introductory_pricing"] is False
    assert r["introductory_expiry"] is None
    before = p.cost_usd(f"global.{p.BASE_SONNET_5}", SAMPLE_USAGE, as_of=BEFORE_INTRO_EXPIRY)
    after = p.cost_usd(f"global.{p.BASE_SONNET_5}", SAMPLE_USAGE, as_of=AFTER_INTRO_EXPIRY)
    assert after == pytest.approx(before * 1.5, rel=1e-12)


def test_cost_records_carry_the_introductory_caveat() -> None:
    b = p.cost_breakdown(f"global.{p.BASE_SONNET_5}", SAMPLE_USAGE, as_of=BEFORE_INTRO_EXPIRY)
    assert b["introductory_pricing"] is True
    assert b["introductory_expiry"] == "2026-09-01"


def test_only_sonnet_5_is_marked_introductory() -> None:
    for base_id in p.BASE_RATES_PER_MTOK:
        r = p.rates_for(f"global.{base_id}", as_of=BEFORE_INTRO_EXPIRY)
        assert r["introductory_pricing"] is (base_id == p.BASE_SONNET_5), base_id


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------


def test_batch_supported_matches_the_verified_list() -> None:
    assert p.BATCH_SUPPORTED == {
        "anthropic.claude-opus-5": True,
        "anthropic.claude-sonnet-4-6": True,
        "anthropic.claude-haiku-4-5-20251001-v1:0": True,
        "anthropic.claude-opus-4-8": False,
        "anthropic.claude-sonnet-5": False,
        "anthropic.claude-opus-4-7": False,
        "anthropic.fable-5": False,
    }
    for prefix in ("global.", "us."):
        assert p.supports_batch(f"{prefix}anthropic.claude-opus-5") is True
        assert p.supports_batch(f"{prefix}anthropic.claude-sonnet-4-6") is True
        assert p.supports_batch(f"{prefix}anthropic.claude-haiku-4-5-20251001-v1:0") is True
        assert p.supports_batch(f"{prefix}anthropic.claude-opus-4-8") is False
        assert p.supports_batch(f"{prefix}anthropic.claude-sonnet-5") is False


def test_batch_is_exactly_half_price_where_offered() -> None:
    model_id = f"global.{p.BASE_OPUS_5}"
    on_demand = p.rates_for(model_id, as_of=BEFORE_INTRO_EXPIRY)
    batched = p.rates_for(model_id, batch=True, as_of=BEFORE_INTRO_EXPIRY)
    for category in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
        assert batched[category] == pytest.approx(on_demand[category] * 0.5), category
    assert p.BATCH_DISCOUNT == 0.50
    u = usage(10_000, 2_000)
    assert p.cost_usd(model_id, u, batch=True, as_of=BEFORE_INTRO_EXPIRY) == pytest.approx(
        p.cost_usd(model_id, u, as_of=BEFORE_INTRO_EXPIRY) * 0.5
    )


def test_batch_on_an_unsupported_model_raises() -> None:
    for base_id in (p.BASE_OPUS_4_8, p.BASE_SONNET_5):
        with pytest.raises(p.UnsupportedBatchError):
            p.rates_for(f"global.{base_id}", batch=True, as_of=BEFORE_INTRO_EXPIRY)


def test_batch_plus_prompt_caching_is_rejected_as_impossible() -> None:
    model_id = f"global.{p.BASE_OPUS_5}"
    assert p.BATCH_AND_CACHING_ARE_EXCLUSIVE is True
    with pytest.raises(p.UnsupportedBatchError):
        p.cost_usd(model_id, usage(100, 50, read=4000), batch=True, as_of=BEFORE_INTRO_EXPIRY)
    with pytest.raises(p.UnsupportedBatchError):
        p.cost_usd(model_id, usage(100, 50, write=4000), batch=True, as_of=BEFORE_INTRO_EXPIRY)
    # No cache tokens: batch pricing is fine.
    assert p.cost_usd(model_id, usage(100, 50), batch=True, as_of=BEFORE_INTRO_EXPIRY) > 0
    assert p.rates_for(model_id, batch=True, as_of=BEFORE_INTRO_EXPIRY)["batch_excludes_prompt_caching"] is True


# ---------------------------------------------------------------------------
# Minimum cacheable prefix
# ---------------------------------------------------------------------------


def test_min_cacheable_prefix_table_matches_the_measured_values() -> None:
    assert m.MIN_CACHEABLE_PROMPT_TOKENS == {
        "anthropic.claude-opus-5": 512,
        "anthropic.claude-sonnet-5": 1024,
        "anthropic.claude-sonnet-4-6": 1024,
        "anthropic.claude-opus-4-8": 1024,
        "anthropic.claude-haiku-4-5-20251001-v1:0": 4096,
    }


def test_1800_token_prompt_does_not_cache_on_haiku_but_does_on_opus_5() -> None:
    prompt_tokens = 1800
    assert m.will_cache(f"global.{m.BASE_HAIKU_4_5}", prompt_tokens) is False
    assert m.will_cache(f"global.{m.BASE_OPUS_5}", prompt_tokens) is True
    # Sonnet 5 / 4.6 / Opus 4.8 all sit at 1024, so 1800 clears them.
    for base_id in (m.BASE_SONNET_5, m.BASE_SONNET_4_6, m.BASE_OPUS_4_8):
        assert m.will_cache(f"global.{base_id}", prompt_tokens) is True


def test_the_light_tier_reports_a_silent_full_price_prefix() -> None:
    report = m.cache_prefix_report(m.LIGHT_MODEL, "x" * (1800 * 4))
    assert report["will_cache"] is False
    assert report["prompt_tokens"] == 1800
    assert report["prompt_tokens_estimated"] is True
    assert report["min_cacheable_tokens"] == 4096
    assert "cacheWriteInputTokens will be 0" in report["reason"]


def test_a_real_token_count_is_not_marked_estimated() -> None:
    report = m.cache_prefix_report(m.HEAVY_MODEL, "ignored", prompt_tokens=600)
    assert report["prompt_tokens"] == 600
    assert report["prompt_tokens_estimated"] is False
    assert report["will_cache"] is True


def test_boundary_is_inclusive_at_the_measured_minimum() -> None:
    opus5 = f"global.{m.BASE_OPUS_5}"
    assert m.will_cache(opus5, 511) is False
    assert m.will_cache(opus5, 512) is True


def test_caching_is_not_promised_for_unmeasured_or_non_claude_models() -> None:
    assert m.will_cache("global.anthropic.claude-opus-4-7", 100_000) is False  # unmeasured
    assert m.supports_prompt_caching("global.amazon.nova-pro-v1:0") is False
    assert m.will_cache("global.amazon.nova-pro-v1:0", 100_000) is False


def test_no_bare_specialist_prompt_caches_on_any_tier_except_heavy() -> None:
    """Measured against the real prompt files, not assumed.

    The customer's specialist prompts are 547-956 estimated tokens. That clears
    Opus 5's 512 minimum and nothing else: not the 1024 of Sonnet 5 / Sonnet 4.6
    / Opus 4.8, and nowhere near Haiku 4.5's 4096. So a bare specialist prompt
    caches only on the HEAVY tier.
    """
    from prompts import DOMAINS, SPECIALIST_PROMPTS

    for domain in DOMAINS:
        bare = m.estimate_prompt_tokens(SPECIALIST_PROMPTS[domain])
        assert 512 <= bare < 1024, (domain, bare)
        assert m.will_cache(m.HEAVY_MODEL, bare) is True, domain
        assert m.will_cache(m.MID_MODEL, bare) is False, domain
        assert m.will_cache(m.LIGHT_MODEL, bare) is False, domain


def test_prepending_orchestration_notes_still_leaves_two_domains_uncacheable_on_haiku() -> None:
    """Even prompt + orchestration notes does not reach Haiku 4.5's 4096.

    dealer (3752) and income (3546) stay short. dealer runs on the LIGHT tier by
    default, so its prompt would silently never cache there - which is exactly
    what the guard is for. The other six clear 4096.
    """
    from prompts import DOMAINS, ORCHESTRATION_NOTES, SPECIALIST_PROMPTS

    short_on_haiku = set()
    for domain in DOMAINS:
        combined = m.estimate_prompt_tokens(ORCHESTRATION_NOTES[domain] + SPECIALIST_PROMPTS[domain])
        # Every domain clears the 1024 minimum of MID and the 512 of HEAVY.
        assert m.will_cache(m.MID_MODEL, combined) is True, domain
        assert m.will_cache(m.HEAVY_MODEL, combined) is True, domain
        if not m.will_cache(m.LIGHT_MODEL, combined):
            short_on_haiku.add(domain)

    assert short_on_haiku == {"dealer", "income"}
    # dealer is LIGHT by default, so this is a live full-price path.
    assert m.model_for("dealer") == m.LIGHT_MODEL
    assert m.model_for("income") == m.MID_MODEL


def test_cached_system_prompt_emits_the_bedrock_cache_point_shape() -> None:
    blocks = m.cached_system_prompt("hello")
    assert blocks == [{"text": "hello"}, {"cachePoint": {"type": "default"}}]


# ---------------------------------------------------------------------------
# Tiering: the saving must equal the arithmetic
# ---------------------------------------------------------------------------


def _uniform_usage(inp: int = 1800, out: int = 450, synth_in: int = 5000, synth_out: int = 700):
    """A per-role usage profile. Illustrative INPUT to the comparison only.

    These counts are supplied by the test as a scenario; they are never returned
    to a caller as a cost. The assertion below is that the code's reported saving
    equals the arithmetic on whatever counts it was given.
    """
    profile = {d: usage(inp, out) for d in m.DOMAINS}
    profile["synthesizer"] = usage(synth_in, synth_out)
    return profile


def test_reported_saving_equals_independently_computed_arithmetic() -> None:
    profile = _uniform_usage()
    result = p.compare_tiered_vs_uniform(profile, as_of=BEFORE_INTRO_EXPIRY)

    expected_tiered = 0.0
    expected_baseline = 0.0
    for role, u in profile.items():
        rates_tiered = p.rates_for(m.model_for(role), as_of=BEFORE_INTRO_EXPIRY)
        rates_base = p.rates_for(m.BASELINE_MODEL, as_of=BEFORE_INTRO_EXPIRY)
        for rates, bucket in ((rates_tiered, "tiered"), (rates_base, "baseline")):
            cost = (
                u["inputTokens"] / 1e6 * rates["input"]
                + u["outputTokens"] / 1e6 * rates["output"]
                + u["cacheReadInputTokens"] / 1e6 * rates["cache_read"]
                + u["cacheWriteInputTokens"] / 1e6 * rates["cache_write_5m"]
            )
            if bucket == "tiered":
                expected_tiered += cost
            else:
                expected_baseline += cost

    assert result["tiered_usd"] == pytest.approx(expected_tiered, rel=1e-12)
    assert result["baseline_usd"] == pytest.approx(expected_baseline, rel=1e-12)
    assert result["saving_usd"] == pytest.approx(expected_baseline - expected_tiered, rel=1e-12)
    assert result["saving_pct"] == pytest.approx(
        (expected_baseline - expected_tiered) / expected_baseline * 100.0, rel=1e-12
    )
    assert result["saving_usd"] == pytest.approx(
        sum(-r["delta_usd"] for r in result["per_role"].values()), rel=1e-12
    )
    assert result["roles_missing"] == []


def test_two_opus_cancelling_two_haiku_reports_a_zero_saving() -> None:
    """The regression this module exists to prevent, reproduced explicitly.

    2 Haiku (1/5) + 5 Sonnet-4.6 (3/15) + 2 Opus-4.8 (5/25) specialists plus a
    Sonnet-4.6 synthesizer, against an all-Sonnet-4.6 baseline, on a uniform
    token profile: the Opus surcharge exactly cancels the Haiku discount. If a
    tiering ever reports a saving, that number must survive this arithmetic.
    """
    per_mtok = 1e6
    inp, out = 1800, 450
    synth_in, synth_out = 5000, 700

    def cost(rate_in: float, rate_out: float, i: int, o: int) -> float:
        return i / per_mtok * rate_in + o / per_mtok * rate_out

    haiku = cost(1.0, 5.0, inp, out)
    sonnet = cost(3.0, 15.0, inp, out)
    opus = cost(5.0, 25.0, inp, out)
    synth = cost(3.0, 15.0, synth_in, synth_out)

    old_tiered = 2 * haiku + 4 * sonnet + 2 * opus + synth
    old_baseline = 8 * sonnet + synth
    assert old_tiered == pytest.approx(old_baseline, abs=1e-12)
    assert old_tiered == pytest.approx(0.12270, abs=1e-9)


def test_current_defaults_produce_a_positive_saving_that_the_code_reports() -> None:
    result = p.compare_tiered_vs_uniform(_uniform_usage(), as_of=BEFORE_INTRO_EXPIRY)
    assert result["saving_usd"] > 0
    assert result["baseline_model"] == m.BASELINE_MODEL
    # Every role is priced on its assigned model, not on the baseline.
    for role, row in result["per_role"].items():
        assert row["model_id"] == m.model_for(role)


def test_saving_decomposes_into_tier_shape_plus_model_generation() -> None:
    result = p.compare_tiered_vs_uniform(_uniform_usage(), as_of=BEFORE_INTRO_EXPIRY)
    assert result["saving_usd"] == pytest.approx(
        result["structural_saving_usd"] + result["model_saving_usd"], rel=1e-12
    )


def test_the_tier_shape_alone_saves_nothing_on_a_uniform_token_mix() -> None:
    """2 Haiku + 2 Opus 5 against an all-Sonnet-4.6 baseline cancel exactly.

    This is the honest headline: on uniform per-agent token counts the current
    tier *structure* is worth $0.00, and the entire reported saving comes from
    the MID tier's model generation. Anyone re-introducing a "tiering saves
    money" claim has to move this number first.
    """
    result = p.compare_tiered_vs_uniform(_uniform_usage(), as_of=BEFORE_INTRO_EXPIRY)
    assert result["structural_saving_usd"] == pytest.approx(0.0, abs=1e-12)
    assert result["model_saving_usd"] == pytest.approx(result["saving_usd"], rel=1e-12)


def test_the_whole_current_saving_expires_with_the_sonnet_5_promo() -> None:
    """MID is Sonnet 5 at 2.00/10.00; on 2026-09-01 it becomes 3.00/15.00.

    At that point MID equals the Sonnet 4.6 baseline and, with the tier shape
    worth zero, the saving is zero. A TCO slide built on today's figure is wrong
    from the expiry date onward, so the comparison reports both.
    """
    result = p.compare_tiered_vs_uniform(_uniform_usage(), as_of=BEFORE_INTRO_EXPIRY)
    assert result["roles_on_introductory_pricing"] == sorted(
        r for r in m.ROLES if m.model_for(r) == m.MID_MODEL
    )
    assert result["introductory_expiry"] == "2026-09-01"

    lapsed = p.compare_tiered_vs_uniform(_uniform_usage(), as_of=AFTER_INTRO_EXPIRY)
    assert lapsed["roles_on_introductory_pricing"] == []
    assert lapsed["saving_usd_after_introductory_expiry"] is None

    # What survives the expiry is exactly the synthesizer substitution -- a different
    # model FAMILY on a non-promotional rate -- and nothing else. The MID tier collapses
    # onto the baseline and the tier shape is still worth zero, so if the synthesizer
    # were ever moved back to a Claude tier this whole saving would go to zero with the
    # promo, which is what the original version of this test asserted.
    assert lapsed["saving_usd"] == pytest.approx(
        lapsed["synthesizer_saving_usd"], rel=1e-9
    )
    assert lapsed["structural_saving_usd"] == pytest.approx(0.0, abs=1e-12)
    assert lapsed["synthesizer_saving_usd"] > 0
    assert lapsed["synthesizer_model"] == m.SYNTHESIZER_MODEL


def test_a_tier_step_down_and_a_tier_step_up_are_worth_the_same_per_token() -> None:
    """Why 2 LIGHT + 2 HEAVY cancels: the rate card's steps are symmetric.

    Haiku 1/5 -> Sonnet 3/15 is a 2.00/10.00 step, and Sonnet 3/15 -> Opus 5/25
    is the same 2.00/10.00 step. So one agent moved down exactly funds one agent
    moved up, at equal token counts. This is arithmetic about the rate card, not
    a property of this pipeline - it is why a symmetric 2-down/2-up tiering can
    never save money on its own.
    """
    u = usage(1800, 450)
    baseline = p.cost_usd(m.BASELINE_MODEL, u, as_of=BEFORE_INTRO_EXPIRY)
    light = p.cost_usd(f"global.{p.BASE_HAIKU_4_5}", u, as_of=BEFORE_INTRO_EXPIRY)
    heavy = p.cost_usd(f"global.{p.BASE_OPUS_5}", u, as_of=BEFORE_INTRO_EXPIRY)
    step_down = baseline - light
    step_up = heavy - baseline
    assert step_down == pytest.approx(step_up, rel=1e-12)


def test_the_brevity_rules_in_the_light_agents_prompts_make_the_shape_negative() -> None:
    """The tier shape actively costs money once the prompts are honoured.

    Because a step down and a step up are worth the same per token, the shape only
    pays if the LIGHT agents carry MORE tokens than the HEAVY ones. The customer's
    prompts say the opposite: straw is capped at '2-3 sentences max' with no
    co-borrower, while synthetic and rings write 'Detailed analysis proportional
    to severity'. So the LIGHT discount shrinks, the HEAVY surcharge does not, and
    the structural saving goes negative.
    """
    profile = {d: usage(1800, 450) for d in m.DOMAINS}
    profile["straw"] = usage(1800, 90)  # '2-3 sentences max'
    profile["dealer"] = usage(1800, 150)
    # The synthesizer is deliberately excluded: it is a model substitution on a
    # different endpoint, not one of the eight tier assignments under test here.
    result = p.compare_tiered_vs_uniform(profile, as_of=BEFORE_INTRO_EXPIRY)
    assert result["structural_saving_usd"] < 0
    # It is still worth running, but only because of the MID model generation.
    assert result["model_saving_usd"] > -result["structural_saving_usd"]
    assert result["saving_usd"] > 0


def test_the_shape_only_pays_when_light_agents_outweigh_heavy_ones() -> None:
    """The one condition under which the tier shape is genuinely worth money."""
    profile = {d: usage(1800, 450) for d in m.DOMAINS}
    profile["straw"] = usage(6000, 1500)
    profile["dealer"] = usage(6000, 1500)
    profile["synthesizer"] = usage(5000, 700)
    result = p.compare_tiered_vs_uniform(profile, as_of=BEFORE_INTRO_EXPIRY)
    assert result["structural_saving_usd"] > 0


def test_a_saving_claim_cannot_be_made_without_usage() -> None:
    with pytest.raises(p.MissingUsageError):
        p.compare_tiered_vs_uniform({})
    with pytest.raises(p.MissingUsageError):
        p.compare_tiered_vs_uniform({"identity": {}})


def test_partial_usage_reports_which_roles_were_not_priced() -> None:
    result = p.compare_tiered_vs_uniform({"identity": usage(100, 50)}, as_of=BEFORE_INTRO_EXPIRY)
    assert result["roles_priced"] == ["identity"]
    assert set(result["roles_missing"]) == set(m.ROLES) - {"identity"}


def test_project_monthly_only_multiplies_a_supplied_cost() -> None:
    projection = p.project_monthly(0.05, 300_000)
    assert projection["daily_usd"] == pytest.approx(15_000.0)
    assert projection["monthly_usd"] == pytest.approx(450_000.0)
    with pytest.raises(p.MissingUsageError):
        p.project_monthly(None, 300_000)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# No fabricated latency
# ---------------------------------------------------------------------------


def test_the_fabricated_latency_table_is_gone() -> None:
    for name in ("NOMINAL_LATENCY_S", "nominal_latency", "SIM_LATENCY_SCALE"):
        assert not hasattr(m, name), f"agents.models must not expose {name}"
    assert "latency" not in m.model_assignment_table().lower()


def test_model_assignment_table_shows_which_tiers_are_prompt_justified() -> None:
    table = m.model_assignment_table()
    for role in m.ROLES:
        assert role in table
    assert m.BASELINE_MODEL in table


def test_every_role_has_a_recorded_tier_rationale() -> None:
    assert set(m.TIER_RATIONALE) == set(m.ROLES)
    justified = {r for r in m.ROLES if m.rationale_for(r).startswith(("LIGHT - justified", "HEAVY - justified"))}
    assert justified == {"straw", "dealer", "synthetic", "rings"}
    # Everything unjustified-by-prompt-text sits on MID, except the synthesizer, whose
    # model was chosen on a latency MEASUREMENT (6.1s vs 55.2s) rather than on a reading
    # of the prompt -- see agents.models.SYNTHESIZER_MODEL.
    for role in set(m.ROLES) - justified - {"synthesizer"}:
        assert m.model_for(role) == m.MID_MODEL, role
    assert m.model_for("synthesizer") == m.SYNTHESIZER_MODEL


def test_justified_rationales_quote_the_customer_prompt_they_rely_on() -> None:
    """Each justified tier must quote text that really exists in docs/."""
    from prompts import SPECIALIST_PROMPTS

    quotes = {
        "straw": "If no co-borrower exists, straw risk is inherently minimal",
        "dealer": "Do NOT escalate fraud risk classification based solely on dealer default rates",
        "synthetic": "thin file + reuse + velocity",
        "rings": "Do NOT recompute network relationships through excessive queries",
    }
    for domain, quote in quotes.items():
        assert quote in SPECIALIST_PROMPTS[domain], domain
        assert quote in m.rationale_for(domain), domain


# ---------------------------------------------------------------------------
# BedrockModel factory (offline shape checks)
# ---------------------------------------------------------------------------


def test_factory_kwargs_all_exist_in_bedrock_config() -> None:
    """A typo in a model_config key is only a UserWarning in strands."""
    from typing_extensions import get_type_hints

    from strands.models import BedrockModel

    valid = set(get_type_hints(BedrockModel.BedrockConfig).keys())
    for key in ("model_id", "max_tokens", "streaming", "temperature", "additional_request_fields"):
        assert key in valid, key
    with pytest.raises(ValueError, match="does not declare"):
        m._validate_model_config({"model_id": "x", "max_token": 10})


def test_client_tuning_constants_address_the_documented_ceilings() -> None:
    from strands.models.bedrock import DEFAULT_READ_TIMEOUT

    assert m.MAX_POOL_CONNECTIONS >= 8 * 2  # 8-way fan-out over botocore's default 10
    # Supplying boto_client_config drops strands' own read_timeout, so it is set
    # explicitly and must be no lower than what strands would have used.
    assert m.READ_TIMEOUT_S == DEFAULT_READ_TIMEOUT
    assert m.RETRY_MAX_ATTEMPTS == 4
    assert m.RETRY_INITIAL_DELAY_S == 1
    assert m.RETRY_MAX_DELAY_S == 8


def test_retry_strategy_backoff_stays_inside_a_sub_minute_budget() -> None:
    strategy = m.build_retry_strategy()
    delays = [strategy._calculate_delay(i) for i in range(m.RETRY_MAX_ATTEMPTS)]
    assert delays == [1, 2, 4, 8]
    assert sum(delays) < 20  # vs [4,8,16,32,64,128] on the SDK default


def test_shared_client_is_one_object_per_region_with_a_raised_pool() -> None:
    m.reset_shared_clients()
    try:
        first = m.shared_bedrock_client("us-east-1")
        second = m.shared_bedrock_client("us-east-1")
        assert first is second
        cfg = first.meta.config
        assert cfg.max_pool_connections == m.MAX_POOL_CONNECTIONS
        assert cfg.read_timeout == m.READ_TIMEOUT_S
        assert cfg.retries["mode"] == "adaptive"
        assert m.shared_bedrock_client("us-west-2") is not first
    finally:
        m.reset_shared_clients()


def test_built_models_share_one_client_and_carry_per_tier_max_tokens() -> None:
    m.reset_shared_clients()
    try:
        light = m.build_bedrock_model(m.LIGHT_MODEL, region="us-east-1")
        heavy = m.build_bedrock_model(m.HEAVY_MODEL, region="us-east-1")
        assert light.client is heavy.client is m.shared_bedrock_client("us-east-1")
        assert light.get_config()["max_tokens"] == m.MAX_TOKENS_BY_TIER["light"]
        assert heavy.get_config()["max_tokens"] == m.MAX_TOKENS_BY_TIER["heavy"]
        assert light.get_config()["model_id"] == m.LIGHT_MODEL
        assert light._supports_caching is True
    finally:
        m.reset_shared_clients()


def test_effort_is_opt_in_and_lands_in_additional_request_fields() -> None:
    m.reset_shared_clients()
    try:
        plain = m.build_bedrock_model(m.HEAVY_MODEL, region="us-east-1")
        assert "additional_request_fields" not in plain.get_config()
        low = m.build_bedrock_model(m.HEAVY_MODEL, region="us-east-1", effort="low")
        assert low.get_config()["additional_request_fields"] == {"output_config": {"effort": "low"}}
    finally:
        m.reset_shared_clients()


def test_unknown_prefix_env_value_raises_instead_of_silently_mispricing(monkeypatch) -> None:
    monkeypatch.setenv("BEDROCK_MODEL_PREFIX", "eu.")
    with pytest.raises(ValueError, match="not supported"):
        m._resolve_prefix()
    monkeypatch.setenv("BEDROCK_MODEL_PREFIX", "us")
    assert m._resolve_prefix() == "us."


def test_specialist_assignment_covers_exactly_the_eight_domains() -> None:
    assert set(m.SPECIALIST_MODELS) == set(m.DOMAINS)
    assert m.model_for("synthesizer") == m.SYNTHESIZER_MODEL
    assert m.tier_label(m.LIGHT_MODEL) == "light"
    assert m.tier_label("global.anthropic.claude-opus-4-8") == "custom"
