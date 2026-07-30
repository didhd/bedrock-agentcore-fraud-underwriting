"""
Per-agent Bedrock cost accounting.

Every number a customer sees has to come from a real token count. This module
therefore does exactly two things: hold the published rate card, and turn a
Bedrock ``usage`` dict into dollars. It cannot invent a token count - the
previous revision's ``NOMINAL_TOKENS`` (specialist 1800/450, synthesizer
5000/700) was a hand-written guess presented as spend, and both the README's
cost headline and the CloudWatch spend dashboard were derived from it. It is
gone. A caller with no real usage gets ``None`` from :func:`cost_usd_or_none` or
a ``MissingUsageError`` from :func:`cost_usd`.

Four correctness rules the old code broke:

1. **Rates are keyed by base model id and scaled by inference profile.** The
   table below is the ``global.*`` rate card. ``us.*`` bills exactly
   ``REGIONAL_MULTIPLIER`` (1.10x) on *every* category, so pricing a ``us.*``
   call at global rates under-reports by 9.09%. The multiplier is applied from
   the prefix carried by the model id itself.

2. **Bedrock's ``usage.inputTokens`` counts only NON-cached tokens.** Total input
   is ``inputTokens + cacheReadInputTokens + cacheWriteInputTokens``. Any cost
   function taking a single input number is wrong the moment prompt caching is
   on, so :func:`cost_usd` takes the whole usage dict. The cache keys are
   optional in Bedrock's ``Usage`` TypedDict and are read with ``.get()``.

3. **Sonnet 5's 2.00/10.00 is introductory and expires.** On
   ``SONNET_5_INTRO_EXPIRY`` it reverts to 3.00/15.00 - a 50% increase. Rate
   lookups take an ``as_of`` date, default today, and every returned rate/cost
   record carries ``introductory_pricing`` plus the expiry so a TCO slide cannot
   be built on the promo without the caveat travelling with it.

4. **Batch is 50% off but mutually exclusive with prompt caching**, and is not
   offered for every model. :data:`BATCH_SUPPORTED` records which, and passing
   cache tokens together with ``batch=True`` raises rather than quietly pricing
   an impossible request.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping
from typing import Any, Final

from agents.models import (
    BASE_HAIKU_4_5,
    BASE_OPUS_4_7,
    BASE_OPUS_4_8,
    BASE_OPUS_5,
    BASE_SONNET_4_6,
    BASE_SONNET_5,
    BASELINE_MODEL,
    HEAVY_MODEL,
    LIGHT_MODEL,
    MID_MODEL,
    REGIONAL_MULTIPLIER,
    ROLES,
    SYNTHESIZER_MODEL,
    US_PREFIX,
    base_model_id,
    model_for,
    prefix_of,
    tier_label,
)

__all__ = [
    "BASE_RATES_PER_MTOK",
    "BATCH_DISCOUNT",
    "BATCH_SUPPORTED",
    "CACHE_READ_MULTIPLIER",
    "CACHE_WRITE_1H_MULTIPLIER",
    "CACHE_WRITE_5M_MULTIPLIER",
    "MissingUsageError",
    "SONNET_5_INTRO_EXPIRY",
    "UnknownModelRateError",
    "UnsupportedBatchError",
    "compare_tiered_vs_uniform",
    "cost_breakdown",
    "cost_usd",
    "cost_usd_or_none",
    "normalize_usage",
    "project_monthly",
    "rate_card",
    "rates_for",
    "supports_batch",
    "total_input_tokens",
]


class UnknownModelRateError(KeyError):
    """Raised when no verified rate exists for a model id."""


class MissingUsageError(ValueError):
    """Raised when a cost is requested without real token counts."""


class UnsupportedBatchError(ValueError):
    """Raised for a batch price that Bedrock does not offer, or batch+caching."""


# ---------------------------------------------------------------------------
# Rate card
# ---------------------------------------------------------------------------

#: `global.*` on-demand rates in $ per 1,000,000 tokens as (input, output).
#: Verified live-invokable model ids only. Two ids appear in BATCH_SUPPORTED but
#: NOT here - `anthropic.claude-opus-4-7` and `anthropic.fable-5` - because their
#: Bedrock rates were not verified for this port. Pricing either raises
#: UnknownModelRateError rather than guessing.
BASE_RATES_PER_MTOK: Final[dict[str, tuple[float, float]]] = {
    BASE_HAIKU_4_5: (1.00, 5.00),
    BASE_SONNET_5: (2.00, 10.00),  # INTRODUCTORY - see SONNET_5_INTRO_EXPIRY
    BASE_SONNET_4_6: (3.00, 15.00),
    BASE_OPUS_5: (5.00, 25.00),
    BASE_OPUS_4_8: (5.00, 25.00),
}

#: Sonnet 5 introductory pricing applies to calls made strictly before this
#: date; from this date the standard rate below applies.
SONNET_5_INTRO_EXPIRY: Final[_dt.date] = _dt.date(2026, 9, 1)

#: Sonnet 5's rate on and after the expiry: a 50% increase on both categories.
SONNET_5_STANDARD_RATES: Final[tuple[float, float]] = (3.00, 15.00)

#: Cache rates are multipliers on the model's own base *input* rate.
CACHE_READ_MULTIPLIER: Final[float] = 0.10
CACHE_WRITE_5M_MULTIPLIER: Final[float] = 1.25
CACHE_WRITE_1H_MULTIPLIER: Final[float] = 2.00

#: Batch inference is half price where offered.
BATCH_DISCOUNT: Final[float] = 0.50

#: Which models Bedrock offers batch inference for. opus-4-8, sonnet-5,
#: opus-4-7 and fable-5 have no batch price and no batch service quota, so a
#: bulk-underwriting plan that assumes -50% must not pick them.
BATCH_SUPPORTED: Final[dict[str, bool]] = {
    BASE_OPUS_5: True,
    BASE_SONNET_4_6: True,
    BASE_HAIKU_4_5: True,
    BASE_OPUS_4_8: False,
    BASE_SONNET_5: False,
    BASE_OPUS_4_7: False,
    "anthropic.fable-5": False,
}

#: Batch inference and prompt caching cannot be combined on the same request.
BATCH_AND_CACHING_ARE_EXCLUSIVE: Final[bool] = True

_CACHE_TTLS: Final[dict[str, float]] = {
    "5m": CACHE_WRITE_5M_MULTIPLIER,
    "1h": CACHE_WRITE_1H_MULTIPLIER,
}


def _today() -> _dt.date:
    return _dt.date.today()


def _base_rates(base_id: str, as_of: _dt.date) -> tuple[tuple[float, float], bool]:
    """Return ((input, output), is_introductory) for a base model id."""
    try:
        rates = BASE_RATES_PER_MTOK[base_id]
    except KeyError as exc:
        raise UnknownModelRateError(
            f"no verified Bedrock rate for {base_id!r}; refusing to guess. "
            f"Known: {sorted(BASE_RATES_PER_MTOK)}"
        ) from exc
    if base_id == BASE_SONNET_5:
        if as_of >= SONNET_5_INTRO_EXPIRY:
            return SONNET_5_STANDARD_RATES, False
        return rates, True
    return rates, False



def _is_mantle(model_id: str) -> bool:
    """True for a model priced by :mod:`agents.mantle` instead of the Claude card."""
    try:
        from agents import mantle
    except Exception:
        return False
    return mantle.is_mantle_model(model_id)


def _mantle_rates(
    model_id: str, *, as_of: _dt.date | None = None, cache_ttl: str = "5m", batch: bool = False
) -> dict[str, Any]:
    """Rate record for a GPT-5.x model, in the same key schema :func:`rates_for` returns.

    The mantle endpoint has no batch inference API and no us./global. variants, so the
    batch discount and regional multiplier are both inert here. ``as_of`` selects the
    rate card in effect on that date (Terra/Luna were repriced 2026-07-30).
    """
    from agents import mantle

    rate_in, rate_cache, rate_out = mantle.rates_for(model_id, as_of=as_of)
    write = rate_in * mantle.CACHE_WRITE_MULTIPLIER.get(model_id, 1.25)
    return {
        "model_id": model_id,
        "base_model_id": model_id,
        "input": rate_in,
        "output": rate_out,
        "cache_read": rate_cache,
        "cache_write_5m": write,
        "cache_write_1h": write,
        "cache_write_applied": write,
        "cache_ttl": cache_ttl,
        "regional_multiplier": 1.0,
        "introductory_pricing": False,
        "introductory_expiry": None,
        "post_introductory_input": rate_in,
        "post_introductory_output": rate_out,
        "batch": False,
        "batch_supported": False,
        "batch_discount_applied": 0.0,
        "batch_excludes_prompt_caching": False,
        "endpoint": "bedrock-mantle",
    }


def rates_for(
    model_id: str,
    *,
    as_of: _dt.date | None = None,
    cache_ttl: str = "5m",
    batch: bool = False,
) -> dict[str, Any]:
    """Full per-category rate record for ``model_id`` in $ per 1M tokens.

    Applies the ``us.*`` regional multiplier when the id carries that prefix,
    the batch discount when requested, and reports whether the input/output
    rates are introductory.
    """
    if cache_ttl not in _CACHE_TTLS:
        raise ValueError(f"cache_ttl must be one of {sorted(_CACHE_TTLS)}, got {cache_ttl!r}")

    # GPT-5.x is served on the bedrock-mantle endpoint with its own published card and
    # no us./global. variants. Delegate rather than duplicate it here, so there is one
    # rate table per model family and neither can drift from the other.
    if _is_mantle(model_id):
        return _mantle_rates(model_id, as_of=as_of, cache_ttl=cache_ttl, batch=batch)

    base_id = base_model_id(model_id)
    (in_rate, out_rate), introductory = _base_rates(base_id, as_of or _today())

    regional = REGIONAL_MULTIPLIER if prefix_of(model_id) == US_PREFIX else 1.0
    if batch:
        if not supports_batch(model_id):
            raise UnsupportedBatchError(
                f"Bedrock does not offer batch inference for {base_id!r}; "
                "no batch price and no batch service quota exist"
            )
        multiplier = regional * BATCH_DISCOUNT
    else:
        multiplier = regional

    scaled_in = in_rate * multiplier
    scaled_out = out_rate * multiplier
    return {
        "model_id": model_id,
        "base_model_id": base_id,
        "input": scaled_in,
        "output": scaled_out,
        "cache_read": scaled_in * CACHE_READ_MULTIPLIER,
        "cache_write_5m": scaled_in * CACHE_WRITE_5M_MULTIPLIER,
        "cache_write_1h": scaled_in * CACHE_WRITE_1H_MULTIPLIER,
        "cache_write_applied": scaled_in * _CACHE_TTLS[cache_ttl],
        "cache_ttl": cache_ttl,
        "regional_multiplier": regional,
        "batch": batch,
        "batch_discount_applied": BATCH_DISCOUNT if batch else None,
        "batch_supported": supports_batch(model_id),
        "batch_excludes_prompt_caching": BATCH_AND_CACHING_ARE_EXCLUSIVE,
        "introductory_pricing": introductory,
        "introductory_expiry": SONNET_5_INTRO_EXPIRY.isoformat() if introductory else None,
        "post_introductory_input": SONNET_5_STANDARD_RATES[0] * multiplier if introductory else None,
        "post_introductory_output": SONNET_5_STANDARD_RATES[1] * multiplier if introductory else None,
    }


def supports_batch(model_id: str) -> bool:
    """Whether Bedrock offers batch inference for ``model_id``."""
    return BATCH_SUPPORTED.get(base_model_id(model_id), False)


def rate_card(*, as_of: _dt.date | None = None) -> list[dict[str, Any]]:
    """Every priced model at both inference-profile prefixes, for display/CI."""
    out: list[dict[str, Any]] = []
    for base_id in BASE_RATES_PER_MTOK:
        for prefix in ("global.", US_PREFIX):
            out.append(rates_for(f"{prefix}{base_id}", as_of=as_of))
    return out


# ---------------------------------------------------------------------------
# Usage -> dollars
# ---------------------------------------------------------------------------

_REQUIRED_USAGE_KEYS: Final[tuple[str, ...]] = ("inputTokens", "outputTokens")
_CACHE_USAGE_KEYS: Final[tuple[str, ...]] = ("cacheReadInputTokens", "cacheWriteInputTokens")


def normalize_usage(usage: Mapping[str, Any] | None) -> dict[str, int]:
    """Validate a Bedrock ``accumulated_usage`` dict into four integer counts.

    ``inputTokens``/``outputTokens`` are required by Bedrock's ``Usage``
    TypedDict; the two cache keys are optional and default to 0 - hence
    ``.get()``, never subscripting. A missing required key raises rather than
    defaulting to zero, because a silent zero is a fabricated cost of $0.
    """
    if not usage:
        raise MissingUsageError(
            "no usage supplied; cost cannot be computed without real token counts "
            "(pass result.metrics.accumulated_usage, or use cost_usd_or_none)"
        )
    missing = [key for key in _REQUIRED_USAGE_KEYS if usage.get(key) is None]
    if missing:
        raise MissingUsageError(f"usage is missing required key(s) {missing}; got keys {sorted(usage)}")
    return {
        "inputTokens": int(usage["inputTokens"]),
        "outputTokens": int(usage["outputTokens"]),
        "cacheReadInputTokens": int(usage.get("cacheReadInputTokens") or 0),
        "cacheWriteInputTokens": int(usage.get("cacheWriteInputTokens") or 0),
    }


def total_input_tokens(usage: Mapping[str, Any]) -> int:
    """Total tokens that entered the model.

    ``usage.inputTokens`` is the NON-cached remainder only, so this is the sum of
    all three input categories. Reporting ``inputTokens`` alone as "input" is the
    single most common caching accounting error.
    """
    counts = normalize_usage(usage)
    return counts["inputTokens"] + counts["cacheReadInputTokens"] + counts["cacheWriteInputTokens"]


def cost_breakdown(
    model_id: str,
    usage: Mapping[str, Any],
    *,
    cache_ttl: str = "5m",
    batch: bool = False,
    as_of: _dt.date | None = None,
) -> dict[str, Any]:
    """Itemised dollar cost of one call, plus the rate metadata behind it."""
    counts = normalize_usage(usage)
    if batch and BATCH_AND_CACHING_ARE_EXCLUSIVE and (
        counts["cacheReadInputTokens"] or counts["cacheWriteInputTokens"]
    ):
        raise UnsupportedBatchError(
            "usage reports cache tokens but batch=True: batch inference and prompt "
            "caching are mutually exclusive on Bedrock, so this request cannot exist"
        )

    rates = rates_for(model_id, as_of=as_of, cache_ttl=cache_ttl, batch=batch)
    per_mtok = 1_000_000.0

    uncached_input = counts["inputTokens"] / per_mtok * rates["input"]
    cache_read = counts["cacheReadInputTokens"] / per_mtok * rates["cache_read"]
    cache_write = counts["cacheWriteInputTokens"] / per_mtok * rates["cache_write_applied"]
    output = counts["outputTokens"] / per_mtok * rates["output"]
    total = uncached_input + cache_read + cache_write + output

    return {
        "model_id": model_id,
        "tier": tier_label(model_id),
        "usage": counts,
        "total_input_tokens": counts["inputTokens"] + counts["cacheReadInputTokens"] + counts["cacheWriteInputTokens"],
        "uncached_input_usd": uncached_input,
        "cache_read_usd": cache_read,
        "cache_write_usd": cache_write,
        "output_usd": output,
        "total_usd": total,
        "rates_per_mtok": rates,
        "introductory_pricing": rates["introductory_pricing"],
        "introductory_expiry": rates["introductory_expiry"],
    }


def cost_usd(
    model_id: str,
    usage: Mapping[str, Any],
    *,
    cache_ttl: str = "5m",
    batch: bool = False,
    as_of: _dt.date | None = None,
) -> float:
    """Dollar cost of one call from its full Bedrock usage dict.

    ``usage`` is ``result.metrics.accumulated_usage`` (or the same shape). There
    is deliberately no ``(input_tokens, output_tokens)`` overload: that signature
    cannot express cached input and silently under-reports once caching is on.
    """
    return cost_breakdown(model_id, usage, cache_ttl=cache_ttl, batch=batch, as_of=as_of)["total_usd"]


def cost_usd_or_none(
    model_id: str,
    usage: Mapping[str, Any] | None,
    *,
    cache_ttl: str = "5m",
    batch: bool = False,
    as_of: _dt.date | None = None,
) -> float | None:
    """Cost, or ``None`` when usage is absent or incomplete.

    For telemetry paths that must not crash on a missing metric. ``None`` means
    "unknown" and must be rendered as unknown - never coerced to 0.0.
    """
    try:
        return cost_usd(model_id, usage or {}, cache_ttl=cache_ttl, batch=batch, as_of=as_of)
    except MissingUsageError:
        return None


# ---------------------------------------------------------------------------
# Tiering comparison
# ---------------------------------------------------------------------------


def compare_tiered_vs_uniform(
    usage_by_role: Mapping[str, Mapping[str, Any]],
    *,
    baseline_model: str = BASELINE_MODEL,
    cache_ttl: str = "5m",
    batch: bool = False,
    as_of: _dt.date | None = None,
) -> dict[str, Any]:
    """Cost of the per-agent tiering vs running every agent on one model.

    ``usage_by_role`` maps a role name (the eight domains plus 'synthesizer') to
    that role's real Bedrock usage dict. Both sides are priced from the *same*
    per-role token counts, so the delta isolates the model choice.

    The saving is decomposed into the only two things that can produce it, because
    quoting the blended figure is how a cost narrative dies:

    * ``structural_saving_usd`` - what the tier *shape* is worth: LIGHT and HEAVY
      assignments priced against the baseline, with the MID tier held at the
      baseline model. On a roughly uniform token mix this is near zero and can be
      exactly zero, because a HEAVY agent's surcharge cancels a LIGHT agent's
      discount. The previous configuration (2 Haiku + 4 Sonnet-4.6 + 2 Opus-4.8 +
      Sonnet-4.6 synthesizer vs an all-Sonnet-4.6 baseline) cancelled to the cent:
      $0.12270/app either way, $0.00 saved, while the README called it the
      differentiator.
    * ``model_saving_usd`` - what the MID *model generation* is worth. With MID on
      Sonnet 5's introductory rate most of this is promotional, and that part goes to
      zero when the rate expires (see ``expiring_saving_usd``).

    NOTE ON THE SYNTHESIZER. The default synthesizer is now GPT-5.6 Luna on the
    bedrock-mantle endpoint, chosen on a measurement (6.1s vs 55.2s) rather than on
    price. Because Luna is also far cheaper per token than any Claude tier, the
    synthesizer row now contributes a real, non-expiring saving that lands in
    ``model_saving_usd`` -- it is a model *substitution*, not the tier shape. The tier
    shape across the eight specialists is still worth ~$0 and
    ``structural_saving_usd`` still isolates it; ``synthesizer_saving_usd`` breaks the
    substitution out so no one attributes it to tiering.

    Any figure quoted to a customer must come from this function, on measured
    usage, with every component shown.
    """
    if not usage_by_role:
        raise MissingUsageError("usage_by_role is empty; a tiering comparison needs real per-role usage")

    per_role: dict[str, dict[str, Any]] = {}
    tiered_total = 0.0
    baseline_total = 0.0
    structural_total = 0.0
    introductory_roles: list[str] = []

    synthesizer_structural = 0.0
    synthesizer_tiered = 0.0

    for role, usage in usage_by_role.items():
        assigned = model_for(role)
        # Hold the MID tier at the baseline model to isolate the tier shape. The
        # synthesizer is held at the baseline too: swapping it to a mantle model is a
        # substitution, not a tier assignment, so letting it into structural_saving_usd
        # would credit the tier shape with a saving it did not produce.
        is_synth = role == "synthesizer"
        if is_synth or tier_label(assigned) == "mid":
            structural_model = baseline_model
        else:
            structural_model = assigned

        tiered = cost_usd(assigned, usage, cache_ttl=cache_ttl, batch=batch, as_of=as_of)
        uniform = cost_usd(baseline_model, usage, cache_ttl=cache_ttl, batch=batch, as_of=as_of)
        structural = cost_usd(structural_model, usage, cache_ttl=cache_ttl, batch=batch, as_of=as_of)

        tiered_total += tiered
        baseline_total += uniform
        structural_total += structural
        if is_synth:
            synthesizer_structural += structural
            synthesizer_tiered += tiered

        rates = rates_for(assigned, as_of=as_of, cache_ttl=cache_ttl, batch=batch)
        if rates["introductory_pricing"]:
            introductory_roles.append(role)

        per_role[role] = {
            "model_id": assigned,
            "tier": tier_label(assigned),
            "tiered_usd": tiered,
            "baseline_usd": uniform,
            "delta_usd": tiered - uniform,
            "introductory_pricing": rates["introductory_pricing"],
        }

    saving = baseline_total - tiered_total
    structural_saving = baseline_total - structural_total
    model_saving = structural_total - tiered_total
    # The part of model_saving that comes from substituting the synthesizer's model
    # family rather than from the MID tier's generation. Non-expiring, unlike the
    # Sonnet 5 introductory component.
    synthesizer_saving = synthesizer_structural - synthesizer_tiered

    # What the same comparison yields once every introductory rate has lapsed.
    post_expiry = None
    if introductory_roles:
        lapsed = compare_tiered_vs_uniform(
            usage_by_role,
            baseline_model=baseline_model,
            cache_ttl=cache_ttl,
            batch=batch,
            as_of=SONNET_5_INTRO_EXPIRY,
        )
        post_expiry = lapsed["saving_usd"]

    return {
        "roles_priced": sorted(per_role),
        "roles_missing": sorted(set(ROLES) - set(per_role)),
        "baseline_model": baseline_model,
        "baseline_usd": baseline_total,
        "tiered_usd": tiered_total,
        "structural_usd": structural_total,
        "saving_usd": saving,
        "synthesizer_saving_usd": synthesizer_saving,
        "synthesizer_model": model_for("synthesizer"),
        "saving_pct": (saving / baseline_total * 100.0) if baseline_total else 0.0,
        "structural_saving_usd": structural_saving,
        "model_saving_usd": model_saving,
        "roles_on_introductory_pricing": sorted(introductory_roles),
        "introductory_expiry": SONNET_5_INTRO_EXPIRY.isoformat() if introductory_roles else None,
        "saving_usd_after_introductory_expiry": post_expiry,
        "expiring_saving_usd": (saving - post_expiry) if post_expiry is not None else 0.0,
        "per_role": per_role,
        "cache_ttl": cache_ttl,
        "batch": batch,
    }


def project_monthly(per_app_cost: float, apps_per_day: int) -> dict[str, Any]:
    """Project daily and monthly cost from a measured per-application cost.

    ``per_app_cost`` must come from :func:`cost_usd` on real usage; this function
    only multiplies.
    """
    if per_app_cost is None:
        raise MissingUsageError("per_app_cost is None (unknown usage); cannot project spend")
    daily = per_app_cost * apps_per_day
    return {
        "per_app_usd": per_app_cost,
        "apps_per_day": apps_per_day,
        "daily_usd": daily,
        "monthly_usd": daily * 30,
    }


# Re-exported so callers can price a tier without importing two modules.
_TIER_MODELS: Final[tuple[str, ...]] = (LIGHT_MODEL, MID_MODEL, HEAVY_MODEL, SYNTHESIZER_MODEL)
