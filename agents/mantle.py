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
"""

from __future__ import annotations

import os
import time
from typing import Any, Final

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

DEFAULT_MANTLE_REGION: Final[str] = "us-east-1"
DEFAULT_EFFORT: Final[str] = "low"


class MantleUnavailable(RuntimeError):
    """The mantle endpoint could not be used (SDK missing, no credentials, no access)."""


def is_mantle_model(model_id: str) -> bool:
    """True for a model that must go through bedrock-mantle rather than Converse."""
    return model_id.startswith("openai.gpt-5")


def resolve_mantle_region(model_id: str, region: str | None = None) -> str:
    """A region where this variant is actually available.

    Falls back to the model's first supported region rather than failing, because a
    stack deployed outside the model's footprint can still call it cross-region (the
    request leaves the deployment region, which is worth knowing but not fatal).
    """
    requested = region or os.environ.get("BEDROCK_MANTLE_REGION") or os.environ.get(
        "AWS_REGION"
    ) or DEFAULT_MANTLE_REGION
    allowed = MANTLE_REGIONS.get(model_id)
    if allowed and requested not in allowed:
        return allowed[0]
    return requested


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
        base_url=f"https://bedrock-mantle.{region}.api.aws/openai/v1",
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


__all__ = [
    "DEFAULT_EFFORT",
    "DEFAULT_MANTLE_REGION",
    "MANTLE_EFFORTS",
    "MANTLE_RATES_PER_MTOK",
    "MANTLE_REGIONS",
    "MantleUnavailable",
    "complete",
    "cost_usd",
    "is_mantle_model",
    "rates_for",
    "resolve_mantle_region",
]
