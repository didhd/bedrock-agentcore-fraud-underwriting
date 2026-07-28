#!/usr/bin/env python3
"""
The measurement harness. This module is the ONLY place in the repo allowed to
produce a latency or cost figure for the customer.

WHY IT EXISTS: the pre-port demo computed its headline claim ("26s sequential ->
8.5s parallel") from a hand-written ``NOMINAL_LATENCY_S`` dict and then printed
``time.sleep(nominal * 0.05)`` next to it as "measured wall-clock". Per-agent
cost came from a hard-coded ``NOMINAL_TOKENS`` table. Every one of those numbers
was invented. Point Predictive's own engineer will ask where a number came from,
so the answer has to be "this run measured it, here is the sample count, the git
sha, the region and the model id" -- or "we did not measure that".

Two modes, and the difference is load-bearing:

  --offline   Measures ONLY this repo's orchestration overhead: payload
              projection, prompt assembly, asyncio fan-out scheduling, and
              output-contract validation. NO model is called, so NO model
              latency is measured. Every report says so in the header, in the
              per-row label and in the JSON ``caveats``.

  --live      Real Amazon Bedrock calls. Double-gated: the ``--live`` flag AND
              the ``AGENTCORE_BENCH_ALLOW_LIVE=1`` environment variable. Prints
              the model ids and an upper-bound spend estimate before it calls
              anything, and defaults to a small sample.

Percentiles are refused below a documented minimum sample count (see
``PERCENTILE_MIN_SAMPLES``): a p99 computed from one sample is not a p99, and
printing one to a customer is the same class of error as inventing the number.

Usage
-----
  python -m evals.bench --offline
  python -m evals.bench --offline --concurrency 1,4,16 --iterations 40
  python -m evals.bench --offline --json evals/last_offline_run.json

  AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.bench --live --iterations 3
  AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.bench --live --dry-run   # price only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prompts.loader import DOMAINS, MASTER_SYNTHESIS_PROMPT, SPECIALIST_PROMPTS  # noqa: E402

# ---------------------------------------------------------------------------
# Statistics policy
# ---------------------------------------------------------------------------

#: Minimum sample count required before a percentile may be printed.
#:
#: The rule is 1/(1-p) -- the smallest sample in which the requested tail can
#: actually be observed. You cannot see a 1-in-100 event in 20 samples, so a
#: "p99" over 20 samples is really the maximum wearing a percentile's name.
#: p50 keeps a practical floor of 5 rather than the arithmetic 2, because a
#: three-sample median is not a number worth showing a customer either.
PERCENTILE_MIN_SAMPLES: dict[str, int] = {"p50": 5, "p95": 20, "p99": 100}

#: How percentiles are computed, recorded in the output so the number is
#: reproducible. Nearest-rank (no interpolation) never reports a value that was
#: not actually observed, which matters most at exactly the small sample sizes
#: this harness runs at.
PERCENTILE_METHOD = "nearest-rank, inclusive (no interpolation); ceil(p*n)-th of the sorted samples"


# ---------------------------------------------------------------------------
# Model / pricing resolution
#
# agents.pricing and agents.models own the real rate table and the cache
# minimums. This module binds to them when they expose the richer contract and
# otherwise falls back to the same published figures, recording which source it
# used in the report so a stale fallback cannot masquerade as the real table.
# ---------------------------------------------------------------------------

#: $ per 1M tokens for the *global.* inference profile, keyed by model family.
#: (input, output). Source: Anthropic published Bedrock rates. sonnet-5's 2/10
#: is INTRODUCTORY and reverts to 3/15 on 2026-09-01, which is why the reverted
#: pair is carried alongside rather than being silently baked in.
_FALLBACK_GLOBAL_RATES: dict[str, tuple[float, float]] = {
    "haiku-4-5": (1.0, 5.0),
    "sonnet-5": (2.0, 10.0),
    "sonnet-4-6": (3.0, 15.0),
    "opus-5": (5.0, 25.0),
    "opus-4-8": (5.0, 25.0),
}

#: sonnet-5 rate after the introductory period ends.
SONNET_5_POST_INTRO_RATES: tuple[float, float] = (3.0, 15.0)
SONNET_5_INTRO_ENDS = "2026-09-01"

#: Cache-read is 0.1x the input rate; cache-write is 1.25x (5-minute TTL).
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

#: The ``us.*`` cross-region inference profile costs exactly 1.10x ``global.*``.
US_PROFILE_MULTIPLIER = 1.10

#: Minimum cacheable prompt prefix, in tokens, keyed by model family.
#:
#: This is the trap the report has to name out loud: the minimum is NOT
#: monotonic across generations. A ~1800-token specialist prompt caches fine on
#: Opus 5 (512) and Sonnet 5 / Sonnet 4.6 / Opus 4.8 (1024) and *silently never
#: caches* on Haiku 4.5 (4096) -- no error, no warning, just a permanent 0% hit
#: rate. Showing a customer "cache hit rate: 0%" without that sentence invites
#: them to conclude the caching is broken.
_FALLBACK_MIN_CACHEABLE_PREFIX: dict[str, int] = {
    "opus-5": 512,
    "opus-4-8": 1024,
    "sonnet-5": 1024,
    "sonnet-4-6": 1024,
    "haiku-4-5": 4096,
}


def model_family(model_id: str) -> str:
    """Strip the inference-profile prefix and any date/version suffix.

    ``us.anthropic.claude-haiku-4-5-20251001-v1:0`` -> ``haiku-4-5``.
    Returns the longest matching known family so ``sonnet-4-6`` is not mistaken
    for ``sonnet-5``.
    """
    text = model_id.rsplit(":", 1)[0]
    for family in sorted(_FALLBACK_GLOBAL_RATES, key=len, reverse=True):
        if family in text:
            return family
    return text


def profile_multiplier(model_id: str) -> float:
    """1.10 for a ``us.*`` inference profile, 1.0 for ``global.*`` / bare ids."""
    return US_PROFILE_MULTIPLIER if model_id.startswith("us.") else 1.0


@dataclass(frozen=True)
class RateCard:
    """Per-1M-token rates actually applied to one model, and where they came from."""

    model_id: str
    family: str
    profile: str
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float
    source: str
    note: str = ""


def rate_card(model_id: str) -> RateCard | None:
    """Resolve the rate card for a model id, or None if it has no verified rate.

    ``agents.pricing`` owns the published rate card and is asked first; it
    already applies the ``us.*`` regional multiplier, the cache multipliers, and
    the Sonnet-5 introductory expiry. The local table is a fallback for a model
    that module has no entry for, and never overrides it.

    None is deliberate: a model with no verified rate must make cost come back as
    ``None`` rather than quietly borrowing another model's price.
    """
    try:
        from agents import pricing as _pricing  # noqa: PLC0415

        rates = _pricing.rates_for(model_id)
        note = ""
        if rates.get("introductory_pricing"):
            note = (
                f"introductory rate; reverts to "
                f"{rates['post_introductory_input']:g}/"
                f"{rates['post_introductory_output']:g} per 1M on "
                f"{rates['introductory_expiry']}"
            )
        return RateCard(
            model_id=model_id,
            family=model_family(model_id),
            profile="us" if model_id.startswith("us.") else "global",
            input_per_mtok=float(rates["input"]),
            output_per_mtok=float(rates["output"]),
            cache_read_per_mtok=float(rates["cache_read"]),
            cache_write_per_mtok=float(rates["cache_write_applied"]),
            source="agents.pricing.rates_for",
            note=note,
        )
    except Exception:
        # Either agents.pricing is unavailable, or it refused this model
        # (UnknownModelRateError). Fall through to the documented table.
        pass

    family = model_family(model_id)
    base = _FALLBACK_GLOBAL_RATES.get(family)
    if base is None:
        return None
    multiplier = profile_multiplier(model_id)
    in_rate = base[0] * multiplier
    out_rate = base[1] * multiplier
    note = ""
    if family == "sonnet-5":
        note = (
            f"introductory rate; reverts to "
            f"{SONNET_5_POST_INTRO_RATES[0]:g}/{SONNET_5_POST_INTRO_RATES[1]:g} "
            f"per 1M on {SONNET_5_INTRO_ENDS}"
        )
    return RateCard(
        model_id=model_id,
        family=family,
        profile="us" if model_id.startswith("us.") else "global",
        input_per_mtok=in_rate,
        output_per_mtok=out_rate,
        cache_read_per_mtok=in_rate * CACHE_READ_MULTIPLIER,
        cache_write_per_mtok=in_rate * CACHE_WRITE_MULTIPLIER,
        source="evals.bench._FALLBACK_GLOBAL_RATES (published rates)",
        note=note,
    )


def min_cacheable_prefix_tokens(model_id: str) -> int | None:
    """Minimum prompt prefix, in tokens, that this model will cache at all.

    ``agents.models`` owns this table; this defers to its ``min_cacheable_tokens``
    so the two cannot disagree. The local fallback only applies if that helper
    has no entry for the model, and never overrides it.
    """
    try:
        from agents.models import min_cacheable_tokens  # noqa: PLC0415

        found = min_cacheable_tokens(model_id)
        if found is not None:
            return int(found)
    except Exception:  # pragma: no cover - agents/ is importable in this repo
        pass
    return _FALLBACK_MIN_CACHEABLE_PREFIX.get(model_family(model_id))


# ---------------------------------------------------------------------------
# Usage -> cost
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsage:
    """One call's real token usage, exactly as Bedrock reports it.

    ``input_tokens`` is Bedrock's ``inputTokens``, which counts ONLY the
    non-cached prompt tokens. The full prompt is therefore
    ``input_tokens + cache_read_tokens + cache_write_tokens``; using
    ``input_tokens`` alone as "prompt size" under-reports a cached call, which
    is the mistake that makes caching look like it did nothing.
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def cache_hit(self) -> bool:
        return self.cache_read_tokens > 0


def usage_from_accumulated(accumulated: Any) -> TokenUsage | None:
    """Build a TokenUsage from ``result.metrics.accumulated_usage``.

    ``inputTokens``/``outputTokens`` are required. The two cache fields are
    OPTIONAL in the Strands/Bedrock payload -- they are absent entirely on a
    non-cached call -- so they are read with ``.get`` and default to 0.
    Returns None when the required fields are missing, so a caller cannot end up
    charging a call it never measured.
    """
    if accumulated is None:
        return None
    if not hasattr(accumulated, "get"):
        return None
    raw_in = accumulated.get("inputTokens")
    raw_out = accumulated.get("outputTokens")
    if raw_in is None or raw_out is None:
        return None
    return TokenUsage(
        input_tokens=int(raw_in),
        output_tokens=int(raw_out),
        cache_read_tokens=int(accumulated.get("cacheReadInputTokens", 0) or 0),
        cache_write_tokens=int(accumulated.get("cacheWriteInputTokens", 0) or 0),
    )


def cost_usd(model_id: str, usage: TokenUsage | None) -> float | None:
    """Dollar cost of one call from its REAL measured usage.

    Returns ``None`` -- never a number -- when usage is missing or the model's
    rate card cannot be resolved. A missing measurement has to stay visibly
    missing all the way to the report; substituting a plausible figure here is
    exactly how the old ``NOMINAL_TOKENS`` table got into the demo.
    """
    if usage is None:
        return None
    card = rate_card(model_id)
    if card is None:
        return None
    return (
        usage.input_tokens / 1_000_000 * card.input_per_mtok
        + usage.output_tokens / 1_000_000 * card.output_per_mtok
        + usage.cache_read_tokens / 1_000_000 * card.cache_read_per_mtok
        + usage.cache_write_tokens / 1_000_000 * card.cache_write_per_mtok
    )


# ---------------------------------------------------------------------------
# Prompt cacheability
# ---------------------------------------------------------------------------


def cacheability(model_id: str, prompt_tokens: int | None) -> dict[str, Any]:
    """Can this prompt cache on this model at all?

    Separate from "did it cache". A 0% hit rate has two completely different
    causes -- a cold cache, or a prompt below the model's minimum cacheable
    prefix, where no number of repeat calls will ever produce a hit. The report
    must distinguish them.

    ``prompt_tokens`` is None when this run did not measure a token count
    (offline runs make no Bedrock call, so they cannot); the verdict is then
    ``unknown``, not a guess.
    """
    minimum = min_cacheable_prefix_tokens(model_id)
    result: dict[str, Any] = {
        "model_id": model_id,
        "model_family": model_family(model_id),
        "min_cacheable_prefix_tokens": minimum,
        "prompt_tokens": prompt_tokens,
    }
    if minimum is None:
        result["verdict"] = "unknown"
        result["explanation"] = (
            f"no minimum-cacheable-prefix figure on record for {model_family(model_id)}; "
            "cannot say whether this prompt is cacheable"
        )
        return result
    if prompt_tokens is None:
        result["verdict"] = "unknown"
        result["explanation"] = (
            f"prompt token count not measured in this run; {model_family(model_id)} "
            f"requires >= {minimum} tokens before it caches anything"
        )
        return result
    if prompt_tokens < minimum:
        result["verdict"] = "never_cacheable"
        result["explanation"] = (
            f"prompt is {prompt_tokens} tokens but {model_family(model_id)} caches "
            f"only prefixes of >= {minimum} tokens. This prompt will NEVER cache on "
            "this model: a 0% hit rate here is the documented behaviour, not a "
            "misconfiguration and not a cold cache."
        )
        return result
    result["verdict"] = "cacheable"
    result["explanation"] = (
        f"prompt is {prompt_tokens} tokens, at or above {model_family(model_id)}'s "
        f"{minimum}-token minimum, so a repeat call can hit the cache"
    )
    return result


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


@dataclass
class CallMeasurement:
    """One specialist or synthesis invocation, as measured."""

    agent: str
    model_id: str | None
    wall_ms: float
    #: Bedrock's own ``result.metrics.accumulated_metrics['latencyMs']``. None
    #: offline (no call was made) and None if the SDK omitted it.
    reported_latency_ms: float | None = None
    #: Time to first streamed token. Only observable on a streaming live call.
    ttft_ms: float | None = None
    usage: TokenUsage | None = None
    cost_usd: float | None = None
    cold: bool = False
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "model_id": self.model_id,
            "wall_ms": round(self.wall_ms, 3),
            "reported_latency_ms": self.reported_latency_ms,
            "ttft_ms": round(self.ttft_ms, 3) if self.ttft_ms is not None else None,
            "usage": asdict(self.usage) if self.usage else None,
            "total_input_tokens": self.usage.total_input_tokens if self.usage else None,
            "cache_hit": self.usage.cache_hit if self.usage else None,
            "cost_usd": self.cost_usd,
            "cold": self.cold,
            "error": self.error,
        }


@dataclass
class IterationMeasurement:
    """One end-to-end adjudication: 8 specialists fanned out, then synthesis."""

    application_id: str
    concurrency: int
    iteration: int
    cold: bool
    e2e_wall_ms: float
    calls: list[CallMeasurement] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "concurrency": self.concurrency,
            "iteration": self.iteration,
            "cold": self.cold,
            "e2e_wall_ms": round(self.e2e_wall_ms, 3),
            "calls": [c.to_json() for c in self.calls],
        }


def nearest_rank_percentile(sorted_values: Sequence[float], p: float) -> float:
    """ceil(p*n)-th value of an ascending sample. Never interpolates."""
    n = len(sorted_values)
    rank = max(1, math.ceil(p * n))
    return sorted_values[min(rank, n) - 1]


def summarize_latencies(values: Iterable[float], label: str = "") -> dict[str, Any]:
    """p50/p95/p99 of a sample, refusing any percentile the sample cannot support.

    A suppressed percentile comes back as ``None`` with its reason recorded in
    ``suppressed``. Callers render "n/a (need N samples)" rather than a number.
    """
    samples = sorted(float(v) for v in values)
    n = len(samples)
    out: dict[str, Any] = {
        "label": label,
        "n": n,
        "method": PERCENTILE_METHOD,
        "min_ms": round(samples[0], 3) if n else None,
        "max_ms": round(samples[-1], 3) if n else None,
        "mean_ms": round(statistics.fmean(samples), 3) if n else None,
        "p50_ms": None,
        "p95_ms": None,
        "p99_ms": None,
        "suppressed": {},
    }
    for name, p in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
        need = PERCENTILE_MIN_SAMPLES[name]
        if n >= need:
            out[f"{name}_ms"] = round(nearest_rank_percentile(samples, p), 3)
        else:
            out["suppressed"][name] = (
                f"refused: {n} sample(s), need >= {need}. A {name} over {n} "
                f"sample(s) is not a {name}."
            )
    return out


# ---------------------------------------------------------------------------
# Run environment provenance
# ---------------------------------------------------------------------------


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except Exception:
        return None


def _package_versions() -> dict[str, str]:
    import importlib.metadata as md

    out: dict[str, str] = {}
    for name in ("strands-agents", "bedrock-agentcore", "boto3", "pydantic"):
        try:
            out[name] = md.version(name)
        except Exception:
            out[name] = "not-installed"
    return out


def run_environment(mode: str, region: str, model_ids: dict[str, str]) -> dict[str, Any]:
    """Everything needed to reproduce or discredit this run's numbers."""
    dirty = _git("status", "--porcelain")
    return {
        "mode": mode,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(dirty) if dirty is not None else None,
        "aws_region": region,
        "model_ids": model_ids,
        "host": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "packages": _package_versions(),
    }


# ---------------------------------------------------------------------------
# Workload: payloads, prompts, models
# ---------------------------------------------------------------------------


def resolve_application_source() -> tuple[Callable[[], list[str]], Callable[[str], Any], str]:
    """Bind to fixtures/ if it has landed, else the older data/applications.py.

    Which source was used is recorded in the report: an offline overhead number
    measured against a 5-record module is not the same measurement as one taken
    against the full fixture set, and the reader is entitled to know which.
    """
    try:
        from fixtures import loader as _fx  # noqa: PLC0415

        return _fx.list_application_ids, _fx.get_application, "fixtures.loader"
    except Exception:
        from data import applications as _apps  # noqa: PLC0415

        return _apps.list_application_ids, _apps.get_application, "data.applications"


def resolve_specialist_models() -> dict[str, str]:
    """Per-domain model assignment from agents.models, plus the synthesizer."""
    from agents import models as _models  # noqa: PLC0415

    assigned = dict(getattr(_models, "SPECIALIST_MODELS", {}))
    out = {d: assigned.get(d, getattr(_models, "MID_MODEL", "unknown")) for d in DOMAINS}
    out["synthesis"] = getattr(_models, "SYNTHESIZER_MODEL", out[DOMAINS[0]])
    return out


def render_payload(application: Any, domain: str | None = None) -> str:
    """Project one application into the text a specialist actually receives.

    Uses the signal layer's own projection when it is available so the measured
    prompt-assembly cost is the real one, and falls back to a deterministic JSON
    dump otherwise.

    ``domain`` matters: the projected payload is what bounds a specialist to its own
    signals, and the projected size is what decides cacheability (an unprojected
    whole-record dump is ~3x larger and would make every tier look cacheable). It
    defaults to the first domain only so a caller measuring a single representative
    payload keeps working; per-domain callers should pass their domain.
    """
    target = domain or DOMAINS[0]
    try:
        from signal_layer import render_for_agent  # noqa: PLC0415
        from fixtures.loader import build_payload  # noqa: PLC0415

        app_id = application if isinstance(application, str) else (
            application.get("application_id") or application.get("record_id")
            if isinstance(application, dict)
            else None
        )
        if app_id:
            return render_for_agent(build_payload(app_id), target)
    except Exception:
        pass
    return json.dumps(application, sort_keys=True, indent=2, default=str)


def build_specialist_prompt(domain: str, payload_text: str) -> str:
    """System prompt + payload, assembled the way the runtime assembles it."""
    return f"{SPECIALIST_PROMPTS[domain]}\n\nAPPLICATION DATA:\n{payload_text}"


def build_synthesis_prompt(analyses: dict[str, str]) -> str:
    """Substitute the eight ``{<domain>_analysis}`` placeholders, verbatim prompt."""
    prompt = MASTER_SYNTHESIS_PROMPT
    for domain in DOMAINS:
        prompt = prompt.replace(f"{{{domain}_analysis}}", analyses.get(domain, ""))
    return prompt


# ---------------------------------------------------------------------------
# Offline mode: orchestration overhead only
# ---------------------------------------------------------------------------

_OFFLINE_STUB_ANALYSIS = (
    "OFFLINE STUB. No model was called. This string stands in for a specialist "
    "analysis so the orchestration path can be measured end to end."
)


async def _offline_specialist(domain: str, payload_text: str, model_id: str, cold: bool) -> CallMeasurement:
    """Measure the real per-specialist orchestration work, minus the model call."""
    start = time.perf_counter()
    prompt = build_specialist_prompt(domain, payload_text)
    # The work a real call would do around the model: assemble the prompt and
    # validate the shape of what comes back. No sleep, no simulated latency.
    _ = len(prompt)
    analysis = _OFFLINE_STUB_ANALYSIS
    _ = analysis.strip().splitlines()
    wall_ms = (time.perf_counter() - start) * 1000.0
    return CallMeasurement(
        agent=domain,
        model_id=model_id,
        wall_ms=wall_ms,
        reported_latency_ms=None,
        ttft_ms=None,
        usage=None,  # nothing was tokenized: cost must come back None, not 0.0
        cost_usd=None,
        cold=cold,
    )


async def _offline_iteration(
    application_id: str,
    application: Any,
    models: dict[str, str],
    concurrency: int,
    iteration: int,
    cold: bool,
) -> IterationMeasurement:
    payload_text = render_payload(application if not isinstance(application, dict) else application)
    e2e_start = time.perf_counter()
    # asyncio.gather is the real fan-out primitive (a Strands Agent raises
    # ConcurrencyException if reused concurrently, and ThreadPoolExecutor is the
    # wrong tool here), so the scheduling cost measured is the production one.
    calls = list(
        await asyncio.gather(
            *(_offline_specialist(d, payload_text, models[d], cold) for d in DOMAINS)
        )
    )
    syn_start = time.perf_counter()
    prompt = build_synthesis_prompt({d: _OFFLINE_STUB_ANALYSIS for d in DOMAINS})
    _ = len(prompt)
    syn_wall = (time.perf_counter() - syn_start) * 1000.0
    calls.append(
        CallMeasurement(
            agent="synthesis",
            model_id=models["synthesis"],
            wall_ms=syn_wall,
            usage=None,
            cost_usd=None,
            cold=cold,
        )
    )
    e2e_wall = (time.perf_counter() - e2e_start) * 1000.0
    return IterationMeasurement(
        application_id=application_id,
        concurrency=concurrency,
        iteration=iteration,
        cold=cold,
        e2e_wall_ms=e2e_wall,
        calls=calls,
    )


async def run_offline(
    concurrency_levels: Sequence[int],
    iterations: int,
    application_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Measure orchestration overhead. NO model latency is measured."""
    list_ids, get_app, source = resolve_application_source()
    ids = list(application_ids or list_ids())
    if not ids:
        raise RuntimeError(f"{source} returned no application ids; nothing to measure")
    models = resolve_specialist_models()

    measurements: list[IterationMeasurement] = []
    for level in concurrency_levels:
        for i in range(iterations):
            cold = i == 0
            batch_ids = [ids[(i * level + k) % len(ids)] for k in range(level)]
            batch = await asyncio.gather(
                *(
                    _offline_iteration(app_id, get_app(app_id), models, level, i, cold)
                    for app_id in batch_ids
                )
            )
            measurements.extend(batch)

    env = run_environment("offline", region="n/a (no AWS calls made)", model_ids=models)
    return build_report(
        env=env,
        measurements=measurements,
        models=models,
        application_source=source,
        applications=ids,
        measured=[
            "this repo's orchestration overhead: signal-layer payload projection, "
            "verbatim prompt assembly, asyncio.gather fan-out scheduling, and "
            "synthesis-prompt substitution",
            "wall-clock per specialist and end to end, at each requested concurrency",
        ],
        not_measured=[
            "MODEL LATENCY IS NOT MEASURED IN OFFLINE MODE. No Amazon Bedrock call "
            "was made, so no per-agent inference latency, no time-to-first-token, "
            "and no end-to-end adjudication latency appears in this report.",
            "token usage and therefore cost: nothing was tokenized, so every "
            "cost field is null rather than 0.0",
            "prompt-cache behaviour: with no call there is no cache read, write, "
            "hit or miss to observe",
            "prompt token counts, so the minimum-cacheable-prefix check reports "
            "'unknown' rather than a verdict",
        ],
        cacheability_reports=[cacheability(m, None) for m in sorted(set(models.values()))],
        extra_caveats=[
            "Offline wall-clock numbers describe THIS HOST, not the AgentCore "
            "runtime. They are useful for spotting orchestration regressions and "
            "for nothing else.",
            "Do not present any offline number to the customer as an "
            "application-processing time.",
        ],
    )


# ---------------------------------------------------------------------------
# Live mode: real Amazon Bedrock
# ---------------------------------------------------------------------------

LIVE_ENV_GATE = "AGENTCORE_BENCH_ALLOW_LIVE"
LIVE_DEFAULT_ITERATIONS = 2

#: MEASURED: a 1200-token cap truncated the bustout and rings analyses with
#: MaxTokensReachedException (bustout alone emitted 1057 output tokens, and the
#: customer's prompts allow POSSIBLE/HIGH analyses up to 4000 characters). A cap
#: that truncates does not just lose the analysis -- it produces no usage, so the
#: call goes unpriced and the adjudication is excluded from the per-application
#: cost. 4000 is the smallest default that completed a call for every specialist.
LIVE_DEFAULT_MAX_TOKENS = 4000


def live_gate_error(args_live: bool) -> str | None:
    """Why live mode may not run, or None if both gates are satisfied."""
    if not args_live:
        return "live mode not requested (pass --live)"
    if os.environ.get(LIVE_ENV_GATE) != "1":
        return (
            f"--live also requires {LIVE_ENV_GATE}=1 in the environment. "
            "Two gates, deliberately: --live alone is one keystroke away from a "
            "billed run in someone's account."
        )
    return None


def count_prompt_tokens(model_id: str, prompt: str, region: str) -> int | None:
    """Real prompt token count via Bedrock CountTokens, or None if unavailable.

    MEASURED BEHAVIOUR (us-east-1, boto3 1.43.54): CountTokens rejects an
    inference-profile prefix. Both ``global.anthropic.claude-haiku-4-5-...`` and
    ``us.anthropic.claude-haiku-4-5-...`` fail with
    ``ValidationException: The provided model doesn't support counting tokens``,
    while the bare ``anthropic.claude-haiku-4-5-...`` succeeds. The prefix is
    therefore stripped before the call -- token counts are a property of the
    model, not of the routing profile, so this changes nothing about the answer.

    CountTokens is a free metering call: it runs no inference and costs nothing,
    which is why the ``--dry-run`` spend estimate is allowed to use it.

    ALSO MEASURED: CountTokens is not offered for every model. In us-east-1 on
    2026-07-28, ``anthropic.claude-haiku-4-5-20251001-v1:0`` returns a count,
    while ``anthropic.claude-sonnet-5`` and ``anthropic.claude-opus-5`` both
    raise ``ValidationException: The provided model doesn't support counting
    tokens``. So the pre-run spend estimate can be priced exactly for the Haiku
    specialists and comes back ``None`` -- explicitly unknown -- for the rest.
    That is the intended outcome: a partial estimate that says which parts are
    missing beats a complete-looking one built on a char/4 guess.

    Returning None keeps the spend estimate honestly labelled as unknown rather
    than back-filling an approximation.
    """
    try:
        import boto3  # noqa: PLC0415

        try:
            from agents.models import base_model_id  # noqa: PLC0415

            bare = base_model_id(model_id)
        except Exception:
            bare = model_id.split(".", 1)[1] if model_id.startswith(("us.", "global.")) else model_id

        client = boto3.client("bedrock-runtime", region_name=region)
        response = client.count_tokens(
            modelId=bare,
            input={"converse": {"messages": [{"role": "user", "content": [{"text": prompt}]}]}},
        )
        return int(response["inputTokens"])
    except Exception:
        return None


def estimate_live_spend(
    models: dict[str, str],
    prompts: dict[str, str],
    region: str,
    iterations: int,
    concurrency_levels: Sequence[int],
    max_tokens: int,
) -> dict[str, Any]:
    """Upper-bound spend for a live run, before anything is called.

    Input side uses a REAL CountTokens measurement per model. Output side uses
    ``max_tokens``, which is a true ceiling rather than a guess at how much the
    model will actually write, so the total is an upper bound and is labelled as
    one. If CountTokens is unavailable the input side reports null and the
    estimate says so instead of inventing a number.
    """
    calls_per_iteration = len(DOMAINS) + 1
    total_calls = sum(iterations * level * calls_per_iteration for level in concurrency_levels)
    per_model: dict[str, Any] = {}
    upper_bound = 0.0
    complete = True
    for agent, model_id in models.items():
        tokens = count_prompt_tokens(model_id, prompts.get(agent, ""), region)
        card = rate_card(model_id)
        agent_calls = sum(iterations * level for level in concurrency_levels)
        entry: dict[str, Any] = {
            "model_id": model_id,
            "measured_prompt_tokens": tokens,
            "calls": agent_calls,
            "max_tokens_per_call": max_tokens,
        }
        if tokens is None or card is None:
            complete = False
            entry["upper_bound_usd"] = None
            entry["note"] = (
                "NOT estimated. Either this model has no verified rate, or Bedrock "
                "CountTokens is not offered for it -- measured in us-east-1: "
                "haiku-4-5 supports CountTokens, sonnet-5 and opus-5 return "
                "ValidationException 'The provided model doesn't support counting "
                "tokens'. No approximation is substituted."
            )
        else:
            cost = agent_calls * (
                tokens / 1_000_000 * card.input_per_mtok
                + max_tokens / 1_000_000 * card.output_per_mtok
            )
            entry["upper_bound_usd"] = round(cost, 6)
            upper_bound += cost
        per_model[agent] = entry
    return {
        "basis": (
            "input side = real Bedrock CountTokens measurement per prompt; output "
            "side = max_tokens ceiling, not a prediction of actual output length"
        ),
        "is_upper_bound": True,
        "complete": complete,
        "total_calls": total_calls,
        "total_upper_bound_usd": round(upper_bound, 4) if complete else None,
        "per_agent": per_model,
    }


async def _live_specialist(
    domain: str,
    prompt: str,
    model_id: str,
    max_tokens: int,
    region: str,
    cold: bool,
) -> CallMeasurement:
    """One real streaming Bedrock call, measured.

    A fresh Agent per invocation: a Strands Agent raises ConcurrencyException if
    the same instance is invoked concurrently.
    """
    from strands import Agent  # noqa: PLC0415
    from strands.models import BedrockModel  # noqa: PLC0415

    start = time.perf_counter()
    ttft_ms: float | None = None
    reported: float | None = None
    usage: TokenUsage | None = None
    error: str | None = None
    try:
        agent = Agent(
            model=BedrockModel(
                model_id=model_id,
                region_name=region,
                max_tokens=max_tokens,
            ),
            system_prompt=SPECIALIST_PROMPTS[domain],
            callback_handler=None,
        )
        async for event in agent.stream_async(prompt):
            if ttft_ms is None and event.get("data"):
                ttft_ms = (time.perf_counter() - start) * 1000.0
            result = event.get("result")
            if result is not None:
                metrics = getattr(result, "metrics", None)
                usage = usage_from_accumulated(getattr(metrics, "accumulated_usage", None))
                accumulated = getattr(metrics, "accumulated_metrics", None) or {}
                if hasattr(accumulated, "get"):
                    raw = accumulated.get("latencyMs")
                    reported = float(raw) if raw is not None else None
    except Exception as exc:  # a failed call is reported, never silently dropped
        error = f"{type(exc).__name__}: {exc}"
    wall_ms = (time.perf_counter() - start) * 1000.0
    return CallMeasurement(
        agent=domain,
        model_id=model_id,
        wall_ms=wall_ms,
        reported_latency_ms=reported,
        ttft_ms=ttft_ms,
        usage=usage,
        cost_usd=cost_usd(model_id, usage),
        cold=cold,
        error=error,
    )


async def _live_iteration(
    application_id: str,
    payload_text: str,
    models: dict[str, str],
    max_tokens: int,
    region: str,
    concurrency: int,
    iteration: int,
    cold: bool,
) -> IterationMeasurement:
    e2e_start = time.perf_counter()
    calls = list(
        await asyncio.gather(
            *(
                _live_specialist(
                    d,
                    build_specialist_prompt(d, payload_text),
                    models[d],
                    max_tokens,
                    region,
                    cold,
                )
                for d in DOMAINS
            )
        )
    )
    analyses = {c.agent: (c.error or "") for c in calls}
    calls.append(
        await _live_specialist(
            DOMAINS[0],  # system prompt is unused for synthesis; overridden below
            build_synthesis_prompt(analyses),
            models["synthesis"],
            max_tokens,
            region,
            cold,
        )
    )
    calls[-1].agent = "synthesis"
    e2e_wall = (time.perf_counter() - e2e_start) * 1000.0
    return IterationMeasurement(
        application_id=application_id,
        concurrency=concurrency,
        iteration=iteration,
        cold=cold,
        e2e_wall_ms=e2e_wall,
        calls=calls,
    )


async def run_live(
    concurrency_levels: Sequence[int],
    iterations: int,
    region: str,
    max_tokens: int,
    application_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Measure real Bedrock latency and cost. Callers must pass both gates."""
    list_ids, get_app, source = resolve_application_source()
    ids = list(application_ids or list_ids())[: max(concurrency_levels)]
    models = resolve_specialist_models()

    measurements: list[IterationMeasurement] = []
    for level in concurrency_levels:
        for i in range(iterations):
            cold = i == 0
            batch_ids = [ids[(i * level + k) % len(ids)] for k in range(level)]
            batch = await asyncio.gather(
                *(
                    _live_iteration(
                        app_id,
                        render_payload(get_app(app_id)),
                        models,
                        max_tokens,
                        region,
                        level,
                        i,
                        cold,
                    )
                    for app_id in batch_ids
                )
            )
            measurements.extend(batch)

    prompt_tokens: dict[str, int | None] = {}
    for call in (c for m in measurements for c in m.calls):
        if call.usage and call.model_id:
            prompt_tokens.setdefault(call.model_id, call.usage.total_input_tokens)

    env = run_environment("live", region=region, model_ids=models)
    return build_report(
        env=env,
        measurements=measurements,
        models=models,
        application_source=source,
        applications=ids,
        measured=[
            "real Amazon Bedrock per-call wall clock, the SDK-reported latencyMs, "
            "and time-to-first-token from the streaming response",
            "real token usage (inputTokens, outputTokens, and cacheRead/cacheWrite "
            "when the response carried them) and the cost computed from it",
            "prompt-cache hit/miss per call",
        ],
        not_measured=[
            "AgentCore runtime overhead: these calls go straight to Bedrock from "
            "this host, so container cold start, session setup and SSE framing are "
            "NOT included in the end-to-end figures",
            "network latency from the customer's own region or VPC",
        ],
        cacheability_reports=[
            cacheability(m, prompt_tokens.get(m)) for m in sorted(set(models.values()))
        ],
        extra_caveats=[
            f"max_tokens was capped at {max_tokens} for this run; output token "
            "counts and output cost do not describe a full-length production "
            "analysis.",
            "Bedrock latency varies with region and load. A figure from one run is "
            "one run.",
        ],
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def build_report(
    *,
    env: dict[str, Any],
    measurements: list[IterationMeasurement],
    models: dict[str, str],
    application_source: str,
    applications: Sequence[str],
    measured: Sequence[str],
    not_measured: Sequence[str],
    cacheability_reports: Sequence[dict[str, Any]],
    extra_caveats: Sequence[str] = (),
) -> dict[str, Any]:
    """Assemble the machine-readable report the UI and README consume."""
    all_calls = [c for m in measurements for c in m.calls]

    per_agent: dict[str, Any] = {}
    for agent in [*DOMAINS, "synthesis"]:
        calls = [c for c in all_calls if c.agent == agent]
        if not calls:
            continue
        costs = [c.cost_usd for c in calls if c.cost_usd is not None]
        hits = [c.usage.cache_hit for c in calls if c.usage is not None]
        reported = [c.reported_latency_ms for c in calls if c.reported_latency_ms is not None]
        ttfts = [c.ttft_ms for c in calls if c.ttft_ms is not None]
        per_agent[agent] = {
            "model_id": calls[0].model_id,
            "rate_card": asdict(rate_card(calls[0].model_id)) if calls[0].model_id else None,
            "wall_clock": summarize_latencies((c.wall_ms for c in calls), f"{agent} wall clock"),
            "sdk_reported_latency": (
                summarize_latencies(reported, f"{agent} latencyMs")
                if reported
                else {"n": 0, "note": "no SDK-reported latencyMs in this run"}
            ),
            "time_to_first_token": (
                summarize_latencies(ttfts, f"{agent} TTFT")
                if ttfts
                else {"n": 0, "note": "not observable in this run (no streaming model call)"}
            ),
            "cost_usd": {
                "n_priced_calls": len(costs),
                "n_unpriced_calls": len(calls) - len(costs),
                "total": round(sum(costs), 6) if costs else None,
                "mean_per_call": round(statistics.fmean(costs), 6) if costs else None,
                "note": (
                    None
                    if costs
                    else "no real token usage was measured, so cost is null (not zero)"
                ),
            },
            "prompt_cache": {
                "n_calls_with_usage": len(hits),
                "hits": sum(1 for h in hits if h),
                "misses": sum(1 for h in hits if not h),
                "hit_rate": (round(sum(1 for h in hits if h) / len(hits), 4) if hits else None),
                "note": (
                    None
                    if hits
                    else "no call usage observed, so hit rate is null (not 0%)"
                ),
            },
            "errors": [c.error for c in calls if c.error],
        }

    by_concurrency: dict[str, Any] = {}
    for level in sorted({m.concurrency for m in measurements}):
        rows = [m for m in measurements if m.concurrency == level]
        cold = [m.e2e_wall_ms for m in rows if m.cold]
        warm = [m.e2e_wall_ms for m in rows if not m.cold]
        by_concurrency[str(level)] = {
            "n_iterations": len(rows),
            "end_to_end_all": summarize_latencies(
                (m.e2e_wall_ms for m in rows), f"e2e @ concurrency {level}"
            ),
            "end_to_end_cold": (
                summarize_latencies(cold, f"e2e cold @ {level}")
                if cold
                else {"n": 0, "note": "no cold iterations at this concurrency"}
            ),
            "end_to_end_warm": (
                summarize_latencies(warm, f"e2e warm @ {level}")
                if warm
                else {"n": 0, "note": "no warm iterations at this concurrency"}
            ),
        }

    priced = [c.cost_usd for c in all_calls if c.cost_usd is not None]

    # An adjudication is only "complete" if all nine of its calls produced real
    # usage. A per-adjudication cost averaged over runs where a specialist errored
    # is an UNDER-count, and publishing it as the cost of an adjudication would be
    # exactly the kind of quietly-wrong number this harness exists to prevent.
    expected_calls = len(DOMAINS) + 1
    complete = [
        m
        for m in measurements
        if len(m.calls) == expected_calls
        and all(c.usage is not None and c.cost_usd is not None for c in m.calls)
    ]
    complete_cost = [sum(c.cost_usd for c in m.calls) for m in complete]  # type: ignore[misc]
    incomplete_n = len(measurements) - len(complete)
    caveats = [
        *(f"MEASURED: {item}" for item in measured),
        *(f"NOT MEASURED: {item}" for item in not_measured),
        *extra_caveats,
        *(
            [
                f"{incomplete_n} of {len(measurements)} adjudication(s) did not produce "
                f"real usage for all {expected_calls} calls and are EXCLUDED from "
                "cost_per_adjudication_usd. Including them would under-report the cost "
                "of a complete adjudication. See totals.errors and per_agent.*.errors."
            ]
            if incomplete_n
            else []
        ),
        f"Percentiles are suppressed below {PERCENTILE_MIN_SAMPLES} samples; a "
        "suppressed percentile is null with the reason recorded next to it.",
        "Every number in this file was produced by this run. No figure here is "
        "illustrative, nominal, extrapolated or borrowed from a vendor datasheet.",
    ]
    return {
        "schema": "pointpredictive.agentcore.bench/1",
        "run": env,
        "workload": {
            "application_source": application_source,
            "application_ids": list(applications),
            "domains": list(DOMAINS),
            "models": models,
            "calls_per_adjudication": len(DOMAINS) + 1,
        },
        "totals": {
            "iterations": len(measurements),
            "calls": len(all_calls),
            "calls_with_real_usage": sum(1 for c in all_calls if c.usage is not None),
            "errors": sum(1 for c in all_calls if c.error),
            "total_cost_usd": round(sum(priced), 6) if priced else None,
            # Averaged over COMPLETE adjudications only, so a run in which a
            # specialist errored cannot silently deflate the figure.
            "complete_adjudications": len(complete),
            "incomplete_adjudications": incomplete_n,
            "cost_per_adjudication_usd": (
                round(statistics.fmean(complete_cost), 6) if complete_cost else None
            ),
            "cost_per_adjudication_note": (
                None
                if complete_cost
                else (
                    "null: no adjudication had real usage for all "
                    f"{expected_calls} calls, so no per-application cost was measured"
                )
            ),
        },
        "per_agent": per_agent,
        "by_concurrency": by_concurrency,
        "prompt_cacheability": list(cacheability_reports),
        "caveats": caveats,
        "raw_iterations": [m.to_json() for m in measurements],
    }


def _fmt(value: Any, width: int = 9) -> str:
    """Right-align a millisecond figure, keeping sub-millisecond detail.

    Offline overhead lands in the tens of microseconds, so a fixed 1-decimal
    format would print every row as "0.0" and hide a real regression.
    """
    if value is None:
        return "n/a".rjust(width)
    if isinstance(value, float):
        if value >= 100:
            return f"{value:,.0f}".rjust(width)
        if value >= 1:
            return f"{value:,.2f}".rjust(width)
        return f"{value:.4f}".rjust(width)
    return str(value).rjust(width)


def render_human(report: dict[str, Any]) -> str:
    """The table a human reads. Says what was not measured, first and loudly."""
    run = report["run"]
    lines: list[str] = []
    mode = run["mode"].upper()
    lines.append("=" * 78)
    lines.append(f"AgentCore bench -- mode: {mode}")
    if run["mode"] == "offline":
        lines.append("")
        lines.append("  *** OFFLINE MODE: ORCHESTRATION OVERHEAD ONLY. ***")
        lines.append("  *** NO Amazon Bedrock call was made. MODEL LATENCY IS NOT MEASURED. ***")
        lines.append("  *** No token usage, so every cost below is n/a, not zero. ***")
    lines.append("=" * 78)
    lines.append(f"  timestamp : {run['timestamp_utc']}")
    lines.append(f"  git sha   : {run['git_sha']} ({run['git_branch']})"
                 + ("  [WORKING TREE DIRTY]" if run.get("git_dirty") else ""))
    lines.append(f"  region    : {run['aws_region']}")
    lines.append(f"  workload  : {report['workload']['application_source']}, "
                 f"{len(report['workload']['application_ids'])} application(s)")
    lines.append(f"  totals    : {report['totals']['iterations']} iteration(s), "
                 f"{report['totals']['calls']} call(s), "
                 f"{report['totals']['calls_with_real_usage']} with real token usage, "
                 f"{report['totals']['errors']} error(s)")
    lines.append("")

    lines.append("Per-agent wall clock (ms) and cost")
    lines.append(f"  {'agent':<13}{'model':<48}{'n':>5}{'p50':>10}{'p95':>10}{'p99':>10}{'cost $':>12}")
    for agent, data in report["per_agent"].items():
        wall = data["wall_clock"]
        cost = data["cost_usd"]["mean_per_call"]
        # Full model id, never truncated: a clipped id is unverifiable, and the
        # reader needs to see which inference profile the number was priced at.
        model = data["model_id"] or "n/a"
        lines.append(
            f"  {agent:<13}{model:<48}{wall['n']:>5}"
            f"{_fmt(wall['p50_ms'], 10)}{_fmt(wall['p95_ms'], 10)}{_fmt(wall['p99_ms'], 10)}"
            f"{_fmt(cost, 12) if cost is None else format(cost, '.6f').rjust(12)}"
        )
    suppressed = {
        name
        for data in report["per_agent"].values()
        for name in data["wall_clock"]["suppressed"]
    }
    if suppressed:
        lines.append(
            f"  ({', '.join(sorted(suppressed))} suppressed: sample count below "
            f"{ {k: v for k, v in PERCENTILE_MIN_SAMPLES.items() if k in suppressed} })"
        )
    lines.append("")

    lines.append("End-to-end per adjudication (ms), by concurrency")
    lines.append(f"  {'conc':<7}{'iters':>7}{'cold p50':>11}{'warm p50':>11}{'p95':>10}{'p99':>10}")
    for level, data in report["by_concurrency"].items():
        lines.append(
            f"  {level:<7}{data['n_iterations']:>7}"
            f"{_fmt(data['end_to_end_cold'].get('p50_ms'), 11)}"
            f"{_fmt(data['end_to_end_warm'].get('p50_ms'), 11)}"
            f"{_fmt(data['end_to_end_all']['p95_ms'], 10)}"
            f"{_fmt(data['end_to_end_all']['p99_ms'], 10)}"
        )
    lines.append("")

    lines.append("Prompt cache -- can these prompts cache at all?")
    for entry in report["prompt_cacheability"]:
        lines.append(f"  {entry['model_family']:<12} min prefix "
                     f"{str(entry['min_cacheable_prefix_tokens']):>5} tok, "
                     f"prompt {str(entry['prompt_tokens']):>6} tok -> {entry['verdict']}")
        lines.append(f"      {entry['explanation']}")
    lines.append("")

    lines.append("CAVEATS (also in the JSON, verbatim)")
    for caveat in report["caveats"]:
        lines.append(f"  - {caveat}")
    lines.append("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_concurrency(text: str) -> list[int]:
    levels = [int(p) for p in text.split(",") if p.strip()]
    if not levels or any(v < 1 for v in levels):
        raise argparse.ArgumentTypeError("concurrency must be comma-separated positive integers")
    return levels


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.bench",
        description=(
            "Measure this port's latency and cost. --offline measures orchestration "
            "overhead only and labels it as such; --live calls Amazon Bedrock and is "
            f"double-gated on --live plus {LIVE_ENV_GATE}=1."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true", help="no Bedrock calls; overhead only")
    mode.add_argument("--live", action="store_true", help=f"real Bedrock calls; needs {LIVE_ENV_GATE}=1")
    parser.add_argument("--concurrency", type=_parse_concurrency, default=[1, 4, 16],
                        help="comma-separated concurrency levels (default 1,4,16)")
    parser.add_argument("--iterations", type=int, default=None,
                        help=f"iterations per level (offline default 40, live default {LIVE_DEFAULT_ITERATIONS})")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--max-tokens", type=int, default=LIVE_DEFAULT_MAX_TOKENS,
                        help=f"live only: per-call output cap (default {LIVE_DEFAULT_MAX_TOKENS})")
    parser.add_argument("--application", action="append", dest="applications",
                        help="restrict to specific application ids (repeatable)")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="write the machine-readable report here")
    parser.add_argument("--dry-run", action="store_true",
                        help="live only: print models and the spend upper bound, then stop")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.offline:
        iterations = args.iterations if args.iterations is not None else 40
        report = asyncio.run(run_offline(args.concurrency, iterations, args.applications))
    else:
        gate = live_gate_error(args.live)
        if gate:
            print(f"refusing to run live: {gate}", file=sys.stderr)
            return 2
        iterations = args.iterations if args.iterations is not None else LIVE_DEFAULT_ITERATIONS
        models = resolve_specialist_models()
        list_ids, get_app, _ = resolve_application_source()
        sample_id = (args.applications or list_ids())[0]
        payload = render_payload(get_app(sample_id))
        prompts = {d: build_specialist_prompt(d, payload) for d in DOMAINS}
        prompts["synthesis"] = build_synthesis_prompt({d: "" for d in DOMAINS})

        print("LIVE RUN -- this will call Amazon Bedrock and incur charges.")
        print(f"  region     : {args.region}")
        print(f"  iterations : {iterations} per concurrency level {args.concurrency}")
        print(f"  max_tokens : {args.max_tokens}")
        print("  models:")
        for agent, model_id in models.items():
            card = rate_card(model_id)
            rates = (
                f"${card.input_per_mtok:g}/${card.output_per_mtok:g} per 1M ({card.profile})"
                if card
                else "rate card UNKNOWN"
            )
            print(f"    {agent:<13}{model_id:<48}{rates}")
            if card and card.note:
                print(f"    {'':<13}note: {card.note}")
        estimate = estimate_live_spend(
            models, prompts, args.region, iterations, args.concurrency, args.max_tokens
        )
        print(f"  estimated spend (UPPER BOUND, {estimate['total_calls']} calls): "
              f"{'$' + format(estimate['total_upper_bound_usd'], '.4f') if estimate['complete'] else 'UNKNOWN'}")
        print(f"    basis: {estimate['basis']}")
        if not estimate["complete"]:
            print("    at least one model could not be priced; see per_agent notes in --json output")
        if args.dry_run:
            if args.json_path:
                Path(args.json_path).write_text(json.dumps(estimate, indent=2), encoding="utf-8")
                print(f"wrote spend estimate to {args.json_path}")
            return 0
        report = asyncio.run(
            run_live(args.concurrency, iterations, args.region, args.max_tokens, args.applications)
        )
        report["live_spend_estimate_before_run"] = estimate

    print(render_human(report))
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote machine-readable report to {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
