"""
OpenAI GPT-5.6 on Amazon Bedrock, via the ``bedrock-mantle`` endpoint.

WHY THIS MODULE EXISTS

The customer asked about GPT models, and the master synthesizer is the measured
bottleneck of an adjudication: on Claude Sonnet 5 it took **55.2s of a 101.1s
end-to-end run** (28,567 input tokens -- the eight specialist narratives -- for
6,018 output tokens). Swapping only that one call to GPT-5.6 Luna, measured on the
same fixture and the same eight analyses:

    Claude Sonnet 5   55.2s   $0.11731   21 keys, HIGH RISK / DECLINE
    GPT-5.6 Luna       4.4s   $0.01314   21 keys, HIGH RISK / DECLINE

i.e. ~9.5x faster and ~89% cheaper on the synthesis call, with an identical verdict
and a valid 21-key object. That single change takes a measured end-to-end from
101.1s to ~51.7s, which is what makes the customer's sub-minute target reachable at
all. The specialists stay on Claude: they are the fraud analysis, and nothing here
has measured their accuracy on another family.

WHY IT IS NOT A STRANDS MODEL PROVIDER

GPT-5.x models are NOT served on Bedrock Converse or InvokeModel. They are only
available on the ``bedrock-mantle`` endpoint through the OpenAI Responses API:

    https://bedrock-mantle.{region}.api.aws/openai/v1

So ``strands.models.BedrockModel`` cannot reach them -- ``converse`` rejects the id
with ``ValidationException: The provided model identifier is invalid``. This module
talks to the endpoint with the OpenAI SDK and returns the same
``(text, usage, latency)`` shape ``agents.synthesize`` already expects, so the
synthesizer does not care which family answered.

GOTCHAS THAT COST TIME (all verified live)

* The hostname is ``bedrock-mantle.{region}.api.aws`` -- **not** ``.amazonaws.com``.
  The amazonaws.com form does not resolve at all (NXDOMAIN), which looks exactly
  like a permissions problem and is not one.
* **The path is ``/openai/v1``, not ``/v1``, and the two are mutually exclusive.**
  Measured 2026-07-28 against us-east-1, all four combinations:

      /v1         + openai.gpt-oss-120b   -> 200
      /v1         + openai.gpt-5.6-luna   -> 400 validation_error
                     "The model 'openai.gpt-5.6-luna' does not support the
                      '/v1/responses' API"
      /openai/v1  + openai.gpt-oss-120b   -> 400 validation_error
      /openai/v1  + openai.gpt-5.6-luna   -> 200

  So one path serves the open-weight ``gpt-oss`` family and the other serves the
  frontier ``gpt-5.6`` family; neither serves both. The Strands documentation for
  its ``OpenAIResponsesModel`` provider shows ``/v1`` with ``openai.gpt-oss-120b``,
  which is correct for that pair and **wrong for GPT-5.6** -- following it verbatim
  produces a 400 that reads like the model is unavailable rather than like the path
  is wrong.

* Auth is a bearer token from ``aws_bedrock_token_generator.provide_token``, not
  SigV4 request signing. Tokens are short-lived, so one is minted per call rather
  than cached across a long-running runtime.
* IAM needs ``bedrock-mantle:CreateInference`` (AWS managed policy
  ``AmazonBedrockMantleInferenceAccess``). It is a different action prefix from
  ``bedrock:`` and from ``bedrock-agentcore:``.
* These are reasoning models: ``temperature`` / ``top_p`` / ``top_k`` are ignored.
  Use ``reasoning={"effort": ...}``. Measured on our synthesis prompt, ``low`` and
  ``medium`` both returned a valid 21-key object; ``low`` is the default here
  because the master prompt is an explicit instruction set, not a puzzle.
* There is no ``global.*`` or ``us.*`` prefix and no cross-region inference: the id
  is bare (``openai.gpt-5.6-luna``) and the call is in-region only.
* Regional availability differs per variant. Luna and Terra: us-east-1, us-east-2,
  us-west-2. Sol: us-east-1, us-east-2 only.
* Prompt caching is explicit for 5.6 and would not help here anyway -- the eight
  analyses sit inside the prompt, so the prefix differs on every application.
* Token accounting differs from Converse: Responses ``usage.input_tokens`` is the
  TOTAL prompt size and already includes cached tokens, whereas Bedrock Converse
  reports only the non-cached count. ``_usage`` below normalises to the Converse
  convention so ``agents.pricing`` stays correct for both families.

WHY NOT THE STRANDS PROVIDER

Strands ships ``strands.models.openai_responses.OpenAIResponsesModel``, which
documents Bedrock Mantle support and would replace most of this module. It is NOT
usable here yet, for two verified reasons:

* it does not exist in the pinned ``strands-agents==1.26.0`` (``ImportError: No
  module named 'strands.models.openai_responses'``); it arrives in a later release
  (1.50.2 is current), and this repo's 1000+ tests are written against 1.26.0's API
  surface, which changed materially in between (1.35.0 alone adds ``plugins`` and
  ``concurrent_invocation_mode`` to ``Agent`` and ``service_tier`` to
  ``BedrockConfig``);
* it requires ``openai>=2.0.0`` and the installed SDK is 1.107.3.

So migrating is a deliberate dependency bump, not a drop-in, and it should not
happen days before a customer milestone. When it does happen, this module collapses
to a thin factory returning ``OpenAIResponsesModel(model_id=..., client_args={
"api_key": provide_token(region), "base_url": f".../openai/v1"})`` -- note the path
above -- and the cost/region tables here stay, because Strands prices nothing.

REGION IS A MEASURED CHOICE, NOT A DEPLOYMENT ACCIDENT

``resolve_mantle_region`` deliberately does **not** read ``AWS_REGION``. The region
that answers a mantle call fastest is a property of the workload, and it was measured:
:data:`MEASURED_SYNTHESIS_LATENCY` records every variant x region on the REAL
31,343-character synthesis prompt (the master prompt with all eight specialist
narratives substituted in), 3 trials per cell, effort=low, all 24 calls returning a
valid 21-key object and all 24 agreeing on HIGH RISK / DECLINE.

That table is a measurement of **our** prompt, not a vendor benchmark, and the
distinction is the whole point. A short-prompt probe of the same cells ranks the regions
differently (see :data:`THIRD_PARTY_SHORT_PROMPT_PROBE`): with a ~32-token reply almost
all the wall clock is time-to-first-token, so the lowest-TTFT region wins, while our
reply is ~800-1000 tokens and 83% of the wall clock is generation. Ranking one and
deploying the other is the trap this table exists to prevent.

Note the precise form of the divergence, because it is easy to overstate: on OUR data,
ordering the cells by TTFT happens to give the same ranking as ordering them by total, so
that is not the distinction. Ordering by tokens/s is -- it puts Luna's us-west-2 first
(180.0 vs 176.0 tok/s) despite its worse total (5.02s vs 4.61s), which is exactly where
the short-prompt probe puts it. The two datasets also disagree about which region has
Luna's lowest TTFT (theirs: us-west-2 at 531ms; ours: us-east-1 at 0.79s).

Provenance: measured 2026-07-28 from a streaming probe whose raw output is
``/tmp/bench/matrix.json`` (scratch, uncommitted). The numbers below are transcribed
from that file, rounded to 0.01s, and the probe itself is reimplemented as
:func:`measure_matrix` so the table can be re-derived instead of trusted:

    AGENTCORE_MANTLE_ALLOW_MEASURE=1 python3 -m agents.mantle --measure

``python3 -m agents.mantle`` with no flags prints the recorded table and spends nothing.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

log = logging.getLogger("pp.mantle")

#: Published in-region on-demand rates, $ per 1M tokens: (input, cache_read, output).
#: There is no global/geo variant to discount against, unlike the Claude ids.
MANTLE_RATES_PER_MTOK: Final[dict[str, tuple[float, float, float]]] = {
    "openai.gpt-5.6-sol": (5.50, 0.55, 33.00),
    "openai.gpt-5.6-terra": (2.75, 0.28, 16.50),
    "openai.gpt-5.6-luna": (1.10, 0.11, 6.60),
    "openai.gpt-5.5": (5.50, 0.55, 33.00),
    "openai.gpt-5.4": (2.75, 0.275, 16.50),
}

#: Where each variant is actually callable. Sol is absent from us-west-2.
MANTLE_REGIONS: Final[dict[str, tuple[str, ...]]] = {
    "openai.gpt-5.6-sol": ("us-east-1", "us-east-2"),
    "openai.gpt-5.6-terra": ("us-east-1", "us-east-2", "us-west-2"),
    "openai.gpt-5.6-luna": ("us-east-1", "us-east-2", "us-west-2"),
    "openai.gpt-5.5": ("us-east-1", "us-east-2"),
    "openai.gpt-5.4": ("us-east-1", "us-east-2", "us-west-2", "us-gov-west-1"),
}

#: Allowed reasoning-effort values on the Responses API.
MANTLE_EFFORTS: Final[tuple[str, ...]] = ("none", "low", "medium", "high", "xhigh", "max")

#: Base-URL template for the frontier GPT-5.x family. The ``/openai/v1`` path is
#: load-bearing: ``/v1`` on the same host serves only the open-weight ``gpt-oss``
#: models and returns 400 validation_error for any ``gpt-5.6`` id (measured
#: 2026-07-28, all four combinations -- see the module docstring). Strands' own
#: Mantle documentation shows ``/v1``, which is correct for its ``gpt-oss`` example
#: and wrong for these models.
MANTLE_BASE_URL_TEMPLATE: Final[str] = "https://bedrock-mantle.{region}.api.aws/openai/v1"

#: The path that serves the open-weight family instead. Recorded so the distinction
#: is discoverable rather than folklore; nothing in this module calls it, because
#: gpt-oss models are also on Bedrock Converse and go through Strands normally.
MANTLE_OPEN_WEIGHT_BASE_URL_TEMPLATE: Final[str] = "https://bedrock-mantle.{region}.api.aws/v1"


def base_url_for(region: str) -> str:
    """The Responses-API base URL for GPT-5.x in ``region``."""
    return MANTLE_BASE_URL_TEMPLATE.format(region=region)

DEFAULT_EFFORT: Final[str] = "low"


class MantleUnavailable(RuntimeError):
    """The mantle endpoint could not be used (SDK missing, no credentials, no access)."""


def is_mantle_model(model_id: str) -> bool:
    """True for a model that must go through bedrock-mantle rather than Converse."""
    return model_id.startswith("openai.gpt-5")


# ---------------------------------------------------------------------------
# The measured latency matrix
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LatencySample:
    """One measured model x region cell.

    Every field is a number that was actually observed. There is no nominal, modelled
    or interpolated cell in :data:`MEASURED_SYNTHESIS_LATENCY`: a cell that was not
    measured is absent, and :func:`measured_region_preference` treats absence as
    "unknown", never as "slow".

    ``n`` and ``measured_on`` are mandatory precisely so a stale or single-sample cell
    cannot masquerade as a benchmark. ``prompt_chars`` is mandatory because the ranking
    these numbers imply is only valid for a prompt of roughly this size.

    ALL FOUR p50s ARE INDEPENDENT MEDIANS, taken column-wise across the trials. So
    ``tps_p50`` is the median of the per-trial throughputs and NOT
    ``output_tokens_p50 / total_p50_s`` -- those agree exactly only when the same trial
    is the median in every column, which is true for 3 of the 8 recorded cells and off by
    up to 10.5% in the others. They are kept as measured rather than recomputed for
    consistency, because a recomputed number is not a measured one.
    """

    total_p50_s: float
    ttft_p50_s: float
    tps_p50: float
    output_tokens_p50: int
    n: int
    measured_on: _dt.date
    prompt_chars: int
    effort: str
    verdict: str


#: Size of the prompt these measurements were taken on: the customer's verbatim master
#: prompt with all eight specialist narratives substituted in, for APP-1004. The
#: specialist analyses are the bulk of it, so any real synthesis prompt is this order of
#: magnitude -- and a 200-character toy prompt is NOT, which is why the ranking below
#: must not be quoted for anything else.
MEASURED_PROMPT_CHARS: Final[int] = 31_343

#: Date the matrix was taken. :func:`matrix_age_days` reports how stale it is.
MEASURED_ON: Final[_dt.date] = _dt.date(2026, 7, 28)

#: How many trials per cell. Below :data:`MIN_TRIALS_FOR_A_RANKING` a p50 is not
#: reported as a ranking input.
MEASURED_TRIALS: Final[int] = 3

#: A p50 from fewer than this many trials is a data point, not a percentile. Three is
#: already thin -- it is stated everywhere the table surfaces rather than hidden.
MIN_TRIALS_FOR_A_RANKING: Final[int] = 3

#: MEASURED, 2026-07-28, raw output in /tmp/bench/matrix.json (scratch, uncommitted).
#: Streaming Responses calls on the REAL 31,343-char synthesis prompt, effort=low,
#: max_output_tokens=8192, 3 trials per cell, 24 calls total. Every one of the 24
#: returned a valid 21-key Adjudication and every one adjudicated HIGH RISK / DECLINE,
#: so the latency spread below is NOT bought by a shorter or worse answer.
#:
#: THIS IS A MEASUREMENT OF OUR WORKLOAD, NOT A VENDOR BENCHMARK. Read
#: :data:`THIRD_PARTY_SHORT_PROMPT_PROBE` before quoting any of it: on a ~32-token reply
#: the region ranking for Luna inverts, because TTFT dominates a short reply and
#: throughput dominates ours.
MEASURED_SYNTHESIS_LATENCY: Final[dict[str, dict[str, LatencySample]]] = {
    "openai.gpt-5.6-luna": {
        "us-east-1": LatencySample(4.61, 0.79, 176.0, 812, 3, MEASURED_ON, 31_343, "low", "HIGH RISK/DECLINE"),
        "us-west-2": LatencySample(5.02, 1.19, 180.0, 903, 3, MEASURED_ON, 31_343, "low", "HIGH RISK/DECLINE"),
        "us-east-2": LatencySample(6.79, 1.63, 115.8, 878, 3, MEASURED_ON, 31_343, "low", "HIGH RISK/DECLINE"),
    },
    "openai.gpt-5.6-terra": {
        "us-east-2": LatencySample(6.29, 0.63, 126.4, 825, 3, MEASURED_ON, 31_343, "low", "HIGH RISK/DECLINE"),
        "us-east-1": LatencySample(6.72, 0.69, 122.3, 785, 3, MEASURED_ON, 31_343, "low", "HIGH RISK/DECLINE"),
        "us-west-2": LatencySample(7.63, 0.89, 95.1, 790, 3, MEASURED_ON, 31_343, "low", "HIGH RISK/DECLINE"),
    },
    "openai.gpt-5.6-sol": {
        "us-east-1": LatencySample(11.19, 4.19, 91.1, 1020, 3, MEASURED_ON, 31_343, "low", "HIGH RISK/DECLINE"),
        "us-east-2": LatencySample(30.66, 22.07, 31.6, 900, 3, MEASURED_ON, 31_343, "low", "HIGH RISK/DECLINE"),
    },
    # openai.gpt-5.5 and openai.gpt-5.4 are priced and region-mapped above but were
    # NOT measured on this prompt. Absent on purpose: see resolve_mantle_region, which
    # falls back to declared availability order for an unmeasured model.
}

#: NOT OUR MEASUREMENT. Third-party dashboard readings supplied by the customer-facing
#: team, on a **~16-32 output token** prompt: (TTFT ms, output tokens/s). Recorded here
#: as independent corroboration and labelled as such -- no sample count and no prompt
#: text came with it, so it cannot be treated as evidence on its own and it never feeds
#: :func:`resolve_mantle_region`.
#:
#: A missing TPS means the reading did not include one.
#:
#: WHERE IT AGREES WITH OURS (which is what makes it useful):
#:   * us-east-2 is the worst region for Luna in both (2703ms TTFT there vs 666ms in
#:     us-east-1; ours: 1.63s vs 0.79s).
#:   * us-east-2 is catastrophic for Sol in both (15636ms TTFT; ours: 22.07s, and a
#:     30.66s total that is 2.7x Sol in us-east-1).
#:   * Sol has by far the highest TTFT of the three variants in both (3896ms in
#:     us-east-1; ours: 4.19s, vs 0.79s for Luna).
#:
#: WHERE IT DIVERGES, AND WHY THAT IS THE LESSON:
#:   * Short-prompt throughput ranks us-west-2 FIRST for Luna (531ms / 405 tps vs
#:     us-east-1's 666ms / 220 tps). On our 31,343-char prompt us-east-1 wins on total
#:     latency (4.61s vs 5.02s p50) even though us-west-2 edges it on tokens/s
#:     (180.0 vs 176.0).
#:   * The reason: a 32-token reply is almost entirely TTFT, so the lowest-TTFT region
#:     wins. Our reply is ~800-1000 tokens, so ~83% of the wall clock is generation and
#:     total time is decided by throughput plus a much smaller TTFT term. Ranking on a
#:     short probe and deploying our workload picks the wrong region.
THIRD_PARTY_SHORT_PROMPT_PROBE: Final[dict[str, dict[str, tuple[float, float | None]]]] = {
    "openai.gpt-5.6-luna": {
        "us-west-2": (531.0, 405.0),
        "us-east-1": (666.0, 220.0),
        "us-east-2": (2703.0, 73.0),
    },
    "openai.gpt-5.6-terra": {
        "us-west-2": (796.0, None),
        "us-east-1": (914.0, None),
        "us-east-2": (1105.0, None),
    },
    "openai.gpt-5.6-sol": {
        "us-east-1": (3896.0, None),
        "us-east-2": (15636.0, None),
    },
}

#: Provenance label carried by every rendering of the third-party table, so a screenshot
#: of it cannot be mistaken for one of ours.
THIRD_PARTY_PROBE_SOURCE: Final[str] = (
    "THIRD-PARTY (not measured by this repo): customer-supplied dashboard readings on a "
    "~16-32 output-token prompt. No sample count, no prompt text, no verdict validation. "
    "Corroboration only -- it does not select a region."
)

#: Ultimate fallback when a model has neither a measurement nor a declared region list.
#: us-east-1 rather than AWS_REGION: it is the only region where every GPT-5.6 variant
#: is available, and it is the measured-fastest for the default synthesizer.
DEFAULT_MANTLE_REGION: Final[str] = "us-east-1"

_region_log_lock = threading.Lock()
_region_logged: set[tuple[str, str, str]] = set()


def matrix_age_days(today: _dt.date | None = None) -> int:
    """How many days old the recorded matrix is. Feeds the staleness warning."""
    return ((today or _dt.date.today()) - MEASURED_ON).days


def measured_region_preference(model_id: str) -> tuple[str, ...]:
    """Regions for ``model_id`` ordered fastest-first BY MEASURED p50 total latency.

    Only regions with a real :class:`LatencySample` appear, and only those that are also
    in :data:`MANTLE_REGIONS` -- a measurement of a region the model has since lost must
    not resurrect it. Returns ``()`` for an unmeasured model, so the caller falls back
    to declared availability order rather than to an invented ranking.

    Ordered on TOTAL latency, because for this workload total is the number the customer
    feels: one adjudication, no streaming UI on the critical path.

    HONEST NOTE ON WHAT THAT CHOICE IS WORTH: on the recorded data, ordering by TTFT
    gives the SAME ranking as ordering by total for all three variants, so the two keys
    are indistinguishable here and no test can tell them apart. Ordering by tokens/s does
    NOT -- it puts Luna's us-west-2 first, which is also where the third-party
    short-prompt probe puts it. So the ranking key that matters is total-vs-throughput,
    not total-vs-TTFT, and :func:`_assert`-style confidence in the TTFT distinction would
    be unearned. See ``test_ranking_on_throughput_would_pick_a_different_region``.
    """
    samples = MEASURED_SYNTHESIS_LATENCY.get(model_id)
    if not samples:
        return ()
    available = MANTLE_REGIONS.get(model_id)
    usable = [
        (region, sample)
        for region, sample in samples.items()
        if (available is None or region in available) and sample.n >= MIN_TRIALS_FOR_A_RANKING
    ]
    usable.sort(key=lambda item: (item[1].total_p50_s, item[0]))
    return tuple(region for region, _ in usable)


def measured_best_region(model_id: str) -> str | None:
    """The measured-fastest available region for ``model_id``, or None if unmeasured."""
    preference = measured_region_preference(model_id)
    return preference[0] if preference else None


def region_choice(model_id: str, region: str | None = None) -> tuple[str, str]:
    """``(region, why)`` for a mantle call. The 'why' is the point of this function.

    Precedence, most explicit first:

    1. an explicit ``region=`` argument (a caller that has already decided),
    2. ``BEDROCK_MANTLE_REGION`` (the operator override -- data residency, a new region,
       or simply disagreeing with our measurement),
    3. the measured-fastest region for this model from
       :data:`MEASURED_SYNTHESIS_LATENCY`,
    4. the model's first declared available region,
    5. :data:`DEFAULT_MANTLE_REGION`.

    ``AWS_REGION`` is deliberately NOT in that list. Where the stack happens to be
    deployed says nothing about which region answers a mantle call fastest, and it
    measurably matters: with ``AWS_REGION=us-west-2`` the previous revision sent every
    Luna synthesis to us-west-2, which measured 5.02s p50 against us-east-1's 4.61s
    (n=3 each) on the real prompt -- an 8.9% latency tax paid for no reason. A mantle
    call is in-region only and has no cross-region inference profile, so co-locating it
    with the Converse specialists buys nothing either.

    Steps 1 and 2 are still subject to the availability check below: a requested region
    the model is not served in is replaced, because the alternative is a confusing 404.
    """
    available = MANTLE_REGIONS.get(model_id)
    preference = measured_region_preference(model_id)

    override = os.environ.get("BEDROCK_MANTLE_REGION")
    if region:
        requested, why = region, "explicit region argument"
    elif override:
        requested, why = override, "BEDROCK_MANTLE_REGION override"
    elif preference:
        best = MEASURED_SYNTHESIS_LATENCY[model_id][preference[0]]
        return preference[0], (
            f"measured fastest for {model_id}: p50 {best.total_p50_s:.2f}s total, "
            f"{best.ttft_p50_s:.2f}s TTFT, {best.tps_p50:.1f} tok/s (n={best.n}, "
            f"{best.measured_on.isoformat()}, {best.prompt_chars}-char prompt)"
        )
    elif available:
        return available[0], (
            f"no latency measurement recorded for {model_id}; using its first declared "
            f"available region {available[0]}"
        )
    else:
        return DEFAULT_MANTLE_REGION, (
            f"no measurement and no availability list for {model_id}; falling back to "
            f"{DEFAULT_MANTLE_REGION}"
        )

    if available and requested not in available:
        # Availability wins over both the argument and the override, because the call
        # would otherwise fail outright. Prefer the measured-fastest available region
        # over the declared-first one -- same fallback, better destination.
        replacement = preference[0] if preference else available[0]
        return replacement, (
            f"{why} asked for {requested}, where {model_id} is not served "
            f"(available: {', '.join(available)}); using {replacement}"
        )
    return requested, why


def resolve_mantle_region(model_id: str, region: str | None = None) -> str:
    """A region where this variant is actually available, chosen from measurement.

    Thin wrapper over :func:`region_choice` that logs the reason once per distinct
    (model, region, reason) so a long-running runtime states its choice in the log
    without repeating it on every adjudication.
    """
    resolved, why = region_choice(model_id, region)
    key = (model_id, resolved, why)
    with _region_log_lock:
        first_time = key not in _region_logged
        if first_time:
            _region_logged.add(key)
    if first_time:
        log.info("mantle region for %s: %s (%s)", model_id, resolved, why)
    return resolved


def reset_region_log() -> None:
    """Forget which region choices have been logged (tests, region switches)."""
    with _region_log_lock:
        _region_logged.clear()


def rates_for(model_id: str) -> tuple[float, float, float]:
    """(input, cache_read, output) $ per 1M tokens. Raises for an unpriced id."""
    try:
        return MANTLE_RATES_PER_MTOK[model_id]
    except KeyError as exc:
        raise MantleUnavailable(
            f"no verified rate card for {model_id!r}; refusing to price it. Add the "
            "published rate before using this model in a cost-reported path."
        ) from exc


#: GPT-5.6 bills tokens written to cache at 1.25x the uncached input rate (a 30-minute
#: cache). GPT-5.4/5.5 cache automatically with NO write charge, so their multiplier is
#: 0. Dropping this term silently under-reports cost: a measured Luna synthesis
#: reported cacheWriteInputTokens=6740, which is $0.00927 of a $0.01499 call -- 62% of
#: the total.
CACHE_WRITE_MULTIPLIER: Final[dict[str, float]] = {
    "openai.gpt-5.6-sol": 1.25,
    "openai.gpt-5.6-terra": 1.25,
    "openai.gpt-5.6-luna": 1.25,
    "openai.gpt-5.5": 0.0,
    "openai.gpt-5.4": 0.0,
}


def cost_usd(model_id: str, usage: dict[str, int] | None) -> float | None:
    """Cost of one mantle call, or None when usage is unknown.

    Never estimates: a missing usage dict yields None so a caller renders an em dash
    rather than a fabricated figure.

    All four token classes are priced. Implicit caching is ON by default for GPT-5.6, so
    a first call against a large prompt reports most of its input as
    ``cacheWriteInputTokens``; that term dominates the bill and must not be dropped.
    """
    if not usage:
        return None
    rate_in, rate_cache, rate_out = rates_for(model_id)
    rate_write = rate_in * CACHE_WRITE_MULTIPLIER.get(model_id, 1.25)
    fresh = usage.get("inputTokens") or 0
    cached = usage.get("cacheReadInputTokens") or 0
    written = usage.get("cacheWriteInputTokens") or 0
    out = usage.get("outputTokens") or 0
    return (
        fresh * rate_in + cached * rate_cache + written * rate_write + out * rate_out
    ) / 1_000_000


def _client(region: str) -> Any:
    """OpenAI SDK client pointed at bedrock-mantle, authenticated with a short-term token."""
    try:
        from aws_bedrock_token_generator import provide_token
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise MantleUnavailable(
            "GPT-5.x on Bedrock needs 'openai>=2.45.0' and 'aws-bedrock-token-generator'. "
            f"Install them or select a Claude synthesizer instead ({exc})."
        ) from exc

    try:
        token = provide_token(region=region)
    except Exception as exc:
        raise MantleUnavailable(
            f"could not mint a Bedrock bearer token for {region}: {exc}. The principal "
            "needs bedrock-mantle:CreateInference (AmazonBedrockMantleInferenceAccess)."
        ) from exc

    return OpenAI(
        base_url=base_url_for(region),
        api_key=token,
        max_retries=int(os.environ.get("BEDROCK_MANTLE_MAX_RETRIES", "4")),
    )


def _usage(response: Any) -> dict[str, int] | None:
    """Normalise Responses usage onto the Bedrock Converse convention.

    Responses ``input_tokens`` is the TOTAL prompt including cached tokens; Converse
    reports only the fresh count. Subtracting keeps a cached token billed once, at the
    cache rate, for both families.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    total_in = int(getattr(usage, "input_tokens", 0) or 0)
    details = getattr(usage, "input_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    written = int(getattr(details, "cache_write_tokens", 0) or 0) if details else 0
    out = int(getattr(usage, "output_tokens", 0) or 0)
    return {
        "inputTokens": max(total_in - cached - written, 0),
        "outputTokens": out,
        "cacheReadInputTokens": cached,
        "cacheWriteInputTokens": written,
        "totalTokens": total_in + out,
    }


def complete(
    model_id: str,
    prompt: str,
    *,
    max_output_tokens: int,
    effort: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """One synchronous Responses call.

    Returns ``{text, usage, latency_ms, model_id, region, effort, cost_usd}``. Raises
    ``MantleUnavailable`` when the endpoint cannot be reached at all, so a caller can
    fall back to a Converse model deliberately rather than silently.
    """
    resolved_effort = (effort or os.environ.get("BEDROCK_MANTLE_REASONING_EFFORT")
                       or DEFAULT_EFFORT)
    if resolved_effort not in MANTLE_EFFORTS:
        raise MantleUnavailable(
            f"reasoning effort {resolved_effort!r} is not one of {list(MANTLE_EFFORTS)}"
        )
    resolved_region = resolve_mantle_region(model_id, region)
    client = _client(resolved_region)

    started = time.perf_counter()
    try:
        response = client.responses.create(
            model=model_id,
            input=prompt,
            reasoning={"effort": resolved_effort},
            max_output_tokens=max_output_tokens,
            store=False,
        )
    except Exception as exc:
        raise MantleUnavailable(
            f"bedrock-mantle call to {model_id} in {resolved_region} failed: {exc}"
        ) from exc
    latency_ms = (time.perf_counter() - started) * 1000.0

    usage = _usage(response)
    return {
        "text": getattr(response, "output_text", "") or "",
        "usage": usage,
        "latency_ms": latency_ms,
        "model_id": model_id,
        "region": resolved_region,
        "effort": resolved_effort,
        "cost_usd": cost_usd(model_id, usage),
    }


# ---------------------------------------------------------------------------
# Re-measurement: the table must be re-derivable, not just trusted
# ---------------------------------------------------------------------------

#: Re-measuring calls every variant in every region and costs real money, so it is
#: double-gated exactly like ``evals/bench.py --live``: the flag AND this env var.
MEASURE_ENV_GATE: Final[str] = "AGENTCORE_MANTLE_ALLOW_MEASURE"

#: Variants the recorded matrix covers. 5.4/5.5 are priced but were never measured, so
#: re-measuring them would silently widen the table beyond what is documented.
MEASURE_DEFAULT_MODELS: Final[tuple[str, ...]] = (
    "openai.gpt-5.6-luna",
    "openai.gpt-5.6-terra",
    "openai.gpt-5.6-sol",
)

#: A re-measurement on a prompt this much smaller (or larger) than
#: :data:`MEASURED_PROMPT_CHARS` is not comparable with the recorded table, because the
#: TTFT-vs-throughput balance is what the ranking turns on.
PROMPT_COMPARABILITY_TOLERANCE: Final[float] = 0.25

#: How far a re-measured p50 may drift from the recorded one before the diff calls it a
#: change rather than noise. 25% is generous on purpose: n=3 medians are noisy, and the
#: recorded max/min spreads in /tmp/bench/matrix.json span 1.3x within a single cell.
DRIFT_TOLERANCE: Final[float] = 0.25

#: Beyond this the recorded table should be re-measured before it is quoted to anyone.
STALE_AFTER_DAYS: Final[int] = 90


def measure_gate_error(requested: bool) -> str | None:
    """Why a re-measurement may not run, or None when both gates are satisfied."""
    if not requested:
        return "re-measurement not requested (pass --measure)"
    if os.environ.get(MEASURE_ENV_GATE) != "1":
        return (
            f"--measure also requires {MEASURE_ENV_GATE}=1 in the environment. Two "
            "gates, deliberately: re-measuring the matrix is trials x models x regions "
            "billed inference calls on a 31K-char prompt."
        )
    return None


def prompt_comparability(prompt_chars: int) -> tuple[bool, str]:
    """``(comparable, note)`` for a re-measurement prompt against the recorded one.

    A ranking measured on a materially different prompt size cannot be diffed against
    the recorded table -- that is the entire short-prompt trap -- so the diff says so
    rather than pretending the two are the same experiment.
    """
    ratio = prompt_chars / MEASURED_PROMPT_CHARS
    low, high = 1.0 - PROMPT_COMPARABILITY_TOLERANCE, 1.0 + PROMPT_COMPARABILITY_TOLERANCE
    if low <= ratio <= high:
        return True, (
            f"prompt is {prompt_chars} chars, {ratio:.2f}x the recorded "
            f"{MEASURED_PROMPT_CHARS}: comparable"
        )
    return False, (
        f"prompt is {prompt_chars} chars, {ratio:.2f}x the recorded "
        f"{MEASURED_PROMPT_CHARS}. NOT comparable: the region ranking depends on the "
        "TTFT-vs-throughput balance, which is a function of prompt and reply size. "
        "Treat these numbers as a new experiment, not as a diff."
    )


def _validate_adjudication(text: str) -> tuple[bool, int, str]:
    """``(valid, key_count, verdict_or_reason)`` for one candidate synthesis reply.

    The latency of a call that produced an invalid object is worthless, so the measure
    path validates the 21-key contract rather than timing whatever came back.
    """
    import json  # noqa: PLC0415

    from agents.contracts import Adjudication  # noqa: PLC0415

    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{") :] if "{" in body else body
    try:
        payload = json.loads(body)
    except Exception as exc:
        return False, 0, f"INVALID JSON: {str(exc)[:80]}"
    keys = len(payload) if isinstance(payload, dict) else 0
    try:
        Adjudication.model_validate(payload)
    except Exception as exc:
        return False, keys, f"INVALID CONTRACT: {str(exc)[:80]}"
    return True, keys, (
        f"{payload.get('overall_risk_decision')}/{payload.get('recommendation_decision')}"
    )


def measure_once(
    model_id: str,
    region: str,
    prompt: str,
    *,
    effort: str = DEFAULT_EFFORT,
    max_output_tokens: int = 8192,
) -> dict[str, Any]:
    """One streamed Responses call, timed. TTFT needs the stream; total does not.

    Streaming is used only to observe time-to-first-token. It is a separate code path
    from :func:`complete`, which the synthesizer uses non-streamed -- so a total measured
    here is the streamed total, and that is stated in the report rather than assumed
    equal.
    """
    client = _client(region)
    started = time.perf_counter()
    ttft: float | None = None
    chunks: list[str] = []
    final: Any = None
    try:
        stream = client.responses.create(
            model=model_id,
            input=prompt,
            reasoning={"effort": effort},
            max_output_tokens=max_output_tokens,
            store=False,
            stream=True,
        )
        for event in stream:
            kind = getattr(event, "type", "")
            if kind == "response.output_text.delta":
                if ttft is None:
                    ttft = time.perf_counter() - started
                chunks.append(getattr(event, "delta", "") or "")
            elif kind == "response.completed":
                final = getattr(event, "response", None)
    except Exception as exc:
        return {
            "model_id": model_id,
            "region": region,
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:160]}",
        }
    total_s = time.perf_counter() - started

    usage = _usage(final) if final is not None else None
    out_tok = int((usage or {}).get("outputTokens") or 0)
    valid, keys, verdict = _validate_adjudication("".join(chunks))
    return {
        "model_id": model_id,
        "region": region,
        "ok": True,
        "total_s": total_s,
        "ttft_s": ttft,
        "tps": (out_tok / total_s) if total_s and out_tok else None,
        "output_tokens": out_tok,
        "usage": usage,
        "cost_usd": cost_usd(model_id, usage),
        "valid": valid,
        "keys": keys,
        "verdict": verdict,
        "effort": effort,
    }


def measure_matrix(
    prompt: str,
    *,
    models: Sequence[str] = MEASURE_DEFAULT_MODELS,
    trials: int = MEASURED_TRIALS,
    effort: str = DEFAULT_EFFORT,
    max_output_tokens: int = 8192,
    call: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Re-run the model x region matrix on ``prompt`` and return the raw result.

    Spends money: ``trials`` calls per (model, region) pair the model is actually served
    in. The caller is responsible for the gate; see :func:`measure_gate_error`.

    ``call`` is injectable so the aggregation, the diff and the renderers are all
    testable offline without touching the endpoint.

    Regions come from :data:`MANTLE_REGIONS` rather than from the recorded matrix, so a
    newly available region shows up as a new row instead of being invisible.
    """
    import statistics  # noqa: PLC0415

    invoke = call or measure_once
    rows: list[dict[str, Any]] = []
    for model_id in models:
        for region in MANTLE_REGIONS.get(model_id, (DEFAULT_MANTLE_REGION,)):
            runs = [
                invoke(
                    model_id,
                    region,
                    prompt,
                    effort=effort,
                    max_output_tokens=max_output_tokens,
                )
                for _ in range(trials)
            ]
            good = [r for r in runs if r.get("ok")]
            row: dict[str, Any] = {
                "model_id": model_id,
                "region": region,
                "attempted": len(runs),
                "n": len(good),
            }
            if not good:
                row["ok"] = False
                row["error"] = next((r.get("error") for r in runs if r.get("error")), "unknown")
                rows.append(row)
                continue
            totals = [r["total_s"] for r in good]
            ttfts = [r["ttft_s"] for r in good if r.get("ttft_s") is not None]
            tps = [r["tps"] for r in good if r.get("tps") is not None]
            costs = [r["cost_usd"] for r in good if r.get("cost_usd") is not None]
            row.update(
                {
                    "ok": True,
                    "total_p50_s": statistics.median(totals),
                    "total_min_s": min(totals),
                    "total_max_s": max(totals),
                    # None, not 0.0: a missing TTFT is unknown, not instant.
                    "ttft_p50_s": statistics.median(ttfts) if ttfts else None,
                    "tps_p50": statistics.median(tps) if tps else None,
                    "output_tokens_p50": int(
                        statistics.median([r["output_tokens"] for r in good])
                    ),
                    "valid": sum(1 for r in good if r.get("valid")),
                    "verdicts": sorted({r.get("verdict") for r in good if r.get("valid")}),
                    "cost_usd_total": sum(costs) if len(costs) == len(good) else None,
                }
            )
            rows.append(row)

    comparable, note = prompt_comparability(len(prompt))
    return {
        "rows": rows,
        "prompt_chars": len(prompt),
        "trials": trials,
        "effort": effort,
        "max_output_tokens": max_output_tokens,
        "measured_on": _dt.date.today().isoformat(),
        "comparable_with_recorded": comparable,
        "comparability_note": note,
        "recorded_prompt_chars": MEASURED_PROMPT_CHARS,
        "streamed": True,
        "caveats": [
            "Totals are STREAMED totals; agents.synthesize calls the endpoint "
            "non-streamed. TTFT cannot be observed without the stream.",
            f"p50 from n={trials} per cell is a median of {trials} points, not a "
            "percentile. It is labelled with its n everywhere it is printed.",
            "Latency of a call that failed the 21-key contract is not a useful number; "
            "the 'valid' column says how many of each cell's calls passed it.",
        ],
    }


def diff_against_recorded(measured: dict[str, Any]) -> list[dict[str, Any]]:
    """Row-by-row comparison of a fresh :func:`measure_matrix` against the table.

    Statuses: ``new`` (no recorded cell), ``failed`` (every trial errored),
    ``unchanged`` (within :data:`DRIFT_TOLERANCE`), ``faster`` / ``slower`` (outside it),
    and ``missing`` for a recorded cell the re-measurement did not cover.

    A ranking change is reported separately by :func:`render_matrix_diff`: an individual
    cell moving 10% matters much less than us-east-1 losing first place.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in measured.get("rows", []):
        model_id, region = row["model_id"], row["region"]
        seen.add((model_id, region))
        recorded = MEASURED_SYNTHESIS_LATENCY.get(model_id, {}).get(region)
        entry: dict[str, Any] = {
            "model_id": model_id,
            "region": region,
            "recorded_p50_s": recorded.total_p50_s if recorded else None,
            "measured_p50_s": row.get("total_p50_s"),
            "n": row.get("n", 0),
        }
        if not row.get("ok"):
            entry["status"] = "failed"
            entry["detail"] = row.get("error", "unknown")
        elif recorded is None:
            entry["status"] = "new"
            entry["detail"] = "no recorded cell for this model/region pair"
        else:
            delta = row["total_p50_s"] - recorded.total_p50_s
            ratio = row["total_p50_s"] / recorded.total_p50_s
            entry["delta_s"] = delta
            entry["ratio"] = ratio
            if abs(ratio - 1.0) <= DRIFT_TOLERANCE:
                entry["status"] = "unchanged"
            else:
                entry["status"] = "slower" if delta > 0 else "faster"
            entry["detail"] = (
                f"{recorded.total_p50_s:.2f}s (n={recorded.n}, "
                f"{recorded.measured_on.isoformat()}) -> {row['total_p50_s']:.2f}s "
                f"(n={row['n']}), {ratio:.2f}x"
            )
        out.append(entry)

    for model_id, regions in MEASURED_SYNTHESIS_LATENCY.items():
        for region, recorded in regions.items():
            if (model_id, region) in seen:
                continue
            out.append(
                {
                    "model_id": model_id,
                    "region": region,
                    "recorded_p50_s": recorded.total_p50_s,
                    "measured_p50_s": None,
                    "n": 0,
                    "status": "missing",
                    "detail": "recorded cell was not re-measured in this run",
                }
            )
    return out


def estimate_measure_spend(
    prompt: str,
    *,
    models: Sequence[str] = MEASURE_DEFAULT_MODELS,
    trials: int = MEASURED_TRIALS,
    max_output_tokens: int = 8192,
) -> dict[str, Any]:
    """UPPER BOUND on what a re-measurement costs, with its basis stated.

    Input tokens are ESTIMATED from a chars/4 divisor -- there is no CountTokens API on
    the mantle endpoint, so a real pre-flight count is not available. Input is priced at
    the 1.25x cache-WRITE rate and output at the full ``max_output_tokens`` cap, so the
    figure is an over-estimate by construction. It is never presented as a measurement.
    """
    from agents.models import estimate_prompt_tokens  # noqa: PLC0415

    in_tokens = estimate_prompt_tokens(prompt)
    per_model: dict[str, Any] = {}
    total = 0.0
    calls = 0
    for model_id in models:
        regions = MANTLE_REGIONS.get(model_id, (DEFAULT_MANTLE_REGION,))
        model_calls = len(regions) * trials
        rate_in, _, rate_out = rates_for(model_id)
        write_rate = rate_in * CACHE_WRITE_MULTIPLIER.get(model_id, 1.25)
        per_call = (in_tokens * write_rate + max_output_tokens * rate_out) / 1_000_000
        per_model[model_id] = {
            "regions": list(regions),
            "calls": model_calls,
            "upper_bound_usd": per_call * model_calls,
        }
        total += per_call * model_calls
        calls += model_calls
    return {
        "total_calls": calls,
        "total_upper_bound_usd": total,
        "per_model": per_model,
        "estimated_input_tokens": in_tokens,
        "basis": (
            f"UPPER BOUND. Input ESTIMATED at chars/4 = {in_tokens} tokens (the mantle "
            "endpoint has no CountTokens API, so this is not a real count) and priced at "
            "the 1.25x cache-write rate; output priced at the full "
            f"{max_output_tokens}-token cap, which no measured call reached "
            "(recorded p50 output was 785-1020 tokens). Actual spend will be lower."
        ),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_recorded_matrix(today: _dt.date | None = None) -> str:
    """The recorded matrix as text, with its provenance and its caveats attached."""
    age = matrix_age_days(today)
    lines = [
        "MEASURED synthesis latency by model x region",
        f"  workload   : this repo's real synthesis prompt, {MEASURED_PROMPT_CHARS} chars "
        "(master prompt + all eight specialist narratives, APP-1004)",
        f"  method     : streamed Responses calls, effort={DEFAULT_EFFORT}, "
        f"n={MEASURED_TRIALS} per cell, {MEASURED_ON.isoformat()} ({age} days ago)",
        "  validation : all 24 calls returned a valid 21-key Adjudication and all 24 "
        "agreed on HIGH RISK / DECLINE",
        "  provenance : /tmp/bench/matrix.json (scratch); re-derive with "
        f"{MEASURE_ENV_GATE}=1 python3 -m agents.mantle --measure",
        "",
        f"  {'model':<22}{'region':<11}{'p50 total':>10}{'p50 TTFT':>10}"
        f"{'p50 tok/s':>11}{'out tok':>9}{'n':>4}",
    ]
    for model_id in MEASURED_SYNTHESIS_LATENCY:
        for region in measured_region_preference(model_id):
            s = MEASURED_SYNTHESIS_LATENCY[model_id][region]
            lines.append(
                f"  {model_id:<22}{region:<11}{s.total_p50_s:>9.2f}s{s.ttft_p50_s:>9.2f}s"
                f"{s.tps_p50:>11.1f}{s.output_tokens_p50:>9}{s.n:>4}"
            )
    lines.append("")
    lines.append(
        "  RANKED ON TOTAL LATENCY, which is the number one adjudication feels. Ranking "
        "the same cells on tokens/s puts Luna's us-west-2 first instead (180.0 vs 176.0 "
        "tok/s, but 5.02s vs 4.61s total) -- which is the axis the third-party "
        "short-prompt probe ranks on. Ranking on TTFT happens to agree with total here."
    )
    if age > STALE_AFTER_DAYS:
        lines.append(
            f"  WARNING: this table is {age} days old (stale after {STALE_AFTER_DAYS}). "
            "Re-measure before quoting it."
        )
    return "\n".join(lines)


def render_third_party_probe() -> str:
    """The customer-supplied short-prompt readings, labelled as not ours."""
    lines = [
        THIRD_PARTY_PROBE_SOURCE,
        f"  {'model':<22}{'region':<11}{'TTFT':>9}{'tok/s':>9}",
    ]
    for model_id, regions in THIRD_PARTY_SHORT_PROMPT_PROBE.items():
        for region, (ttft_ms, tps) in sorted(regions.items(), key=lambda kv: kv[1][0]):
            tps_text = f"{tps:.0f}" if tps is not None else "n/a"
            lines.append(f"  {model_id:<22}{region:<11}{ttft_ms:>8.0f}ms{tps_text:>9}")
    lines += [
        "",
        "  AGREES with our measurement: us-east-2 is the worst region for Luna and "
        "catastrophic for Sol; Sol has by far the highest TTFT of the three variants.",
        "  DIVERGES: the short-prompt reading ranks us-west-2 first for Luna "
        "(531ms/405 tok/s). On our 31,343-char prompt us-east-1 wins on total latency "
        "(4.61s vs 5.02s p50, n=3 each).",
        "  WHY: a ~32-token reply is almost all time-to-first-token, so lowest TTFT "
        "wins. Our reply is ~800-1000 tokens, so throughput dominates and TTFT is a "
        "small term. Pick the region on the workload you actually run.",
    ]
    return "\n".join(lines)


def render_matrix_diff(measured: dict[str, Any], diff: list[dict[str, Any]]) -> str:
    """Re-measurement vs the recorded table, including any ranking change."""
    lines = [
        "RE-MEASUREMENT vs RECORDED",
        f"  prompt     : {measured['prompt_chars']} chars "
        f"(recorded: {measured['recorded_prompt_chars']})",
        f"  method     : n={measured['trials']} per cell, effort={measured['effort']}, "
        f"{measured['measured_on']}",
        f"  comparable : {measured['comparable_with_recorded']} -- "
        f"{measured['comparability_note']}",
        "",
        f"  {'model':<22}{'region':<11}{'status':<11}detail",
    ]
    for entry in diff:
        lines.append(
            f"  {entry['model_id']:<22}{entry['region']:<11}{entry['status']:<11}"
            f"{entry.get('detail', '')}"
        )

    fresh_order: dict[str, list[tuple[float, str]]] = {}
    for row in measured.get("rows", []):
        if row.get("ok") and row.get("total_p50_s") is not None:
            fresh_order.setdefault(row["model_id"], []).append(
                (row["total_p50_s"], row["region"])
            )
    lines.append("")
    any_changed = False
    for model_id, entries in fresh_order.items():
        new_rank = [region for _, region in sorted(entries)]
        old_rank = list(measured_region_preference(model_id))
        changed = new_rank != old_rank
        any_changed = any_changed or changed
        lines.append(
            f"  ranking {model_id:<22}{'CHANGED' if changed else 'UNCHANGED':<10}"
            f"recorded {old_rank or ['(unmeasured)']} -> measured {new_rank}"
        )
    lines.append("")
    if any_changed:
        # Only warned when it is true: an advisory printed on every run is an advisory
        # nobody reads, and it would make "did the ranking move?" ungreppable.
        lines.append(
            "  A CHANGED ranking means the default region in agents/mantle.py is no "
            "longer the measured-best one. Update MEASURED_SYNTHESIS_LATENCY (with the "
            "new n and date) rather than overriding BEDROCK_MANTLE_REGION and leaving "
            "the table wrong."
        )
    for caveat in measured.get("caveats", []):
        lines.append(f"  caveat: {caveat}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_probe_prompt(application_id: str = "APP-1004") -> str:
    """Assemble the CURRENT synthesis prompt, so a re-measurement times today's prompt.

    Runs the eight-way fan-out to get real specialist narratives, because the narratives
    ARE the prompt: substituting stubs produces a few-hundred-character prompt whose
    ranking says nothing about production. In ``MOCK_MODE=1`` the fan-out returns canned
    analyses and the resulting prompt is much shorter --
    :func:`prompt_comparability` catches that and the diff labels itself not comparable.
    """
    import asyncio  # noqa: PLC0415

    from agents.fanout import fan_out  # noqa: PLC0415
    from agents.synthesize import build_synthesis_prompt  # noqa: PLC0415
    from fixtures.loader import build_payload  # noqa: PLC0415

    results = asyncio.run(fan_out(build_payload(application_id)))
    return build_synthesis_prompt(results)


def _build_parser() -> Any:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="python3 -m agents.mantle",
        description=(
            "Show the measured GPT-5.6 model x region latency matrix that picks this "
            "repo's synthesizer region, or re-measure it. Re-measuring calls Amazon "
            f"Bedrock and is double-gated on --measure plus {MEASURE_ENV_GATE}=1."
        ),
    )
    parser.add_argument("--measure", action="store_true",
                       help=f"re-run the matrix live; needs {MEASURE_ENV_GATE}=1")
    parser.add_argument("--trials", type=int, default=MEASURED_TRIALS,
                       help=f"calls per model/region cell (default {MEASURED_TRIALS})")
    parser.add_argument("--model", action="append", dest="models",
                       help="restrict to a model id (repeatable)")
    parser.add_argument("--effort", default=DEFAULT_EFFORT, choices=MANTLE_EFFORTS,
                       help=f"reasoning effort (default {DEFAULT_EFFORT})")
    parser.add_argument("--max-output-tokens", type=int, default=8192,
                       help="per-call output cap for the probe (default 8192)")
    parser.add_argument("--application", default="APP-1004",
                       help="fixture whose fan-out builds the probe prompt")
    parser.add_argument("--prompt-file",
                       help="reuse a saved synthesis prompt instead of re-running the fan-out")
    parser.add_argument("--save-prompt",
                       help="write the assembled probe prompt here for reuse")
    parser.add_argument("--json", dest="json_path",
                       help="write the machine-readable re-measurement here")
    parser.add_argument("--dry-run", action="store_true",
                       help="print the call plan and spend upper bound, then stop")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Print the recorded matrix, or re-measure it behind two gates."""
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    args = _build_parser().parse_args(argv)

    if not args.measure:
        print(render_recorded_matrix())
        print()
        print(render_third_party_probe())
        print()
        choice, why = region_choice(
            os.environ.get("BEDROCK_MODEL_SYNTHESIZER", "openai.gpt-5.6-luna")
        )
        print(f"default synthesizer region: {choice} ({why})")
        print(
            f"\nNothing was called; no spend. Re-measure with {MEASURE_ENV_GATE}=1 "
            "python3 -m agents.mantle --measure"
        )
        return 0

    gate = measure_gate_error(args.measure)
    if gate:
        print(f"refusing to measure: {gate}")
        return 2

    models = tuple(args.models) if args.models else MEASURE_DEFAULT_MODELS
    unknown = [m for m in models if m not in MANTLE_RATES_PER_MTOK]
    if unknown:
        print(f"refusing to measure unpriced model(s): {unknown}")
        return 2
    if args.trials < MIN_TRIALS_FOR_A_RANKING:
        # Refused rather than silently producing a table that cannot be ranked: a cell
        # below the minimum is dropped by measured_region_preference, so the run would
        # spend money and change nothing.
        print(
            f"refusing to measure with --trials {args.trials}: a cell needs at least "
            f"{MIN_TRIALS_FOR_A_RANKING} trials to enter the ranking, so a smaller run "
            "would spend money and produce a table the region choice ignores."
        )
        return 2

    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    else:
        prompt = build_probe_prompt(args.application)
    if args.save_prompt:
        Path(args.save_prompt).write_text(prompt, encoding="utf-8")

    comparable, note = prompt_comparability(len(prompt))
    estimate = estimate_measure_spend(
        prompt, models=models, trials=args.trials, max_output_tokens=args.max_output_tokens
    )
    print("LIVE RE-MEASUREMENT -- this calls Amazon Bedrock and incurs charges.")
    print(f"  prompt   : {len(prompt)} chars -- {note}")
    print(f"  plan     : {estimate['total_calls']} calls "
          f"({args.trials} trials x each model's available regions)")
    for model_id, detail in estimate["per_model"].items():
        print(f"    {model_id:<22}{detail['calls']:>3} calls  "
              f"{', '.join(detail['regions'])}")
    print(f"  spend    : <= ${estimate['total_upper_bound_usd']:.4f}")
    print(f"    basis  : {estimate['basis']}")
    if not comparable:
        print("  WARNING: prompt size is not comparable with the recorded table; the "
              "diff will be labelled accordingly.")
    if args.dry_run:
        print("\n--dry-run: nothing was called, no spend.")
        return 0

    measured = measure_matrix(
        prompt,
        models=models,
        trials=args.trials,
        effort=args.effort,
        max_output_tokens=args.max_output_tokens,
    )
    print()
    print(render_matrix_diff(measured, diff_against_recorded(measured)))
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(measured, indent=2), encoding="utf-8")
        print(f"\nwrote machine-readable re-measurement to {args.json_path}")
    return 0


# ---------------------------------------------------------------------------
# Strands-native model provider (strands-agents >= 1.35)
# ---------------------------------------------------------------------------

#: Minimum strands release carrying ``strands.models.openai_responses``.
STRANDS_RESPONSES_PROVIDER: Final[str] = "strands.models.openai_responses"


def responses_provider_available() -> bool:
    """Whether the installed strands exposes the native Responses provider.

    False on strands 1.26.x, True from the release that adds
    ``strands.models.openai_responses``. Callers use this to choose between the native
    provider and :func:`complete`; both reach the same endpoint, so the difference is
    ergonomics (a real ``strands.Agent``, with tool use, hooks and streaming) rather
    than capability.
    """
    try:
        __import__(STRANDS_RESPONSES_PROVIDER)
    except Exception:
        return False
    return True


def build_responses_model(
    model_id: str,
    *,
    max_tokens: int,
    effort: str | None = None,
    region: str | None = None,
) -> Any:
    """A Strands model object for a GPT-5.x id, usable anywhere ``BedrockModel`` is.

    This is what lets a GPT specialist be an ordinary ``strands.Agent`` alongside the
    Claude ones -- same fan-out, same hooks, same ``result.metrics.accumulated_usage``
    -- instead of a second bespoke code path.

    Routing goes through the provider's own ``bedrock_mantle_config`` rather than a
    hand-built ``client_args``, and that is the whole point of this function now:

    * **The token is minted per REQUEST, not per construction.** Bedrock Mantle bearer
      tokens are short-lived. Passing ``api_key`` in ``client_args`` bakes one token
      into the model object, so a long-running runtime that holds a model for hours
      eventually fails on an expired credential. ``bedrock_mantle_config`` calls
      ``provide_token`` inside ``_resolve_client_args`` on every request instead. That
      removes the failure mode rather than documenting it.
    * **The provider derives the base path itself**, and it derives the same split this
      module measured independently: ``strands.models._openai_bedrock`` carries
      ``_OPENAI_PATH_MODEL_PREFIXES = ("openai.gpt-5.",)`` and routes those to
      ``/openai/v1`` while everything else (``gpt-oss-*``) gets ``/v1``. So the
      four-way measurement in the module docstring is now corroborated by the SDK
      rather than only by us. :func:`base_url_for` is kept for
      :func:`complete`, which talks to the endpoint directly and has no provider to
      derive anything for it.

    ``client_args`` must therefore NOT carry ``api_key`` or ``base_url`` -- the
    provider raises ``ValueError`` if it does, which is the correct fail-fast.

    Raises :class:`MantleUnavailable` when the provider or its dependencies are absent,
    so a caller can fall back to :func:`complete` deliberately.
    """
    resolved_region = resolve_mantle_region(model_id, region)
    resolved_effort = (
        effort or os.environ.get("BEDROCK_MANTLE_REASONING_EFFORT") or DEFAULT_EFFORT
    )
    if resolved_effort not in MANTLE_EFFORTS:
        raise MantleUnavailable(
            f"reasoning effort {resolved_effort!r} is not one of {list(MANTLE_EFFORTS)}"
        )
    try:
        from strands.models.openai_responses import OpenAIResponsesModel
    except ImportError as exc:
        raise MantleUnavailable(
            "the native Strands Responses provider needs strands-agents with "
            f"{STRANDS_RESPONSES_PROVIDER} (absent in 1.26.x), openai>=2.0.0 and "
            f"aws-bedrock-token-generator. Use agents.mantle.complete() instead ({exc})."
        ) from exc

    return OpenAIResponsesModel(
        model_id=model_id,
        bedrock_mantle_config={"region": resolved_region},
        params={
            "max_output_tokens": max_tokens,
            "reasoning": {"effort": resolved_effort},
            "store": False,
        },
    )


__all__ = [
    "DEFAULT_EFFORT",
    "STRANDS_RESPONSES_PROVIDER",
    "DEFAULT_MANTLE_REGION",
    "DRIFT_TOLERANCE",
    "MANTLE_EFFORTS",
    "MANTLE_RATES_PER_MTOK",
    "MANTLE_REGIONS",
    "MEASURED_ON",
    "MEASURED_PROMPT_CHARS",
    "MEASURED_SYNTHESIS_LATENCY",
    "MEASURED_TRIALS",
    "MEASURE_DEFAULT_MODELS",
    "MEASURE_ENV_GATE",
    "MIN_TRIALS_FOR_A_RANKING",
    "STALE_AFTER_DAYS",
    "THIRD_PARTY_PROBE_SOURCE",
    "THIRD_PARTY_SHORT_PROMPT_PROBE",
    "LatencySample",
    "MantleUnavailable",
    "build_probe_prompt",
    "build_responses_model",
    "complete",
    "cost_usd",
    "responses_provider_available",
    "diff_against_recorded",
    "estimate_measure_spend",
    "is_mantle_model",
    "main",
    "matrix_age_days",
    "measure_gate_error",
    "measure_matrix",
    "measure_once",
    "measured_best_region",
    "measured_region_preference",
    "prompt_comparability",
    "rates_for",
    "region_choice",
    "render_matrix_diff",
    "render_recorded_matrix",
    "render_third_party_probe",
    "reset_region_log",
    "resolve_mantle_region",
]


if __name__ == "__main__":
    raise SystemExit(main())
