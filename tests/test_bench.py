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

    # `agentcore configure` and `agentcore launch` do not exist in this CLI: it prints
    # `error: unknown command` and exits 1. (The exit-0 no-op belongs to the DEPRECATED pip
    # starter toolkit, whose console script is also named `agentcore` -- attributing that
    # behaviour to the npm CLI is a mistake this repo has made twice.) The README is allowed
    # to SAY they do not exist -- and does -- but must never present one inside a runnable
    # command block.
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


# ---------------------------------------------------------------------------
# 7. Pipeline mode: the aggregation that produces the published p50/p95
#
# WHY THESE EXIST. --pipeline is the mode that answers "how long does one
# application take". Every guard below is a way that answer can go quietly wrong:
# a p95 printed from 3 samples, an under-60s counter that rounds a 60.4s run down,
# a degraded run averaged into the cost as though nine calls had answered, a
# speed-up published as one number when it is a distribution. All offline: they run
# on synthetic PipelineRun objects and call no model.
# ---------------------------------------------------------------------------


def _specialist(
    domain: str,
    wall_ms: float,
    *,
    error: str | None = None,
    priced: bool = True,
    cost: float = 0.01,
    in_tokens: int = 2000,
    out_tokens: int = 300,
) -> bench.SpecialistMeasurement:
    """One synthetic specialist measurement."""
    usage = (
        {
            "inputTokens": in_tokens,
            "outputTokens": out_tokens,
            "totalTokens": in_tokens + out_tokens,
            "cacheReadInputTokens": 0,
            "cacheWriteInputTokens": 0,
        }
        if priced and error is None
        else None
    )
    return bench.SpecialistMeasurement(
        domain=domain,
        agent_name=f"AGENT_{domain.upper()}",
        model_id="global.anthropic.claude-sonnet-5",
        tier="mid",
        wall_ms=wall_ms,
        latency_ms=wall_ms - 100.0,
        usage=usage,
        cost_usd=(cost if usage else None),
        stop_reason="end_turn" if error is None else None,
        error=error,
        analysis_chars=1200,
        source="bedrock",
    )


def _pipeline_run(
    application_id: str = "APP-1001",
    repetition: int = 1,
    *,
    e2e_ms: float = 30_000.0,
    fanout_ms: float = 25_000.0,
    synthesis_ms: float | None = 5_000.0,
    specialist_walls: dict[str, float] | None = None,
    failed: dict[str, str] | None = None,
    synthesis_error: str | None = None,
    synthesis_cost: float | None = 0.015,
    validated: bool = True,
    repair_attempts: int = 0,
    specialist_cost: float = 0.01,
) -> bench.PipelineRun:
    """One synthetic adjudication, in the shape run_pipeline produces."""
    failed = failed or {}
    walls = specialist_walls or {d: 10_000.0 + 100 * i for i, d in enumerate(DOMAINS)}
    specialists = [
        _specialist(d, walls[d], error=failed.get(d), cost=specialist_cost) for d in DOMAINS
    ]
    usage = (
        {
            "inputTokens": 28_000,
            "outputTokens": 800,
            "totalTokens": 28_800,
            "cacheReadInputTokens": 0,
            "cacheWriteInputTokens": 0,
        }
        if synthesis_cost is not None
        else None
    )
    return bench.PipelineRun(
        application_id=application_id,
        repetition=repetition,
        fanout_wall_ms=fanout_ms,
        specialists=specialists,
        synthesis_wall_ms=synthesis_ms,
        synthesis_latency_ms=synthesis_ms,
        synthesis_usage=usage,
        synthesis_cost_usd=synthesis_cost,
        synthesis_model_id="openai.gpt-5.6-luna",
        synthesis_stop_reason="tool_use",
        repair_attempts=repair_attempts,
        degraded_domains=sorted(failed, key=DOMAINS.index),
        adjudication_validated=validated,
        adjudication_keys=21 if validated else None,
        overall_risk_decision="HIGH RISK" if validated else None,
        recommendation_decision="DECLINE" if validated else None,
        e2e_wall_ms=e2e_ms,
        synthesis_error=synthesis_error,
    )


def _pipeline_report(runs, repetitions=1, mock=False):
    return bench.build_pipeline_report(
        env=bench.run_environment("pipeline-live", "us-east-1", {"synthesis": "openai.gpt-5.6-luna"}),
        runs=runs,
        models={"synthesis": "openai.gpt-5.6-luna"},
        application_ids=sorted({r.application_id for r in runs}),
        repetitions=repetitions,
        mock=mock,
    )


# -- percentile guards on the new summarizer ---------------------------------


def test_summarize_values_applies_the_same_minimums_as_latencies():
    """A ratio and a dollar figure must be guarded like a latency."""
    small = bench.summarize_values([1.0, 2.0, 3.0], "speed-up", unit="x")
    assert small["n"] == 3
    assert small["p50"] is None and small["p95"] is None
    assert "p50" in small["suppressed"] and "p95" in small["suppressed"]
    assert small["unit"] == "x"
    # min/max/mean stay honest at any n.
    assert small["min"] == 1.0 and small["max"] == 3.0

    enough = bench.summarize_values([float(i) for i in range(1, 21)], "speed-up", unit="x")
    assert enough["p50"] is not None and enough["p95"] is not None
    assert enough["p99"] is None


def test_summarize_values_and_summarize_latencies_agree_on_the_same_sample():
    values = [float(i) for i in range(1, 101)]
    bare = bench.summarize_values(values)
    suffixed = bench.summarize_latencies(values)
    for name in ("p50", "p95", "p99", "min", "max"):
        assert bare[name] == pytest.approx(suffixed[f"{name}_ms"]), name


def test_pipeline_p95_is_refused_at_three_repetitions_per_application():
    """The exact sample size --repetitions 3 produces per application.

    14 x 3 clears the pooled p95 minimum and does NOT clear it per application.
    Both facts must show up in the report rather than being resolved silently.
    """
    runs = [
        _pipeline_run(f"APP-10{i:02d}", rep, e2e_ms=20_000.0 + 500 * (i + rep))
        for i in range(1, 15)
        for rep in (1, 2, 3)
    ]
    report = _pipeline_report(runs, repetitions=3)
    assert report["end_to_end"]["n"] == 42
    assert report["end_to_end"]["p50_ms"] is not None
    assert report["end_to_end"]["p95_ms"] is not None, "42 pooled samples supports a p95"
    assert report["end_to_end"]["p99_ms"] is None
    for app_id, data in report["per_application"].items():
        assert data["end_to_end"]["n"] == 3
        assert data["end_to_end"]["p50_ms"] is None, app_id
        assert data["end_to_end"]["p95_ms"] is None, app_id
        assert "p95" in data["end_to_end"]["suppressed"], app_id


def test_pipeline_report_caveats_name_the_refused_p95_when_n_is_too_small():
    report = _pipeline_report([_pipeline_run() for _ in range(4)], repetitions=4)
    assert report["end_to_end"]["p95_ms"] is None
    assert any("below the 20" in c and "p95" in c for c in report["caveats"])


def test_pipeline_percentiles_are_observed_samples_not_interpolated():
    runs = [_pipeline_run(repetition=i, e2e_ms=float(i) * 1000.0) for i in range(1, 101)]
    report = _pipeline_report(runs, repetitions=100)
    observed = {r.e2e_wall_ms for r in runs}
    for key in ("p50_ms", "p95_ms", "p99_ms"):
        assert report["end_to_end"][key] in observed, key


# -- the under-60s counter ---------------------------------------------------


def test_target_threshold_is_the_customers_sixty_seconds():
    assert bench.TARGET_E2E_SECONDS == 60.0


def test_under_target_counter_is_strict_at_the_boundary():
    """59.999s is under; exactly 60.000s and 60.001s are not.

    A counter that rounds is a counter that can report a 60.4s run as meeting a
    60s target.
    """
    runs = [
        _pipeline_run("APP-A", 1, e2e_ms=59_999.0),
        _pipeline_run("APP-B", 1, e2e_ms=60_000.0),
        _pipeline_run("APP-C", 1, e2e_ms=60_001.0),
    ]
    report = _pipeline_report(runs)
    assert report["target"]["runs_under_target"] == 1
    assert report["target"]["applications_all_reps_under_target"] == 1
    assert set(report["target"]["applications_never_under_target"]) == {"APP-B", "APP-C"}


def test_under_target_separates_every_rep_from_any_rep():
    """An application that straddles the line must not be counted as meeting it."""
    runs = [
        _pipeline_run("APP-FAST", 1, e2e_ms=30_000.0),
        _pipeline_run("APP-FAST", 2, e2e_ms=31_000.0),
        _pipeline_run("APP-STRADDLE", 1, e2e_ms=45_000.0),
        _pipeline_run("APP-STRADDLE", 2, e2e_ms=75_000.0),
        _pipeline_run("APP-SLOW", 1, e2e_ms=90_000.0),
        _pipeline_run("APP-SLOW", 2, e2e_ms=91_000.0),
    ]
    report = _pipeline_report(runs, repetitions=2)
    target = report["target"]
    assert target["n_applications"] == 3
    assert target["applications_all_reps_under_target"] == 1  # only APP-FAST
    assert target["applications_any_rep_under_target"] == 2  # + APP-STRADDLE
    assert target["applications_straddling_target"] == ["APP-STRADDLE"]
    assert target["applications_never_under_target"] == ["APP-SLOW"]
    assert target["runs_under_target"] == 3
    assert target["per_application_reps_under_target"]["APP-STRADDLE"] == {
        "n_reps": 2,
        "under": 1,
    }


def test_under_target_note_refuses_to_call_itself_an_sla():
    report = _pipeline_report([_pipeline_run()])
    note = report["target"]["note"]
    assert "not a percentile" in note and "not an SLA" in note


# -- degraded-run accounting -------------------------------------------------


def test_a_degraded_run_stays_in_latency_and_leaves_cost():
    """The whole point: a run that lost a specialist is reported, not averaged in.

    It really did take that long, so it belongs in the latency sample. It did not
    produce nine priced calls, so it must not contribute a per-application cost.
    """
    good = _pipeline_run("APP-1001", 1, e2e_ms=30_000.0)
    degraded = _pipeline_run(
        "APP-1001",
        2,
        e2e_ms=50_000.0,
        failed={"rings": "MaxTokensReachedException: output exceeded 4096"},
    )
    report = _pipeline_report([good, degraded], repetitions=2)

    assert report["end_to_end"]["n"] == 2, "a degraded run must remain in the latency sample"
    assert report["end_to_end"]["max_ms"] == pytest.approx(50_000.0)

    cost = report["cost_per_adjudication_usd"]
    assert cost["n_priced_runs"] == 1
    assert cost["n_unpriced_runs"] == 1
    # 8 x 0.01 + 0.015 from the complete run only.
    assert cost["min"] == pytest.approx(0.095)
    assert cost["max"] == pytest.approx(0.095)

    assert report["totals"]["degraded_runs"] == 1
    assert report["totals"]["complete_runs"] == 1
    assert report["failures"]["by_kind"] == {"max_tokens_truncation": 1}
    assert report["failures"]["by_run"][0]["failed_domains"] == ["rings"]
    assert any("REMAIN in the latency samples" in c for c in report["caveats"])


def test_degraded_run_cost_is_none_not_a_partial_sum():
    degraded = _pipeline_run(failed={"identity": "ThrottlingException: slow down"})
    assert degraded.cost_usd is None, "a 7-of-8 run must not report a total"
    assert degraded.complete is False
    assert degraded.fully_priced is False
    assert degraded.failed_domains == ["identity"]


def test_a_run_whose_synthesis_failed_is_degraded_and_unpriced():
    run = _pipeline_run(
        synthesis_error="SynthesisContractError: no valid adjudication",
        synthesis_cost=None,
        synthesis_ms=8_000.0,
        validated=False,
    )
    report = _pipeline_report([run])
    assert run.cost_usd is None
    assert report["totals"]["degraded_runs"] == 1
    assert report["totals"]["validated_adjudications"] == 0
    assert report["failures"]["by_kind"] == {"contract_failure": 1}
    assert report["cost_per_adjudication_usd"]["n_priced_runs"] == 0
    assert report["cost_per_adjudication_usd"]["note"] is not None


def test_specialist_latency_excludes_failed_calls_from_its_percentiles():
    """A call that failed fast must not make its dimension look fast."""
    fast_failure = _pipeline_run(
        specialist_walls={**{d: 20_000.0 for d in DOMAINS}, "rings": 200.0},
        failed={"rings": "ThrottlingException: rate exceeded"},
    )
    slow_success = _pipeline_run(
        repetition=2, specialist_walls={d: 20_000.0 for d in DOMAINS}
    )
    report = _pipeline_report([fast_failure, slow_success], repetitions=2)
    rings = report["per_specialist"]["rings"]
    assert rings["n_calls"] == 2
    assert rings["n_failed"] == 1
    assert rings["wall_clock_successful_only"]["n"] == 1
    assert rings["wall_clock_successful_only"]["min_ms"] == pytest.approx(20_000.0)
    assert rings["errors"] == ["ThrottlingException: rate exceeded"]


def test_a_specialist_that_never_succeeded_reports_no_latency_at_all():
    run = _pipeline_run(failed={d: "AccessDeniedException: no access" for d in DOMAINS})
    report = _pipeline_report([run])
    for domain in DOMAINS:
        block = report["per_specialist"][domain]["wall_clock_successful_only"]
        assert block["n"] == 0
        assert "no successful call" in block["note"]


@pytest.mark.parametrize(
    "error,expected",
    [
        ("ThrottlingException: Too many requests", "throttled"),
        ("TooManyRequestsException: slow down", "throttled"),
        ("MaxTokensReachedException: truncated", "max_tokens_truncation"),
        ("InternalServerException: try again", "server_5xx"),
        ("ServiceUnavailableException: nope", "server_5xx"),
        ("ReadTimeoutError: timed out", "timeout"),
        ("ValidationException: bad model id", "validation"),
        ("SynthesisContractError: 19 of 21 keys", "contract_failure"),
        ("MantleUnavailable: no bearer token", "mantle_unavailable"),
    ],
)
def test_failure_classification_names_the_conversation_each_error_starts(error, expected):
    """Throttling, truncation and a 5xx are three different customer conversations."""
    assert bench.classify_failure(error) == expected


def test_an_unrecognised_failure_keeps_its_own_name_rather_than_becoming_other():
    assert bench.classify_failure("SomeBrandNewException: boom") == "SomeBrandNewException"


# -- the parallel speed-up distribution --------------------------------------


def test_parallel_speedup_is_a_distribution_not_a_single_number():
    runs = [
        _pipeline_run(
            "APP-A", 1, fanout_ms=20_000.0, specialist_walls={d: 10_000.0 for d in DOMAINS}
        ),
        _pipeline_run(
            "APP-B", 1, fanout_ms=40_000.0, specialist_walls={d: 10_000.0 for d in DOMAINS}
        ),
    ]
    report = _pipeline_report(runs)
    speed = report["parallel_speedup"]
    assert speed["n"] == 2
    assert speed["min"] == pytest.approx(2.0)  # 80s / 40s
    assert speed["max"] == pytest.approx(4.0)  # 80s / 20s
    assert speed["p50"] is None, "2 samples cannot support a p50"
    assert len(speed["distribution"]) == 2
    assert {row["application_id"] for row in speed["distribution"]} == {"APP-A", "APP-B"}
    assert "spread is the finding" in speed["note"]


def test_sum_of_eight_is_labelled_a_counterfactual_in_the_caveats():
    report = _pipeline_report([_pipeline_run()])
    assert any(
        "SEQUENTIAL COUNTERFACTUAL" in c and "arrangement is hypothetical" in c
        for c in report["caveats"]
    )
    assert report["sum_of_eight_sequential_counterfactual"]["n"] == 1


def test_speedup_uses_measured_specialist_walls_and_the_measured_fanout_wall():
    run = _pipeline_run(
        fanout_ms=10_000.0, specialist_walls={d: 5_000.0 for d in DOMAINS}
    )
    assert run.sum_of_eight_ms == pytest.approx(40_000.0)
    assert run.parallel_speedup == pytest.approx(4.0)
    assert run.slowest_specialist_wall_ms == pytest.approx(5_000.0)


def test_a_zero_length_fanout_yields_no_speedup_rather_than_a_division_error():
    run = _pipeline_run(fanout_ms=0.0)
    assert run.parallel_speedup is None
    report = _pipeline_report([run])
    assert report["parallel_speedup"]["n"] == 0
    assert report["parallel_speedup"]["distribution"] == []


# -- cost accounting and its variance ---------------------------------------


def test_cost_variance_drivers_come_from_measured_token_counts():
    cheap = _pipeline_run("APP-CHEAP", 1, specialist_cost=0.005, synthesis_cost=0.01)
    dear = _pipeline_run("APP-DEAR", 1, specialist_cost=0.02, synthesis_cost=0.05)
    report = _pipeline_report([cheap, dear])
    drivers = report["cost_per_adjudication_usd"]["variance_drivers"]
    assert drivers["cheapest_run"]["application_id"] == "APP-CHEAP"
    assert drivers["most_expensive_run"]["application_id"] == "APP-DEAR"
    assert drivers["cheapest_run"]["specialist_input_tokens"] == 8 * 2000
    assert drivers["most_expensive_run"]["synthesis_input_tokens"] == 28_000
    assert drivers["spread_usd"] == pytest.approx((8 * 0.02 + 0.05) - (8 * 0.005 + 0.01))


def test_cost_variance_drivers_refuse_to_attribute_with_no_priced_run():
    report = _pipeline_report([_pipeline_run(synthesis_cost=None)])
    drivers = report["cost_per_adjudication_usd"]["variance_drivers"]
    assert "nothing to attribute" in drivers["note"]


def test_token_totals_distinguish_zero_from_unmeasured():
    """'0 cache reads' and 'no usage measured' are different statements."""
    measured = bench._token_totals(
        [{"inputTokens": 100, "outputTokens": 10, "cacheReadInputTokens": 0}]
    )
    assert measured["n_calls_with_usage"] == 1
    assert measured["totals"]["cacheReadInputTokens"] == 0
    assert measured["note"] is None

    unmeasured = bench._token_totals([None, None])
    assert unmeasured["n_calls_with_usage"] == 0
    assert unmeasured["n_calls_without_usage"] == 2
    assert unmeasured["totals"] is None
    assert unmeasured["mean_per_call"] is None
    assert "null" in unmeasured["note"]


def test_all_four_token_classes_are_carried_into_the_report():
    run = _pipeline_run()
    run.synthesis_usage = {
        "inputTokens": 1_000,
        "outputTokens": 800,
        "cacheReadInputTokens": 12_000,
        "cacheWriteInputTokens": 6_740,
        "totalTokens": 20_540,
    }
    report = _pipeline_report([run])
    totals = report["synthesis_detail"]["tokens"]["totals"]
    for key in (
        "inputTokens",
        "outputTokens",
        "cacheReadInputTokens",
        "cacheWriteInputTokens",
    ):
        assert key in totals, key
    assert totals["cacheWriteInputTokens"] == 6_740


# -- mock pipeline mode labels itself ---------------------------------------


def test_mock_pipeline_report_shouts_that_no_model_was_called():
    report = _pipeline_report([_pipeline_run(synthesis_cost=None)], mock=True)
    assert "MOCK RUN. NO MODEL WAS CALLED" in report["caveats"][0]
    assert report["workload"]["mock"] is True
    rendered = bench.render_pipeline_human(report)
    assert "NO MODEL WAS CALLED" in rendered.split("Pooled latency")[0]


def test_a_real_mock_pipeline_run_produces_a_serializable_report():
    """End-to-end through run_pipeline itself, offline, over two applications."""
    import asyncio

    os.environ["MOCK_MODE"] = "1"
    report = asyncio.run(
        bench.run_pipeline(
            ["APP-1001", "APP-1004"], 2, mock=True, region="us-east-1"
        )
    )
    assert report["schema"] == "pointpredictive.agentcore.pipeline-bench/1"
    assert report["totals"]["runs"] == 4
    assert report["totals"]["expected_runs"] == 4
    assert report["totals"]["validated_adjudications"] == 4
    # No model was called, so every cost must be null rather than 0.0.
    assert report["cost_per_adjudication_usd"]["n_priced_runs"] == 0
    assert report["totals"]["total_measured_cost_usd"] is None
    for domain in DOMAINS:
        assert report["per_specialist"][domain]["cost_usd"]["total"] is None, domain
    round_tripped = json.loads(json.dumps(report))
    assert round_tripped["run"]["mode"] == "pipeline-mock"
    assert round_tripped["workload"]["repetitions_per_application"] == 2


def test_pipeline_report_carries_the_provenance_a_committed_benchmark_needs():
    report = _pipeline_report([_pipeline_run()])
    run = report["run"]
    for key in ("git_sha", "git_branch", "timestamp_utc", "aws_region", "model_ids", "packages"):
        assert key in run, key
    assert run["target_e2e_seconds"] == bench.TARGET_E2E_SECONDS
    assert isinstance(report["caveats"], list) and report["caveats"]
    assert report["raw_runs"], "every individual run must survive so aggregates can be recomputed"
    assert report["workload"]["execution_order"]


def test_pipeline_report_records_expected_versus_actual_run_count():
    """A run that died half way must be visible as a short sample, not a full one."""
    runs = [_pipeline_run("APP-1001", 1), _pipeline_run("APP-1002", 1)]
    report = bench.build_pipeline_report(
        env=bench.run_environment("pipeline-live", "us-east-1", {}),
        runs=runs,
        models={},
        application_ids=["APP-1001", "APP-1002", "APP-1003"],
        repetitions=3,
        mock=False,
    )
    assert report["totals"]["runs"] == 2
    assert report["totals"]["expected_runs"] == 9


# -- the plan is printed before any spend -----------------------------------


def test_pipeline_plan_states_the_call_count_and_labels_the_estimate_a_prior():
    plan = bench.pipeline_plan(
        [f"APP-10{i:02d}" for i in range(1, 15)],
        3,
        mock=False,
        region="us-east-1",
        models={"synthesis": "openai.gpt-5.6-luna"},
        per_application_usd=0.245,
    )
    assert "42" in plan  # adjudications
    assert "378" in plan  # model calls
    assert "PRIOR" in plan
    assert "p95 needs 20" in plan


def test_pipeline_plan_says_zero_spend_in_mock_mode():
    plan = bench.pipeline_plan(
        ["APP-1001"], 1, mock=True, region="us-east-1", models={}, per_application_usd=None
    )
    assert "$0.00" in plan
    assert "no model is called" in plan


def test_pipeline_live_is_gated_on_the_same_env_var_as_live(monkeypatch, capsys):
    monkeypatch.delenv(bench.LIVE_ENV_GATE, raising=False)
    assert bench.main(["--pipeline", "--repetitions", "1"]) == 2
    err = capsys.readouterr().err
    assert "refusing to run the live pipeline" in err
    assert bench.LIVE_ENV_GATE in err


def test_pipeline_mock_needs_no_gate(monkeypatch, capsys):
    monkeypatch.delenv(bench.LIVE_ENV_GATE, raising=False)
    monkeypatch.setenv("MOCK_MODE", "1")
    assert bench.main(["--pipeline", "--mock", "--repetitions", "1",
                       "--application", "APP-1001"]) == 0
    out = capsys.readouterr().out
    assert "MOCK RUN" in out


def test_pipeline_default_repetitions_clears_the_pooled_p95_minimum():
    """3 x 14 = 42 >= 20, so the pooled p95 this mode publishes is supportable."""
    from fixtures.loader import list_application_ids

    pooled = bench.PIPELINE_DEFAULT_REPETITIONS * len(list_application_ids())
    assert pooled >= bench.PERCENTILE_MIN_SAMPLES["p95"]
    # ...and honestly does NOT reach p99.
    assert pooled < bench.PERCENTILE_MIN_SAMPLES["p99"]


# -- the markdown summary contains only numbers the run produced ------------


def test_markdown_summary_refuses_a_suppressed_percentile_out_loud():
    report = _pipeline_report([_pipeline_run() for _ in range(3)], repetitions=3)
    markdown = bench.render_pipeline_markdown(report)
    assert "**refused**" in markdown, "a suppressed p95 must be visible, not omitted"
    assert "p95 is refused, not omitted" in markdown
    assert "n=3 adjudications" in markdown


def test_markdown_summary_reports_degraded_runs_rather_than_hiding_them():
    runs = [
        _pipeline_run("APP-1001", 1),
        _pipeline_run("APP-1002", 1, failed={"rings": "ThrottlingException: rate exceeded"}),
    ]
    markdown = bench.render_pipeline_markdown(_pipeline_report(runs))
    assert "throttled x1" in markdown
    assert "excluded from the cost distribution" in markdown


def test_markdown_summary_states_what_was_not_measured():
    markdown = bench.render_pipeline_markdown(_pipeline_report([_pipeline_run()]))
    assert "Not measured" in markdown
    assert "AgentCore runtime overhead" in markdown
    assert "throughput" in markdown


def test_markdown_summary_carries_the_git_sha_and_region():
    report = _pipeline_report([_pipeline_run()])
    markdown = bench.render_pipeline_markdown(report)
    assert (report["run"]["git_sha"] or "")[:12] in markdown
    assert "us-east-1" in markdown


# ---------------------------------------------------------------------------
# 8. The committed benchmark report in evals/results/ must stay re-readable
# ---------------------------------------------------------------------------

RESULTS_DIR = REPO_ROOT / "evals" / "results"


PIPELINE_BENCH_SCHEMA = "pointpredictive.agentcore.pipeline-bench/1"


def _committed_reports() -> list[Path]:
    """Every committed PIPELINE-BENCH report in evals/results/.

    Selected by its ``schema`` field, not by being a .json file in the directory. The
    directory also holds derived analyses of these runs (e.g. the tail analysis, schema
    ``pointpredictive.agentcore.tail-analysis/1``), which have their own shape and their own
    guards; asserting the pipeline-bench contract against them fails on the contract's own
    keys and says nothing true about either artifact.

    A file that is JSON but carries no recognisable schema is still collected on purpose, so
    that an unschema'd or corrupt report fails loudly here instead of being skipped.
    """
    if not RESULTS_DIR.is_dir():
        return []
    selected = []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8")).get("schema")
        except (json.JSONDecodeError, OSError):
            selected.append(path)
            continue
        if schema is None or schema == PIPELINE_BENCH_SCHEMA:
            selected.append(path)
    return selected


def test_a_pipeline_benchmark_report_is_committed():
    """A benchmark nobody can re-read is not a benchmark."""
    assert _committed_reports(), (
        f"no committed report in {RESULTS_DIR}; run "
        "`python -m evals.bench --pipeline --json evals/results/<name>.json`"
    )


def test_report_selection_skips_only_recognised_non_bench_schemas():
    """The schema filter must not become a way for a real report to escape its guards.

    Every JSON in evals/results/ is either collected above or carries a schema that is
    explicitly known NOT to be a pipeline-bench report. A new artifact with an unlisted
    schema fails here, which forces a decision instead of a silent skip.
    """
    known_non_bench = {
        "pointpredictive.agentcore.tail-analysis/1",
        # The tier-quality study and its two sidecars. Not pipeline-bench runs: they
        # measure one specialist at a time with the model varied, so they have no
        # end-to-end latency, no 21-key adjudication and no fan-out to speed up.
        # Their own guards live in tests/test_tier_quality.py.
        "pointpredictive.agentcore.tier-quality/1",
        "pointpredictive.agentcore.tier-quality-analyses/1",
        "pointpredictive.agentcore.tier-quality-verdicts/1",
        # The Claude-set vs GPT-5.6-set study and its two sidecars. Same shape as the
        # tier-quality study and not pipeline-bench runs for the same reasons: one
        # specialist at a time with the model varied, so no end-to-end latency, no
        # 21-key adjudication and no fan-out. It differs from tier-quality only in
        # crossing model FAMILIES (Claude Converse vs GPT-5.6 on bedrock-mantle) and in
        # scoring with two judges biased in opposite directions rather than one.
        "pointpredictive.agentcore.set-comparison/1",
        "pointpredictive.agentcore.set-comparison-analyses/1",
        "pointpredictive.agentcore.set-comparison-verdicts/1",
        # One invocation of the DEPLOYED AgentCore runtime, recorded to prove the
        # deployed path produces a valid 21-key adjudication from live Bedrock calls.
        # Not a pipeline-bench run: n=1, so it has no distribution and cannot acquire
        # one. It is the closest thing here to a bench report -- it does have
        # end-to-end latency, a 21-key adjudication and a fan-out speedup -- which is
        # exactly why it is registered explicitly rather than left to the filter. The
        # bench guards below assert `raw_runs` and percentile suppression structures
        # that only make sense for a sampled run; satisfying them for a single
        # invocation would mean inventing the shape of a distribution that was never
        # measured. The file publishes no percentile and says so in its own caveats.
        "pointpredictive.agentcore.deployed-runtime-smoke/1",
    }
    collected = set(_committed_reports())
    for path in sorted(RESULTS_DIR.glob("*.json")):
        if path in collected:
            continue
        schema = json.loads(path.read_text(encoding="utf-8")).get("schema")
        assert schema in known_non_bench, (
            f"{path.name} carries schema {schema!r}, which is neither the pipeline-bench "
            "schema nor a known derived analysis. Either it is a benchmark report and must "
            "satisfy the guards below, or add its schema to known_non_bench with a reason."
        )


@pytest.mark.parametrize("path", _committed_reports(), ids=lambda p: p.name)
def test_committed_report_carries_provenance_sample_counts_and_caveats(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["schema"] == PIPELINE_BENCH_SCHEMA
    run = report["run"]
    assert run["git_sha"], "a committed benchmark must name the commit it measured"
    assert run["timestamp_utc"]
    assert run["aws_region"]
    assert run["model_ids"], "model ids must be recorded"
    assert isinstance(report["caveats"], list) and report["caveats"]
    assert report["end_to_end"]["n"] == report["totals"]["runs"]
    assert report["raw_runs"], "raw runs must survive so every aggregate is recomputable"
    assert len(report["raw_runs"]) == report["totals"]["runs"]


@pytest.mark.parametrize("path", _committed_reports(), ids=lambda p: p.name)
def test_committed_report_percentiles_obey_the_sample_minimums(path):
    """The published numbers must satisfy the same guard as a fresh run."""
    report = json.loads(path.read_text(encoding="utf-8"))
    for key in ("end_to_end", "fan_out", "synthesis"):
        block = report[key]
        n = block.get("n", 0)
        for name in ("p50", "p95", "p99"):
            value = block.get(f"{name}_ms")
            if n < bench.PERCENTILE_MIN_SAMPLES[name]:
                assert value is None, f"{path.name}: {key}.{name} published at n={n}"
                assert name in block["suppressed"], f"{path.name}: {key}.{name} has no reason"
            else:
                assert value is not None, f"{path.name}: {key}.{name} suppressed at n={n}"


@pytest.mark.parametrize("path", _committed_reports(), ids=lambda p: p.name)
def test_committed_report_cost_only_counts_fully_priced_runs(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    cost = report["cost_per_adjudication_usd"]
    priced = [r for r in report["raw_runs"] if r["cost_usd"] is not None]
    assert cost["n_priced_runs"] == len(priced)
    assert cost["n_unpriced_runs"] == report["totals"]["runs"] - len(priced)
    for row in report["raw_runs"]:
        if row["failed_domains"] or row["synthesis_error"]:
            assert row["cost_usd"] is None, (
                f"{path.name}: {row['application_id']} rep {row['repetition']} is degraded "
                "but carries a cost"
            )


@pytest.mark.parametrize("path", _committed_reports(), ids=lambda p: p.name)
def test_committed_report_under_target_count_matches_its_own_raw_runs(path):
    """Recompute the headline claim from the raw rows in the same file."""
    report = json.loads(path.read_text(encoding="utf-8"))
    recomputed = sum(
        1
        for row in report["raw_runs"]
        if row["e2e_wall_ms"] / 1000.0 < report["target"]["target_seconds"]
    )
    assert report["target"]["runs_under_target"] == recomputed


@pytest.mark.parametrize("path", _committed_reports(), ids=lambda p: p.name)
def test_committed_report_covers_every_fixture_application(path):
    """A benchmark over a subset must not be read as a benchmark over the set."""
    from fixtures.loader import list_application_ids

    report = json.loads(path.read_text(encoding="utf-8"))
    if report["run"]["mode"] != "pipeline-live":
        pytest.skip("only the live report is the published benchmark")
    assert set(report["workload"]["application_ids"]) == set(list_application_ids())


# ---------------------------------------------------------------------------
# 9. Verdict stability, and re-aggregating a committed report without re-spending
# ---------------------------------------------------------------------------


def test_verdict_stability_counts_matching_runs_not_distinct_values():
    """'2 of 3 runs matched the pin' and 'no run matched' are different findings."""
    runs = [_pipeline_run("APP-1005", rep) for rep in (1, 2, 3)]
    runs[0].overall_risk_decision = "HIGH RISK"
    runs[1].overall_risk_decision = "MEDIUM RISK"
    runs[2].overall_risk_decision = "MEDIUM RISK"
    report = _pipeline_report(runs, repetitions=3)
    data = report["verdict_stability"]["per_application"]["APP-1005"]
    assert data["expected_overall_risk_decision"] == "MEDIUM RISK"
    assert data["observed_overall_risk_decisions"] == ["HIGH RISK", "MEDIUM RISK"]
    assert data["n_runs_matching_expectation"] == 2
    assert data["stable"] is False
    assert report["verdict_stability"]["unstable_applications"] == ["APP-1005"]
    assert "APP-1005" not in report["verdict_stability"][
        "applications_never_matching_pinned_expectation"
    ]


def test_verdict_stability_flags_an_application_that_never_matched_its_pin():
    runs = [_pipeline_run("APP-1003", rep) for rep in (1, 2, 3)]
    for run in runs:
        run.overall_risk_decision = "LOW RISK"
    report = _pipeline_report(runs, repetitions=3)
    stability = report["verdict_stability"]
    data = stability["per_application"]["APP-1003"]
    assert data["n_runs_matching_expectation"] == 0
    assert data["stable"] is True, "stable and wrong are independent findings"
    assert "APP-1003" in stability["applications_disagreeing_with_pinned_expectation"]
    assert "APP-1003" in stability["applications_never_matching_pinned_expectation"]


def test_verdict_stability_refuses_to_adjudicate_the_disagreement():
    report = _pipeline_report([_pipeline_run()])
    note = report["verdict_stability"]["note"]
    assert "reported, not adjudicated" in note
    assert "calibration judge" in note


# -- re-aggregation is lossless and spends nothing ---------------------------


def test_a_pipeline_run_round_trips_through_json_without_losing_a_measurement():
    original = _pipeline_run(
        "APP-1004",
        2,
        e2e_ms=39_231.0,
        fanout_ms=33_850.0,
        synthesis_ms=5_371.0,
        specialist_walls={d: 8_000.0 + 500 * i for i, d in enumerate(DOMAINS)},
    )
    rebuilt = bench.pipeline_run_from_json(original.to_json())
    assert rebuilt.application_id == original.application_id
    assert rebuilt.repetition == original.repetition
    assert rebuilt.e2e_wall_ms == pytest.approx(original.e2e_wall_ms)
    assert rebuilt.fanout_wall_ms == pytest.approx(original.fanout_wall_ms)
    assert rebuilt.sum_of_eight_ms == pytest.approx(original.sum_of_eight_ms)
    assert rebuilt.parallel_speedup == pytest.approx(original.parallel_speedup)
    assert rebuilt.cost_usd == pytest.approx(original.cost_usd)
    assert rebuilt.slowest_specialist == original.slowest_specialist
    assert [s.domain for s in rebuilt.specialists] == [s.domain for s in original.specialists]
    assert [s.usage for s in rebuilt.specialists] == [s.usage for s in original.specialists]


def test_a_degraded_run_round_trips_still_degraded_and_still_unpriced():
    original = _pipeline_run(failed={"rings": "InternalServerException: 500"})
    rebuilt = bench.pipeline_run_from_json(original.to_json())
    assert rebuilt.failed_domains == ["rings"]
    assert rebuilt.cost_usd is None
    assert rebuilt.complete is False
    assert rebuilt.failure_kinds == ["rings: server_5xx"]


def test_reaggregation_reproduces_every_published_aggregate():
    """Recomputing must not move a number, or the published one was not derivable."""
    runs = [
        _pipeline_run(f"APP-10{i:02d}", rep, e2e_ms=20_000.0 + 700 * (i + rep))
        for i in range(1, 15)
        for rep in (1, 2, 3)
    ]
    original = _pipeline_report(runs, repetitions=3)
    rebuilt = bench.reaggregate_pipeline_report(json.loads(json.dumps(original)))
    for key in ("end_to_end", "fan_out", "synthesis", "sum_of_eight_sequential_counterfactual"):
        for stat in ("n", "min_ms", "p50_ms", "p95_ms", "max_ms", "mean_ms"):
            assert rebuilt[key][stat] == pytest.approx(original[key][stat]), f"{key}.{stat}"
    assert rebuilt["target"]["runs_under_target"] == original["target"]["runs_under_target"]
    assert rebuilt["cost_per_adjudication_usd"]["p50"] == pytest.approx(
        original["cost_per_adjudication_usd"]["p50"]
    )
    assert rebuilt["totals"]["runs"] == original["totals"]["runs"]


def test_reaggregation_keeps_the_original_provenance_and_says_it_recomputed():
    original = _pipeline_report([_pipeline_run()])
    original["run"]["git_sha"] = "deadbeefcafe0000"
    original["run"]["timestamp_utc"] = "2020-01-01T00:00:00+00:00"
    rebuilt = bench.reaggregate_pipeline_report(json.loads(json.dumps(original)))
    # The measurement's provenance must NOT be restamped with today's values.
    assert rebuilt["run"]["git_sha"] == "deadbeefcafe0000"
    assert rebuilt["run"]["timestamp_utc"] == "2020-01-01T00:00:00+00:00"
    assert rebuilt["run"]["reaggregated_at_utc"] != original["run"]["timestamp_utc"]
    assert any("recomputed from raw_runs" in c for c in rebuilt["caveats"])
    assert any("No model was called" in c for c in rebuilt["caveats"])


def test_reaggregate_cli_writes_in_place_without_a_live_gate(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(bench.LIVE_ENV_GATE, raising=False)
    source = tmp_path / "report.json"
    source.write_text(json.dumps(_pipeline_report([_pipeline_run()])), encoding="utf-8")
    assert bench.main(["--reaggregate", str(source)]) == 0
    out = capsys.readouterr().out
    assert "no model was called" in out
    assert json.loads(source.read_text(encoding="utf-8"))["run"]["reaggregated_at_utc"]


# -- the committed markdown summary must match the committed JSON ------------


def test_the_committed_markdown_summary_matches_its_json_report():
    """A README-pasteable summary that has drifted from its report is worse than none."""
    for path in _committed_reports():
        markdown_path = path.with_suffix(".md")
        assert markdown_path.is_file(), f"{path.name} has no markdown summary beside it"
        report = json.loads(path.read_text(encoding="utf-8"))
        expected = bench.render_pipeline_markdown(report)
        assert markdown_path.read_text(encoding="utf-8").strip() == expected.strip(), (
            f"{markdown_path.name} has drifted from {path.name}; regenerate with "
            f"`python -m evals.bench --reaggregate {path} --markdown {markdown_path}`"
        )


@pytest.mark.parametrize("path", _committed_reports(), ids=lambda p: p.name)
def test_committed_report_speedup_is_recomputable_from_its_own_raw_runs(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    for row in report["raw_runs"]:
        walls = sum(s["wall_ms"] for s in row["specialists"])
        assert row["sum_of_eight_ms"] == pytest.approx(walls, rel=1e-6), (
            f"{path.name}: {row['application_id']} rep {row['repetition']} "
            "sum-of-eight does not equal the sum of its own specialist walls"
        )
        if row["parallel_speedup"] is not None:
            assert row["parallel_speedup"] == pytest.approx(
                walls / row["fanout_wall_ms"], rel=1e-3
            )


@pytest.mark.parametrize("path", _committed_reports(), ids=lambda p: p.name)
def test_committed_report_records_verdict_stability(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    stability = report["verdict_stability"]
    assert stability["n_applications"] == len(report["per_application"])
    for app_id, data in stability["per_application"].items():
        assert data["n_runs"] >= 1, app_id
        if data["n_runs_matching_expectation"] is not None:
            assert 0 <= data["n_runs_matching_expectation"] <= data["n_runs"], app_id
