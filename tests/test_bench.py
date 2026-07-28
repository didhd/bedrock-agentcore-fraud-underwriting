"""
Guards on the only module allowed to publish a number to the customer.

WHY: the pre-port demo's headline claim was computed from a hand-written
``NOMINAL_LATENCY_S`` dict and printed ``time.sleep(nominal * 0.05)`` beside it
as "measured wall-clock"; per-agent cost came from a hard-coded ``NOMINAL_TOKENS``
table. Point Predictive's engineer will ask where each number came from. These
tests make the four ways that failure mode can come back into the repo fail CI
instead of reaching a slide:

  1. a percentile printed from too small a sample (a one-sample "p99")
  2. a cost figure conjured when no token usage was measured
  3. a 0% cache hit rate shown without saying the prompt cannot cache on Haiku
  4. an offline overhead run presented as though it measured model latency

plus the golden dataset's own integrity: every scenario carries assertions and a
provenance reference back to the `expected` block it encodes.

Run:  python -m pytest tests/test_bench.py -q
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
from prompts.loader import DOMAINS, SPECIALIST_PROMPTS  # noqa: E402

GOLDEN_PATH = REPO_ROOT / "evals" / "datasets" / "underwriting_golden.jsonl"
EVALUATOR_DIR = REPO_ROOT / "evals" / "evaluators"
README_PATH = REPO_ROOT / "evals" / "README.md"


# ---------------------------------------------------------------------------
# 1. Percentiles are refused below the documented minimum sample count
# ---------------------------------------------------------------------------


def test_percentile_minimums_are_documented_and_ordered():
    assert set(bench.PERCENTILE_MIN_SAMPLES) == {"p50", "p95", "p99"}
    assert (
        bench.PERCENTILE_MIN_SAMPLES["p50"]
        < bench.PERCENTILE_MIN_SAMPLES["p95"]
        < bench.PERCENTILE_MIN_SAMPLES["p99"]
    )
    # A p99 needs at least 1/(1-0.99) samples for the tail to be observable.
    assert bench.PERCENTILE_MIN_SAMPLES["p99"] >= 100
    assert bench.PERCENTILE_MIN_SAMPLES["p95"] >= 20


def test_single_sample_yields_no_percentiles_at_all():
    """The exact failure the old demo shipped: a p99 from one measurement."""
    summary = bench.summarize_latencies([1234.0])
    assert summary["n"] == 1
    assert summary["p50_ms"] is None
    assert summary["p95_ms"] is None
    assert summary["p99_ms"] is None
    for name in ("p50", "p95", "p99"):
        assert name in summary["suppressed"]
        assert "need >=" in summary["suppressed"][name]
    # min/max/mean are honest at n=1 and stay populated.
    assert summary["min_ms"] == summary["max_ms"] == 1234.0


@pytest.mark.parametrize("n", [1, 4])
def test_p50_suppressed_below_its_minimum(n):
    summary = bench.summarize_latencies([float(i) for i in range(n)])
    assert summary["p50_ms"] is None
    assert "p50" in summary["suppressed"]


def test_percentiles_appear_exactly_at_their_minimum_sample_count():
    values = [float(i) for i in range(bench.PERCENTILE_MIN_SAMPLES["p50"])]
    summary = bench.summarize_latencies(values)
    assert summary["p50_ms"] is not None
    assert "p50" not in summary["suppressed"]
    # p95/p99 still suppressed at this size.
    assert summary["p95_ms"] is None and summary["p99_ms"] is None

    values = [float(i) for i in range(bench.PERCENTILE_MIN_SAMPLES["p95"])]
    summary = bench.summarize_latencies(values)
    assert summary["p95_ms"] is not None
    assert summary["p99_ms"] is None

    values = [float(i) for i in range(bench.PERCENTILE_MIN_SAMPLES["p99"])]
    summary = bench.summarize_latencies(values)
    assert summary["p99_ms"] is not None
    assert summary["suppressed"] == {}


def test_percentile_values_are_observed_samples_not_interpolated():
    """Nearest-rank never reports a value the run did not actually see."""
    values = [float(i) for i in range(1, 101)]  # 1..100
    summary = bench.summarize_latencies(values)
    for key in ("p50_ms", "p95_ms", "p99_ms"):
        assert summary[key] in values, f"{key}={summary[key]} was never measured"
    assert summary["p50_ms"] == 50.0
    assert summary["p95_ms"] == 95.0
    assert summary["p99_ms"] == 99.0
    assert "nearest-rank" in summary["method"]


def test_percentile_method_is_recorded_so_the_number_is_reproducible():
    assert "no interpolation" in bench.PERCENTILE_METHOD


# ---------------------------------------------------------------------------
# 2. Cost comes from real usage, and missing usage yields None -- never a figure
# ---------------------------------------------------------------------------


def test_cost_is_computed_from_supplied_real_usage():
    usage = bench.TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    # haiku-4-5 global: $1 in / $5 out per 1M -> exactly $6.00
    assert bench.cost_usd(model, usage) == pytest.approx(6.0)


def test_us_profile_costs_exactly_one_point_one_times_global():
    usage = bench.TokenUsage(input_tokens=500_000, output_tokens=250_000)
    global_cost = bench.cost_usd("global.anthropic.claude-opus-4-8", usage)
    us_cost = bench.cost_usd("us.anthropic.claude-opus-4-8", usage)
    assert global_cost is not None and us_cost is not None
    assert us_cost == pytest.approx(global_cost * bench.US_PROFILE_MULTIPLIER)


def test_cached_tokens_are_priced_and_total_input_includes_them():
    """Bedrock's inputTokens counts ONLY non-cached tokens."""
    usage = bench.TokenUsage(
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=1_000,
        cache_write_tokens=500,
    )
    assert usage.total_input_tokens == 1_700
    assert usage.cache_hit is True
    card = bench.rate_card("global.anthropic.claude-sonnet-4-6")
    assert card is not None
    expected = (
        200 / 1_000_000 * card.input_per_mtok
        + 100 / 1_000_000 * card.output_per_mtok
        + 1_000 / 1_000_000 * card.cache_read_per_mtok
        + 500 / 1_000_000 * card.cache_write_per_mtok
    )
    assert bench.cost_usd("global.anthropic.claude-sonnet-4-6", usage) == pytest.approx(expected)
    # Cache read is a tenth of the input rate; write is 1.25x.
    assert card.cache_read_per_mtok == pytest.approx(card.input_per_mtok * 0.1)
    assert card.cache_write_per_mtok == pytest.approx(card.input_per_mtok * 1.25)


def test_missing_usage_yields_none_not_a_fabricated_figure():
    assert bench.cost_usd("us.anthropic.claude-opus-4-8", None) is None


def test_unknown_model_yields_none_rather_than_borrowing_another_price():
    usage = bench.TokenUsage(input_tokens=1000, output_tokens=1000)
    assert bench.cost_usd("us.anthropic.claude-nonexistent-9", usage) is None
    assert bench.rate_card("us.anthropic.claude-nonexistent-9") is None


def test_usage_from_accumulated_requires_the_mandatory_fields():
    assert bench.usage_from_accumulated(None) is None
    assert bench.usage_from_accumulated({}) is None
    assert bench.usage_from_accumulated({"inputTokens": 10}) is None
    assert bench.usage_from_accumulated({"outputTokens": 10}) is None


def test_usage_from_accumulated_treats_cache_fields_as_optional():
    """cacheRead/cacheWrite are absent entirely on a non-cached call."""
    usage = bench.usage_from_accumulated({"inputTokens": 1800, "outputTokens": 420})
    assert usage is not None
    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 0
    assert usage.cache_hit is False

    usage = bench.usage_from_accumulated(
        {
            "inputTokens": 30,
            "outputTokens": 400,
            "cacheReadInputTokens": 1770,
            "cacheWriteInputTokens": 0,
        }
    )
    assert usage is not None
    assert usage.cache_hit is True
    assert usage.total_input_tokens == 1800


def test_sonnet_5_introductory_rate_is_flagged_as_temporary():
    """2/10 reverts to 3/15; a report must not present it as permanent."""
    card = bench.rate_card("global.anthropic.claude-sonnet-5")
    assert card is not None
    assert (card.input_per_mtok, card.output_per_mtok) == (2.0, 10.0)
    assert "introductory" in card.note
    assert bench.SONNET_5_INTRO_ENDS in card.note
    assert bench.SONNET_5_POST_INTRO_RATES == (3.0, 15.0)


def test_rate_card_records_where_its_rates_came_from():
    card = bench.rate_card("us.anthropic.claude-opus-5")
    assert card is not None
    assert card.source and card.source != "unresolved"
    assert card.profile == "us"


def test_rate_card_delegates_to_agents_pricing_and_matches_it():
    """agents.pricing owns the published rate card; bench must not fork it."""
    from agents import pricing as agents_pricing

    for model_id in (
        "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "global.anthropic.claude-sonnet-5",
        "us.anthropic.claude-sonnet-5",
        "global.anthropic.claude-sonnet-4-6",
        "global.anthropic.claude-opus-5",
        "global.anthropic.claude-opus-4-8",
    ):
        owned = agents_pricing.rates_for(model_id)
        card = bench.rate_card(model_id)
        assert card is not None, model_id
        assert card.source == "agents.pricing.rates_for", (
            f"{model_id}: bench used its own fallback while agents.pricing had a rate"
        )
        assert card.input_per_mtok == pytest.approx(owned["input"]), model_id
        assert card.output_per_mtok == pytest.approx(owned["output"]), model_id
        assert card.cache_read_per_mtok == pytest.approx(owned["cache_read"]), model_id
        assert card.cache_write_per_mtok == pytest.approx(owned["cache_write_applied"]), model_id


def test_a_model_agents_pricing_refuses_to_price_yields_no_cost():
    """opus-4-7 has no verified Bedrock rate, so it must price as None.

    agents.pricing raises UnknownModelRateError rather than guessing; bench must
    surface that as an unpriced call, not fall back to a similar model's rate.
    """
    from agents import pricing as agents_pricing

    model_id = "global.anthropic.claude-opus-4-7"
    with pytest.raises(agents_pricing.UnknownModelRateError):
        agents_pricing.rates_for(model_id)
    assert bench.rate_card(model_id) is None
    usage = bench.TokenUsage(input_tokens=1000, output_tokens=1000)
    assert bench.cost_usd(model_id, usage) is None


def test_model_family_does_not_confuse_sonnet_4_6_with_sonnet_5():
    assert bench.model_family("us.anthropic.claude-sonnet-4-6") == "sonnet-4-6"
    assert bench.model_family("global.anthropic.claude-sonnet-5") == "sonnet-5"
    assert bench.model_family("us.anthropic.claude-haiku-4-5-20251001-v1:0") == "haiku-4-5"
    assert bench.model_family("global.anthropic.claude-opus-4-8") == "opus-4-8"
    assert bench.model_family("us.anthropic.claude-opus-5") == "opus-5"


# ---------------------------------------------------------------------------
# 3. Min-cacheable-prefix: a ~1800-token prompt never caches on Haiku 4.5
# ---------------------------------------------------------------------------

#: Representative specialist prompt size. Roughly what SPECIALIST_PROMPTS plus a
#: projected payload comes to, and the size at which the non-monotonic minimums
#: actually bite.
REPRESENTATIVE_PROMPT_TOKENS = 1800


def test_haiku_45_is_flagged_as_never_cacheable_for_an_1800_token_prompt():
    verdict = bench.cacheability(
        "us.anthropic.claude-haiku-4-5-20251001-v1:0", REPRESENTATIVE_PROMPT_TOKENS
    )
    assert verdict["verdict"] == "never_cacheable"
    assert verdict["min_cacheable_prefix_tokens"] == 4096
    # The explanation must say a 0% hit rate here is expected, not a bug.
    assert "NEVER cache" in verdict["explanation"]
    assert "0% hit rate" in verdict["explanation"]


@pytest.mark.parametrize(
    "model_id,minimum",
    [
        ("us.anthropic.claude-opus-5", 512),
        ("global.anthropic.claude-opus-4-8", 1024),
        ("global.anthropic.claude-sonnet-5", 1024),
        ("us.anthropic.claude-sonnet-4-6", 1024),
        ("us.anthropic.claude-haiku-4-5-20251001-v1:0", 4096),
    ],
)
def test_min_cacheable_prefix_per_model(model_id, minimum):
    assert bench.min_cacheable_prefix_tokens(model_id) == minimum


def test_the_same_prompt_caches_on_every_model_except_haiku():
    """The minimum is NOT monotonic across generations -- that is the trap."""
    cacheable = [
        "us.anthropic.claude-opus-5",
        "global.anthropic.claude-opus-4-8",
        "global.anthropic.claude-sonnet-5",
        "us.anthropic.claude-sonnet-4-6",
    ]
    for model_id in cacheable:
        verdict = bench.cacheability(model_id, REPRESENTATIVE_PROMPT_TOKENS)
        assert verdict["verdict"] == "cacheable", model_id
    haiku = bench.cacheability(
        "us.anthropic.claude-haiku-4-5-20251001-v1:0", REPRESENTATIVE_PROMPT_TOKENS
    )
    assert haiku["verdict"] == "never_cacheable"


def test_cache_minimums_defer_to_agents_models_and_cannot_diverge():
    """agents.models owns the table; bench must not carry a second opinion.

    Two copies of a threshold is how one of them goes stale. If agents.models
    exposes the model, its figure wins and this asserts they agree.
    """
    from agents.models import min_cacheable_tokens

    for model_id in (
        "global.anthropic.claude-opus-5",
        "global.anthropic.claude-sonnet-5",
        "global.anthropic.claude-sonnet-4-6",
        "global.anthropic.claude-opus-4-8",
        "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    ):
        owned = min_cacheable_tokens(model_id)
        assert owned is not None, f"agents.models lost its entry for {model_id}"
        assert bench.min_cacheable_prefix_tokens(model_id) == owned, model_id


def test_unmeasured_prompt_size_reports_unknown_not_a_verdict():
    verdict = bench.cacheability("us.anthropic.claude-sonnet-4-6", None)
    assert verdict["verdict"] == "unknown"
    assert "not measured" in verdict["explanation"]


def test_real_assembled_prompts_land_inside_the_haiku_cliff_band():
    """Sanity-check the premise so the cliff is real, not hypothetical.

    What gets cached is the ASSEMBLED prompt -- the verbatim system prompt plus
    the projected application payload -- not the system prompt alone. Sized with
    a deliberately conservative ~4 chars/token approximation (this is a premise
    check, not a published figure; only a live run measures real token counts).

    Every domain must land above Sonnet/Opus 4.8's 1024-token minimum and below
    Haiku 4.5's 4096, because that band is precisely where the non-monotonic
    minimum bites: cacheable on four models, never cacheable on the fifth.
    """
    _, get_app, _ = bench.resolve_application_source()
    payload = bench.render_payload(get_app(bench.resolve_application_source()[0]()[0]))
    for domain in DOMAINS:
        approx_tokens = len(bench.build_specialist_prompt(domain, payload)) / 4
        assert 1024 < approx_tokens < 4096, (
            f"{domain}: assembled prompt is ~{approx_tokens:.0f} tokens, outside the "
            "1024-4096 band the cacheability reporting is calibrated for; re-check "
            "evals/README.md section 4 before trusting its verdicts"
        )
        # Above every minimum except Haiku's, which is the whole point.
        assert bench.min_cacheable_prefix_tokens("us.anthropic.claude-sonnet-4-6") < approx_tokens
        assert approx_tokens < bench.min_cacheable_prefix_tokens(
            "us.anthropic.claude-haiku-4-5-20251001-v1:0"
        )


# ---------------------------------------------------------------------------
# 4. Offline mode must label itself as not measuring model latency
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def offline_report():
    """A real offline run. Small, so the suite stays fast."""
    import asyncio

    return asyncio.run(bench.run_offline(concurrency_levels=[1, 2], iterations=3))


def test_offline_report_says_model_latency_is_not_measured(offline_report):
    caveats = " ".join(offline_report["caveats"])
    assert "MODEL LATENCY IS NOT MEASURED IN OFFLINE MODE" in caveats
    assert "No Amazon Bedrock call was made" in caveats
    assert offline_report["run"]["mode"] == "offline"


def test_offline_human_table_carries_the_warning_in_its_header(offline_report):
    rendered = bench.render_human(offline_report)
    header = rendered.split("Per-agent wall clock")[0]
    assert "OFFLINE MODE" in header
    assert "MODEL LATENCY IS NOT MEASURED" in header
    assert "NO Amazon Bedrock call was made" in header


def test_offline_report_has_no_cost_figures_anywhere(offline_report):
    """No token usage was measured, so cost must be null -- not 0.0."""
    assert offline_report["totals"]["total_cost_usd"] is None
    assert offline_report["totals"]["cost_per_adjudication_usd"] is None
    assert offline_report["totals"]["calls_with_real_usage"] == 0
    for agent, data in offline_report["per_agent"].items():
        assert data["cost_usd"]["total"] is None, agent
        assert data["cost_usd"]["mean_per_call"] is None, agent
        assert data["cost_usd"]["note"] is not None, agent
        assert data["prompt_cache"]["hit_rate"] is None, agent


def _measurement_with(costs: list[float | None]) -> bench.IterationMeasurement:
    """One synthetic adjudication whose per-call costs are given."""
    agents = [*DOMAINS, "synthesis"]
    calls = [
        bench.CallMeasurement(
            agent=agents[i],
            model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
            wall_ms=100.0,
            usage=(
                bench.TokenUsage(input_tokens=1000, output_tokens=100)
                if cost is not None
                else None
            ),
            cost_usd=cost,
            error=None if cost is not None else "MaxTokensReachedException: ...",
        )
        for i, cost in enumerate(costs)
    ]
    return bench.IterationMeasurement(
        application_id="APP-TEST",
        concurrency=1,
        iteration=0,
        cold=True,
        e2e_wall_ms=1000.0,
        calls=calls,
    )


def _report_for(measurements: list[bench.IterationMeasurement]) -> dict:
    return bench.build_report(
        env=bench.run_environment("live", "us-east-1", {}),
        measurements=measurements,
        models={},
        application_source="synthetic",
        applications=["APP-TEST"],
        measured=["synthetic"],
        not_measured=["synthetic"],
        cacheability_reports=[],
    )


def test_per_adjudication_cost_excludes_adjudications_with_a_failed_call():
    """A partial adjudication must not deflate the per-application cost.

    Regression: a real live run had `rings` fail with MaxTokensReachedException,
    and the eight surviving calls were averaged and published as the cost of an
    adjudication -- an under-count wearing a total's name.
    """
    incomplete = _measurement_with([0.01] * 8 + [None])  # synthesis failed
    report = _report_for([incomplete])
    assert report["totals"]["errors"] == 1
    assert report["totals"]["complete_adjudications"] == 0
    assert report["totals"]["incomplete_adjudications"] == 1
    assert report["totals"]["cost_per_adjudication_usd"] is None
    assert "no per-application cost was measured" in (
        report["totals"]["cost_per_adjudication_note"] or ""
    )
    # The partial spend is still reported, just not as a per-adjudication cost.
    assert report["totals"]["total_cost_usd"] == pytest.approx(0.08)
    assert any("EXCLUDED from cost_per_adjudication_usd" in c for c in report["caveats"])


def test_per_adjudication_cost_uses_only_complete_adjudications():
    complete = _measurement_with([0.01] * 9)
    incomplete = _measurement_with([0.01] * 8 + [None])
    report = _report_for([complete, incomplete])
    assert report["totals"]["complete_adjudications"] == 1
    assert report["totals"]["incomplete_adjudications"] == 1
    # 9 x 0.01 from the complete run only -- not (0.09 + 0.08) / 2.
    assert report["totals"]["cost_per_adjudication_usd"] == pytest.approx(0.09)


def test_offline_report_reports_no_ttft_and_no_sdk_latency(offline_report):
    for agent, data in offline_report["per_agent"].items():
        assert data["time_to_first_token"]["n"] == 0, agent
        assert data["sdk_reported_latency"]["n"] == 0, agent


def test_offline_report_carries_run_provenance(offline_report):
    run = offline_report["run"]
    for key in ("timestamp_utc", "git_sha", "git_branch", "aws_region", "model_ids"):
        assert key in run, key
    assert run["model_ids"], "model ids must be recorded with every run"
    assert set(run["packages"]) >= {"strands-agents", "bedrock-agentcore", "boto3"}


def test_offline_report_names_every_agent_and_the_synthesizer(offline_report):
    assert set(offline_report["per_agent"]) == {*DOMAINS, "synthesis"}
    assert offline_report["workload"]["calls_per_adjudication"] == len(DOMAINS) + 1


def test_offline_report_records_sample_counts_next_to_every_percentile(offline_report):
    for agent, data in offline_report["per_agent"].items():
        assert "n" in data["wall_clock"], agent
        for name in ("p50", "p95", "p99"):
            if data["wall_clock"][f"{name}_ms"] is None:
                assert name in data["wall_clock"]["suppressed"], f"{agent}/{name}"


def test_offline_report_covers_cold_and_warm_at_each_concurrency(offline_report):
    assert set(offline_report["by_concurrency"]) == {"1", "2"}
    for level, data in offline_report["by_concurrency"].items():
        assert "end_to_end_cold" in data and "end_to_end_warm" in data, level


def test_offline_report_is_json_serializable(offline_report):
    """The UI and README consume this file; it must round-trip."""
    round_tripped = json.loads(json.dumps(offline_report))
    assert round_tripped["schema"] == "pointpredictive.agentcore.bench/1"


# ---------------------------------------------------------------------------
# Live mode is double-gated
# ---------------------------------------------------------------------------


def test_live_requires_both_the_flag_and_the_env_var(monkeypatch):
    monkeypatch.delenv(bench.LIVE_ENV_GATE, raising=False)
    assert bench.live_gate_error(args_live=False) is not None
    gate = bench.live_gate_error(args_live=True)
    assert gate is not None and bench.LIVE_ENV_GATE in gate
    monkeypatch.setenv(bench.LIVE_ENV_GATE, "1")
    assert bench.live_gate_error(args_live=True) is None
    monkeypatch.setenv(bench.LIVE_ENV_GATE, "true")  # only "1" counts
    assert bench.live_gate_error(args_live=True) is not None


def test_live_cli_refuses_without_the_env_var(monkeypatch, capsys):
    monkeypatch.delenv(bench.LIVE_ENV_GATE, raising=False)
    exit_code = bench.main(["--live", "--iterations", "1"])
    assert exit_code == 2
    assert "refusing to run live" in capsys.readouterr().err


def test_live_defaults_to_a_small_sample():
    assert bench.LIVE_DEFAULT_ITERATIONS <= 3


def test_count_tokens_strips_the_inference_profile_prefix(monkeypatch):
    """MEASURED: CountTokens rejects a global.*/us.* prefix, needs the bare id.

    Both prefixed forms raise ValidationException ("The provided model doesn't
    support counting tokens") while the bare anthropic.* id succeeds, so the
    prefix must be stripped before the call. Token counts are a property of the
    model, not the routing profile, so stripping changes no answer.
    """
    seen: list[str] = []

    class FakeClient:
        def count_tokens(self, *, modelId, input):  # noqa: N803 - boto3's casing
            seen.append(modelId)
            return {"inputTokens": 1829}

    fake_boto3 = type("FakeBoto3", (), {"client": staticmethod(lambda *a, **k: FakeClient())})
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    for model_id in (
        "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "anthropic.claude-haiku-4-5-20251001-v1:0",
    ):
        assert bench.count_prompt_tokens(model_id, "prompt", "us-east-1") == 1829
    assert seen == ["anthropic.claude-haiku-4-5-20251001-v1:0"] * 3, (
        f"CountTokens was called with a prefixed model id: {seen}"
    )


def test_count_tokens_returns_none_when_the_model_does_not_support_it(monkeypatch):
    """MEASURED: sonnet-5 and opus-5 raise ValidationException in us-east-1."""

    class FakeClient:
        def count_tokens(self, *, modelId, input):  # noqa: N803
            raise RuntimeError("ValidationException: The provided model doesn't support counting tokens")

    fake_boto3 = type("FakeBoto3", (), {"client": staticmethod(lambda *a, **k: FakeClient())})
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    assert bench.count_prompt_tokens("global.anthropic.claude-sonnet-5", "p", "us-east-1") is None


#: MEASURED via Bedrock CountTokens (us-east-1, 2026-07-28) on the assembled
#: prompt: verbatim system prompt + projected APP-1004 payload.
MEASURED_HAIKU_PROMPT_TOKENS = {"dealer": 1829, "straw": 1655}


@pytest.mark.parametrize("domain,tokens", sorted(MEASURED_HAIKU_PROMPT_TOKENS.items()))
def test_measured_haiku_prompts_are_reported_as_never_cacheable(domain, tokens):
    """The cliff, from a real measurement rather than an assumption.

    Both Haiku-routed specialists measured ~1.7k tokens against Haiku 4.5's
    4096-token minimum, so their cache hit rate is permanently 0%.
    """
    models = bench.resolve_specialist_models()
    model_id = models[domain]
    assert bench.model_family(model_id) == "haiku-4-5", (
        f"{domain} is no longer routed to Haiku; re-measure before trusting "
        f"MEASURED_HAIKU_PROMPT_TOKENS[{domain!r}]"
    )
    verdict = bench.cacheability(model_id, tokens)
    assert verdict["verdict"] == "never_cacheable"
    assert verdict["min_cacheable_prefix_tokens"] == 4096
    assert "0% hit rate" in verdict["explanation"]


def test_spend_estimate_is_labelled_an_upper_bound_and_never_guesses_tokens(monkeypatch):
    """No CountTokens access must produce a null cost, not an approximation."""
    monkeypatch.setattr(bench, "count_prompt_tokens", lambda *a, **k: None)
    estimate = bench.estimate_live_spend(
        models={"identity": "us.anthropic.claude-sonnet-4-6"},
        prompts={"identity": "x"},
        region="us-east-1",
        iterations=1,
        concurrency_levels=[1],
        max_tokens=512,
    )
    assert estimate["is_upper_bound"] is True
    assert estimate["complete"] is False
    assert estimate["total_upper_bound_usd"] is None
    assert estimate["per_agent"]["identity"]["upper_bound_usd"] is None
    assert "NOT estimated" in estimate["per_agent"]["identity"]["note"]


def test_spend_estimate_prices_from_a_real_token_count(monkeypatch):
    monkeypatch.setattr(bench, "count_prompt_tokens", lambda *a, **k: 1_000_000)
    estimate = bench.estimate_live_spend(
        models={"dealer": "global.anthropic.claude-haiku-4-5-20251001-v1:0"},
        prompts={"dealer": "x"},
        region="us-east-1",
        iterations=1,
        concurrency_levels=[1],
        max_tokens=1_000_000,
    )
    assert estimate["complete"] is True
    # 1M in at $1 + 1M out at $5 = $6.00
    assert estimate["per_agent"]["dealer"]["upper_bound_usd"] == pytest.approx(6.0)
    assert "max_tokens ceiling" in estimate["basis"]


# ---------------------------------------------------------------------------
# 5. The golden dataset parses; every entry has assertions and provenance
# ---------------------------------------------------------------------------


def load_golden() -> list[dict]:
    """Parse the dataset the way the AgentCore CLI parses it."""
    rows = []
    with GOLDEN_PATH.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                pytest.fail(f"Invalid JSON at line {lineno}: {exc}")
    return rows


@pytest.fixture(scope="module")
def golden():
    return load_golden()


def test_golden_dataset_exists_and_is_non_empty(golden):
    assert golden, f"{GOLDEN_PATH} has no scenarios"


def test_golden_dataset_satisfies_the_cli_required_fields(golden):
    """Mirrors the CLI's own parseAndValidate: scenario_id, non-empty turns,
    and a string ``input`` on every turn."""
    seen: set[str] = set()
    for i, row in enumerate(golden, start=1):
        assert isinstance(row.get("scenario_id"), str) and row["scenario_id"], (
            f'Line {i}: missing required field "scenario_id"'
        )
        assert row["scenario_id"] not in seen, f"duplicate scenario_id {row['scenario_id']}"
        seen.add(row["scenario_id"])
        turns = row.get("turns")
        assert isinstance(turns, list) and turns, f'Line {i}: "turns" must be a non-empty array'
        for j, turn in enumerate(turns, start=1):
            assert isinstance(turn.get("input"), str) and turn["input"], (
                f'Line {i}, turn {j}: each turn must have a string "input" field'
            )


def test_every_turn_carries_an_expected_response(golden):
    for row in golden:
        for turn in row["turns"]:
            assert isinstance(turn.get("expectedResponse"), str) and turn["expectedResponse"], (
                f"{row['scenario_id']}: every turn needs an expectedResponse"
            )


def test_every_scenario_has_assertions(golden):
    for row in golden:
        assertions = row.get("assertions")
        assert isinstance(assertions, list) and assertions, (
            f"{row['scenario_id']}: assertions must be a non-empty list"
        )
        for assertion in assertions:
            assert isinstance(assertion, str) and assertion.strip(), row["scenario_id"]


def test_every_scenario_has_an_expected_trajectory_field(golden):
    """Present on every scenario; empty only where calling nothing is correct."""
    for row in golden:
        assert "expected_trajectory" in row, row["scenario_id"]
        assert isinstance(row["expected_trajectory"], list), row["scenario_id"]
    empty = [r["scenario_id"] for r in golden if not r["expected_trajectory"]]
    # The RETRO deflection is the one scenario whose correct trajectory is empty.
    assert all("retro" in sid for sid in empty), (
        f"an empty expected_trajectory needs a reason; found {empty}"
    )


def test_every_scenario_references_the_expected_block_it_encodes(golden):
    """Provenance: which `expected` block, and which verbatim prompt rule."""
    for row in golden:
        prov = row.get("expected_provenance")
        assert isinstance(prov, dict), f"{row['scenario_id']}: missing expected_provenance"
        assert prov.get("expected_ref"), f"{row['scenario_id']}: missing expected_ref"
        assert prov.get("fallback_source"), f"{row['scenario_id']}: missing fallback_source"
        rules = prov.get("rules")
        assert isinstance(rules, list) and rules, f"{row['scenario_id']}: missing rules"
        for rule in rules:
            assert "::" in rule or "prompts/" in rule, (
                f"{row['scenario_id']}: rule must cite a source file: {rule!r}"
            )


def test_full_review_scenarios_expect_all_eight_specialists(golden):
    full = [r for r in golden if len(r["expected_trajectory"]) == len(DOMAINS)]
    assert full, "no full-review scenario in the dataset"
    for row in full:
        assert row["expected_trajectory"] == [f"call_{d}_agent" for d in DOMAINS], (
            f"{row['scenario_id']}: trajectory must be the customer's domain order"
        )


def test_full_review_scenarios_encode_the_21_key_contract(golden):
    """The exact key order, and the absence of application_id."""
    expected_order = (
        "master_summary, overall_risk_decision, recommendation_decision, "
        "recommended_stipulations, top_risk_factors, identity_summary, identity_flag, "
        "dealer_summary, dealer_flag, straw_summary, straw_flag, employment_summary, "
        "employment_flag, income_summary, income_flag, synthetic_summary, "
        "synthetic_flag, bustout_summary, bustout_flag, rings_summary, rings_flag"
    )
    full = [r for r in golden if len(r["expected_trajectory"]) == len(DOMAINS)]
    for row in full:
        joined = " ".join(row["assertions"])
        assert expected_order in joined, f"{row['scenario_id']}: 21-key order not asserted"
        assert "no application_id key" in joined, row["scenario_id"]
        assert "LOW RISK, MEDIUM RISK, HIGH RISK" in joined, row["scenario_id"]
        assert "APPROVE, REVIEW AND APPLY STIPULATIONS, DECLINE" in joined, row["scenario_id"]
        assert "under 1200 characters" in joined, row["scenario_id"]


def test_dataset_covers_every_anti_false_positive_rule(golden):
    """The five rules the customer will look for, each with its own scenario."""
    joined = {r["scenario_id"]: " ".join(r["assertions"]) for r in golden}
    combined = " ".join(joined.values()).lower()
    assert "of 900 or above" in combined, "dealer 900+ consortium score rule missing"
    assert "credit-risk or portfolio context" in combined
    assert "underwriting data gap" in combined, "all-null employer fields rule missing"
    assert "staffing agencies" in combined, "staffing-agency SSN volume rule missing"
    assert "multi-unit or dense housing" in combined, "address-reuse rule missing"
    assert "alert volume alone does not indicate risk" in combined, "alert-volume rule missing"


def test_dataset_pins_titles_where_defined_and_no_title_for_bustout_and_rings(golden):
    combined = " ".join(a for r in golden for a in r["assertions"])
    for title in (
        "Identity Fraud Risk Analysis",
        "Employment Fraud Risk Analysis",
        "Dealer Fraud Risk Analysis",
        "Straw Borrower Fraud Risk Analysis",
    ):
        assert f"titled exactly '{title}'" in combined, f"{title} not pinned"
    assert "has no title" in combined, "the no-title rule for rings/bustout is not asserted"


def test_dataset_pins_the_length_rules(golden):
    combined = " ".join(a for r in golden for a in r["assertions"])
    assert "3 to 5 sentences" in combined, "LOW RISK 3-5 sentence rule missing"
    assert "2 or 3 sentences" in combined, "straw no-co-borrower 2-3 sentence rule missing"


def test_dataset_pins_the_francis_response_contract(golden):
    francis = [r for r in golden if r["scenario_id"].startswith("francis_")]
    assert francis, "no Francis interactive scenario"
    combined = " ".join(a for r in francis for a in r["assertions"])
    assert "## EXECUTIVE SUMMARY" in combined
    assert "## ANALYSIS" in combined
    assert "###RECOMMENDATIONS" in combined
    assert "At most 2 tools" in combined
    assert "RETRO_FRANCIS" in combined
    assert "never identifies itself as Claude" in combined


def test_golden_ids_are_snake_case_and_stable(golden):
    for row in golden:
        sid = row["scenario_id"]
        assert sid == sid.lower(), sid
        assert " " not in sid, sid


# ---------------------------------------------------------------------------
# Evaluator definitions match the CLI schema and quote the customer verbatim
# ---------------------------------------------------------------------------

#: From the CLI's evaluator.js: EvaluatorNameSchema and EvaluationLevelSchema.
EVALUATOR_NAME_MAX = 48
EVALUATION_LEVELS = {"SESSION", "TRACE", "TOOL_CALL"}
#: From the CLI bundle (IPe): instructions must contain at least one
#: level-appropriate placeholder.
LEVEL_PLACEHOLDERS = {
    "SESSION": ("context", "available_tools"),
    "TRACE": ("context", "assistant_turn"),
    "TOOL_CALL": ("available_tools", "context", "tool_turn"),
}
#: From the CLI's tags.js: TagsSchema restricts value characters and length.
TAG_VALUE_MAX = 256


@pytest.fixture(scope="module")
def evaluators():
    paths = sorted(EVALUATOR_DIR.glob("*.json"))
    assert paths, f"no evaluator definitions in {EVALUATOR_DIR}"
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in paths}


def test_evaluators_match_the_cli_schema(evaluators):
    import re

    name_pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")
    tag_pattern = re.compile(r"^[\w\s.:/=+\-@]*$", re.UNICODE)
    for filename, spec in evaluators.items():
        assert set(spec) <= {"name", "level", "description", "config", "kmsKeyArn", "tags"}, filename
        assert name_pattern.match(spec["name"]), filename
        assert len(spec["name"]) <= EVALUATOR_NAME_MAX, filename
        assert spec["level"] in EVALUATION_LEVELS, filename
        config = spec["config"]
        # XOR: llmAsAJudge or codeBased, never both.
        assert ("llmAsAJudge" in config) != ("codeBased" in config), filename
        for key, value in spec.get("tags", {}).items():
            assert len(value) <= TAG_VALUE_MAX, f"{filename}: tag {key} too long"
            assert tag_pattern.match(value), f"{filename}: tag {key} has invalid characters"


def test_llm_judges_have_valid_rating_scales_and_placeholders(evaluators):
    judges = {f: s for f, s in evaluators.items() if "llmAsAJudge" in s["config"]}
    assert judges, "no llm-as-a-judge evaluator defined"
    for filename, spec in judges.items():
        judge = spec["config"]["llmAsAJudge"]
        assert judge["model"] and judge["instructions"]
        scale = judge["ratingScale"]
        # XOR: numerical or categorical, never both.
        assert ("numerical" in scale) != ("categorical" in scale), filename
        if "numerical" in scale:
            for entry in scale["numerical"]:
                assert isinstance(entry["value"], int), filename
                assert entry["label"] and entry["definition"], filename
        else:
            for entry in scale["categorical"]:
                assert entry["label"] and entry["definition"], filename
        allowed = LEVEL_PLACEHOLDERS[spec["level"]]
        assert any(f"{{{p}}}" in judge["instructions"] for p in allowed), (
            f"{filename}: instructions need one of {allowed}"
        )


def test_code_based_evaluator_declares_a_managed_or_external_config(evaluators):
    code_based = {f: s for f, s in evaluators.items() if "codeBased" in s["config"]}
    assert code_based, "no code-based contract checker defined"
    for filename, spec in code_based.items():
        cfg = spec["config"]["codeBased"]
        assert ("managed" in cfg) != ("external" in cfg), filename
        if "managed" in cfg:
            managed = cfg["managed"]
            assert managed["codeLocation"] and managed["entrypoint"], filename
            assert 1 <= managed["timeoutSeconds"] <= 300, filename


def test_calibration_judge_quotes_the_customers_calibration_verbatim(evaluators):
    """The judge's standard must be the customer's own text, not a paraphrase."""
    judge = evaluators["pp_calibration_judge.json"]
    instructions = judge["config"]["llmAsAJudge"]["instructions"]
    for domain in DOMAINS:
        lines = SPECIALIST_PROMPTS[domain].split("\n")
        header = "CALIBRATION:" if "CALIBRATION:" in lines else "DECISION FRAMEWORK:"
        start = lines.index(header)
        # First calibration bullet is enough to prove the block was copied,
        # and it is the line most likely to be silently reworded.
        first_bullet = next(l for l in lines[start + 1 :] if l.startswith("- "))
        assert first_bullet in instructions, (
            f"{domain}: calibration text is not verbatim in the judge instructions"
        )


def test_calibration_judge_states_the_three_governing_rules(evaluators):
    instructions = evaluators["pp_calibration_judge.json"]["config"]["llmAsAJudge"]["instructions"]
    assert "LOW RISK must be the most common outcome" in instructions
    assert "Alert volume alone does NOT indicate risk" in instructions
    assert "HIGH RISK requires multiple independent, corroborated patterns" in instructions


def test_contract_checker_names_every_deterministic_rule_it_enforces(evaluators):
    description = evaluators["pp_output_contract_check.json"]["description"]
    for fragment in (
        "Exact titles",
        "NO title for bustout/rings",
        "LOW = 3-5 sentences",
        "4000 char",
        "straw with no co-borrower",
        "Alert <n>",
        "emoji",
        "risk label in a section header",
        "21-key JSON",
        "1200 chars",
    ):
        assert fragment in description, f"contract checker does not mention {fragment!r}"


def test_false_positive_evaluator_encodes_all_five_customer_rules(evaluators):
    instructions = evaluators["pp_false_positive_discipline.json"]["config"]["llmAsAJudge"][
        "instructions"
    ]
    assert "900 or above" in instructions
    assert "UNDERWRITING DATA GAP" in instructions
    assert "staffing agencies" in instructions
    assert "EXACT-UNIT" in instructions
    assert "Alert volume alone is not risk" in instructions


# ---------------------------------------------------------------------------
# README must state what has and has not been measured
# ---------------------------------------------------------------------------


def test_readme_states_the_measurement_status_honestly():
    text = README_PATH.read_text(encoding="utf-8")
    assert "HAS NOT BEEN MEASURED" in text
    assert "NOMINAL_LATENCY_S" in text, (
        "the README must name the fabricated table this harness replaces"
    )
    for command in (
        "agentcore add evaluator",
        "agentcore add dataset",
        "agentcore dataset publish-version",
        "agentcore run eval",
        "agentcore run batch-evaluation",
        "agentcore add online-eval",
    ):
        assert command in text, f"README is missing the {command!r} command"

    # `agentcore configure` and `agentcore launch` do not exist in this CLI (they
    # print help and exit 0). The README is allowed to SAY they do not exist --
    # and does -- but must never present one inside a runnable command block.
    for phantom in ("agentcore configure", "agentcore launch"):
        assert phantom in text, (
            f"README should warn that {phantom!r} does not exist in this CLI"
        )
    in_code_block = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            for phantom in ("agentcore configure", "agentcore launch"):
                assert phantom not in line, (
                    f"evals/README.md:{lineno} puts the non-existent {phantom!r} in a "
                    "runnable command block"
                )
