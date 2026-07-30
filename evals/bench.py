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

  python -m evals.bench --pipeline --mock --repetitions 1        # no spend, shape only
  AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.bench --pipeline --repetitions 3

``--pipeline`` is the mode that answers the customer's actual question. ``--live``
above re-implements the fan-out inside this module, which makes it a measurement of
*a* fan-out rather than of the shipped one; ``--pipeline`` calls
``agents.fanout.fan_out`` and ``agents.synthesize.synthesize`` directly, so what it
times is the code path AgentCore runs, including the mantle synthesizer, the
bounded repair loop and the 21-key validation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Sequence

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


def summarize_values(
    values: Iterable[float], label: str = "", unit: str = ""
) -> dict[str, Any]:
    """p50/p95/p99 of any sample, refusing any percentile the sample cannot support.

    Unit-agnostic sibling of :func:`summarize_latencies`: the pipeline benchmark
    also has to summarize a dimensionless quantity (the parallel speed-up ratio)
    and dollars, and those must be guarded by the same sample-count policy as a
    latency. Keys are bare (``p50``, not ``p50_ms``) with the unit recorded
    alongside, so a reader cannot mistake a ratio for milliseconds.
    """
    samples = sorted(float(v) for v in values)
    n = len(samples)
    out: dict[str, Any] = {
        "label": label,
        "unit": unit,
        "n": n,
        "method": PERCENTILE_METHOD,
        "min": samples[0] if n else None,
        "max": samples[-1] if n else None,
        "mean": statistics.fmean(samples) if n else None,
        "p50": None,
        "p95": None,
        "p99": None,
        "suppressed": {},
    }
    for name, p in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
        need = PERCENTILE_MIN_SAMPLES[name]
        if n >= need:
            out[name] = nearest_rank_percentile(samples, p)
        else:
            out["suppressed"][name] = (
                f"refused: {n} sample(s), need >= {need}. A {name} over {n} "
                f"sample(s) is not a {name}."
            )
    return out


def summarize_latencies(values: Iterable[float], label: str = "") -> dict[str, Any]:
    """p50/p95/p99 of a millisecond sample, with ``_ms``-suffixed keys.

    A suppressed percentile comes back as ``None`` with its reason recorded in
    ``suppressed``. Callers render "n/a (need N samples)" rather than a number.
    """
    base = summarize_values(values, label=label, unit="ms")
    return {
        "label": base["label"],
        "n": base["n"],
        "method": base["method"],
        "min_ms": round(base["min"], 3) if base["min"] is not None else None,
        "max_ms": round(base["max"], 3) if base["max"] is not None else None,
        "mean_ms": round(base["mean"], 3) if base["mean"] is not None else None,
        "p50_ms": round(base["p50"], 3) if base["p50"] is not None else None,
        "p95_ms": round(base["p95"], 3) if base["p95"] is not None else None,
        "p99_ms": round(base["p99"], 3) if base["p99"] is not None else None,
        "suppressed": base["suppressed"],
    }


# ---------------------------------------------------------------------------
# Run environment provenance
# ---------------------------------------------------------------------------


def _git(*args: str) -> str | None:
    """Run a read-only git command for run provenance, or return None.

    The executable is resolved to an ABSOLUTE path via ``shutil.which`` rather than
    passed as the bare name ``git``. A partial path is resolved against ``PATH`` at
    exec time, so on a machine with a writable directory earlier in ``PATH`` this
    would run whatever ``git`` that directory contains. Provenance metadata is not
    worth that, and the fix costs one lookup.

    ``shell=False`` is explicit for the same reason: it is the default, but stating it
    means a later edit has to remove it deliberately rather than inherit a shell.
    """
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit  # nosec B603 - absolute path via shutil.which, static argv, shell=False, no external input
            [git, *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            shell=False,
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
# Pipeline mode: the shipped fan-out + the shipped synthesizer, over every fixture
#
# WHY THIS IS A SEPARATE MODE FROM --live. ``run_live`` above builds its own
# ``strands.Agent`` per specialist and its own synthesis call. That was enough to
# prove the measurement path works, but it is NOT the code AgentCore runs: it does
# not go through ``agents.fanout``'s semaphore, widened executor or shared botocore
# pool, it does not go through ``agents.synthesize``'s structured-output validation
# or its bounded repair, and it cannot reach the mantle synthesizer at all (GPT-5.x
# is not on Converse). A latency published from it would be a latency for a
# different program.
#
# ``--pipeline`` calls ``fan_out`` and ``synthesize`` and measures what they report
# about themselves, so the headline number describes the shipped pipeline.
# ---------------------------------------------------------------------------

#: The customer's stated target, and the only form of it this harness can answer.
#: "Sub-minute per application" is a wall-clock statement about one adjudication,
#: so the report counts runs under this threshold rather than claiming a
#: distribution-wide guarantee no sample size here could support.
TARGET_E2E_SECONDS: Final = 60.0

#: Default repetitions per application for ``--pipeline``. Three is chosen from the
#: cost, not from statistics: 14 applications x 3 x ~$0.245 is ~$10 of inference,
#: and 42 samples is the smallest set that clears PERCENTILE_MIN_SAMPLES["p95"]=20
#: for the pooled end-to-end figure. It does NOT clear p95 per application (3
#: samples each), and the report suppresses those rather than printing them.
PIPELINE_DEFAULT_REPETITIONS: Final = 3


@dataclass
class SpecialistMeasurement:
    """One specialist within one pipeline run, copied off its SpecialistResult.

    Every field here is read from what ``agents.fanout`` measured; nothing is
    recomputed and nothing is defaulted. ``usage`` is the raw Bedrock dict so all
    four token classes survive into the JSON.
    """

    domain: str
    agent_name: str
    model_id: str
    tier: str
    wall_ms: float
    latency_ms: float | None
    usage: dict[str, int] | None
    cost_usd: float | None
    stop_reason: str | None
    error: str | None
    analysis_chars: int
    source: str

    def to_json(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "agent_name": self.agent_name,
            "model_id": self.model_id,
            "tier": self.tier,
            "wall_ms": round(self.wall_ms, 3),
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "cost_usd": self.cost_usd,
            "stop_reason": self.stop_reason,
            "error": self.error,
            "analysis_chars": self.analysis_chars,
            "source": self.source,
        }


@dataclass
class PipelineRun:
    """One full adjudication: eight specialists fanned out, then one synthesis.

    ``failure_kinds`` classifies every error string this run produced. A run that
    lost a specialist is reported as such -- it stays in the latency sample (it
    really did take that long) and is EXCLUDED from the per-application cost, which
    would otherwise be an under-count wearing a total's name.
    """

    application_id: str
    repetition: int
    fanout_wall_ms: float
    specialists: list[SpecialistMeasurement]
    synthesis_wall_ms: float | None
    synthesis_latency_ms: float | None
    synthesis_usage: dict[str, int] | None
    synthesis_cost_usd: float | None
    synthesis_model_id: str
    synthesis_stop_reason: str | None
    repair_attempts: int | None
    degraded_domains: list[str]
    adjudication_validated: bool
    adjudication_keys: int | None
    overall_risk_decision: str | None
    recommendation_decision: str | None
    e2e_wall_ms: float
    synthesis_error: str | None = None

    # -- derived, all from measured members only ---------------------------

    @property
    def sum_of_eight_ms(self) -> float:
        """The sequential counterfactual: what eight specialists cost one after another.

        Uses each specialist's own measured wall clock. It is a counterfactual and
        labelled as one -- run sequentially the calls might each be marginally
        faster with no pool contention -- but every term in it was measured.
        """
        return sum(s.wall_ms for s in self.specialists)

    @property
    def slowest_specialist(self) -> str | None:
        if not self.specialists:
            return None
        return max(self.specialists, key=lambda s: s.wall_ms).domain

    @property
    def slowest_specialist_wall_ms(self) -> float | None:
        if not self.specialists:
            return None
        return max(s.wall_ms for s in self.specialists)

    @property
    def parallel_speedup(self) -> float | None:
        """sum-of-eight / fan-out wall clock. None if the fan-out took no time."""
        if self.fanout_wall_ms <= 0:
            return None
        return self.sum_of_eight_ms / self.fanout_wall_ms

    @property
    def failed_domains(self) -> list[str]:
        return [s.domain for s in self.specialists if s.error]

    @property
    def complete(self) -> bool:
        """All eight specialists answered AND the adjudication validated."""
        return (
            len(self.specialists) == len(DOMAINS)
            and not self.failed_domains
            and self.adjudication_validated
        )

    @property
    def fully_priced(self) -> bool:
        """Every one of the nine calls reported real usage and a real cost."""
        return (
            self.complete
            and all(s.usage is not None and s.cost_usd is not None for s in self.specialists)
            and self.synthesis_usage is not None
            and self.synthesis_cost_usd is not None
        )

    @property
    def cost_usd(self) -> float | None:
        """Total measured cost of this adjudication, or None if anything is unpriced."""
        if not self.fully_priced:
            return None
        return sum(s.cost_usd for s in self.specialists) + self.synthesis_cost_usd  # type: ignore[misc,operator]

    @property
    def failure_kinds(self) -> list[str]:
        """Every error on this run, classified by exception name."""
        kinds: list[str] = []
        for spec in self.specialists:
            if spec.error:
                kinds.append(f"{spec.domain}: {classify_failure(spec.error)}")
        if self.synthesis_error:
            kinds.append(f"synthesis: {classify_failure(self.synthesis_error)}")
        return kinds

    def to_json(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "repetition": self.repetition,
            "fanout_wall_ms": round(self.fanout_wall_ms, 3),
            "sum_of_eight_ms": round(self.sum_of_eight_ms, 3),
            "parallel_speedup": (
                round(self.parallel_speedup, 4) if self.parallel_speedup is not None else None
            ),
            "slowest_specialist": self.slowest_specialist,
            "slowest_specialist_wall_ms": (
                round(self.slowest_specialist_wall_ms, 3)
                if self.slowest_specialist_wall_ms is not None
                else None
            ),
            "synthesis_wall_ms": (
                round(self.synthesis_wall_ms, 3) if self.synthesis_wall_ms is not None else None
            ),
            "synthesis_latency_ms": self.synthesis_latency_ms,
            "synthesis_usage": self.synthesis_usage,
            "synthesis_cost_usd": self.synthesis_cost_usd,
            "synthesis_model_id": self.synthesis_model_id,
            "synthesis_stop_reason": self.synthesis_stop_reason,
            "synthesis_error": self.synthesis_error,
            "repair_attempts": self.repair_attempts,
            "degraded_domains": self.degraded_domains,
            "adjudication_validated": self.adjudication_validated,
            "adjudication_keys": self.adjudication_keys,
            "overall_risk_decision": self.overall_risk_decision,
            "recommendation_decision": self.recommendation_decision,
            "e2e_wall_ms": round(self.e2e_wall_ms, 3),
            "e2e_seconds": round(self.e2e_wall_ms / 1000.0, 3),
            "under_target": self.e2e_wall_ms / 1000.0 < TARGET_E2E_SECONDS,
            "failed_domains": self.failed_domains,
            "failure_kinds": self.failure_kinds,
            "complete": self.complete,
            "fully_priced": self.fully_priced,
            "cost_usd": self.cost_usd,
            "specialists": [s.to_json() for s in self.specialists],
        }


#: Failure classes worth counting separately, because they mean different things to
#: the customer. Throttling is a capacity/quota conversation; MaxTokens is a config
#: bug in this repo; a 5xx is transient and argues for a retry policy. Lumping them
#: into "errors: 3" hides which of those three conversations is needed.
FAILURE_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("ThrottlingException", "throttled"),
    ("TooManyRequestsException", "throttled"),
    ("ServiceQuotaExceededException", "throttled"),
    ("MaxTokensReachedException", "max_tokens_truncation"),
    ("ModelStreamErrorException", "stream_error"),
    ("InternalServerException", "server_5xx"),
    ("ServiceUnavailableException", "server_5xx"),
    ("ModelTimeoutException", "timeout"),
    ("ReadTimeoutError", "timeout"),
    ("ConnectTimeoutError", "timeout"),
    ("ValidationException", "validation"),
    ("AccessDeniedException", "access_denied"),
    ("SynthesisContractError", "contract_failure"),
    ("MantleUnavailable", "mantle_unavailable"),
)


def classify_failure(error: str) -> str:
    """Bucket one error string into a named failure class.

    Substring matching on the exception name that ``agents.fanout`` already put at
    the front of the string. An unrecognised error keeps its own exception name
    rather than being filed as "other", so nothing disappears into a catch-all.
    """
    for needle, label in FAILURE_PATTERNS:
        if needle in error:
            return label
    return error.split(":", 1)[0].strip() or "unknown"


async def _run_one_pipeline(
    application_id: str,
    repetition: int,
    *,
    mock: bool,
) -> PipelineRun:
    """One adjudication through the shipped fan-out and the shipped synthesizer.

    The payload is built OUTSIDE the timed window: signal projection is the
    customer's own precomputed-signal layer (Agent_Underwriting_Process_Guide 2.2),
    not part of the model latency being measured, and including it would make this
    number incomparable with the per-call figures it is built from.
    """
    from agents.contracts import ADJUDICATION_FIELD_ORDER  # noqa: PLC0415
    from agents.fanout import fan_out  # noqa: PLC0415
    from agents.synthesize import synthesize  # noqa: PLC0415
    from fixtures.loader import build_payload  # noqa: PLC0415

    payload = build_payload(application_id)

    e2e_start = time.perf_counter()
    fanout_start = time.perf_counter()
    results = await fan_out(payload, mock=mock)
    fanout_wall_ms = (time.perf_counter() - fanout_start) * 1000.0

    specialists = [
        SpecialistMeasurement(
            domain=domain,
            agent_name=result.agent_name,
            model_id=result.model_id,
            tier=result.tier,
            wall_ms=result.wall_ms,
            latency_ms=result.latency_ms,
            usage=result.usage,
            cost_usd=result.cost_usd,
            stop_reason=result.stop_reason,
            error=result.error,
            analysis_chars=len(result.analysis or ""),
            source=result.source,
        )
        for domain, result in results.items()
    ]

    synthesis_wall_ms: float | None = None
    synthesis_latency_ms: float | None = None
    synthesis_usage: dict[str, int] | None = None
    synthesis_cost: float | None = None
    synthesis_stop: str | None = None
    synthesis_error: str | None = None
    repair_attempts: int | None = None
    degraded: list[str] = []
    validated = False
    keys: int | None = None
    overall: str | None = None
    recommendation: str | None = None
    synthesis_model = "unknown"

    syn_start = time.perf_counter()
    try:
        synthesis = await synthesize(results, application_id=application_id, mock=mock)
    except Exception as exc:  # a failed synthesis is data, not a crashed benchmark
        synthesis_wall_ms = (time.perf_counter() - syn_start) * 1000.0
        synthesis_error = f"{type(exc).__name__}: {exc}"
        from agents.models import SYNTHESIZER_MODEL  # noqa: PLC0415

        synthesis_model = SYNTHESIZER_MODEL
    else:
        synthesis_wall_ms = synthesis.wall_ms
        synthesis_latency_ms = synthesis.latency_ms
        synthesis_usage = synthesis.usage
        synthesis_cost = synthesis.cost_usd
        synthesis_stop = synthesis.stop_reason
        synthesis_model = synthesis.model_id
        repair_attempts = synthesis.repair_attempts
        degraded = list(synthesis.degraded_domains)
        dumped = synthesis.adjudication.model_dump()
        keys = len(dumped)
        # ``synthesize`` never returns an unvalidated object, so reaching here IS
        # the validation result. The key count is re-checked anyway: it is the one
        # assertion the customer's engineer will make first.
        validated = keys == len(ADJUDICATION_FIELD_ORDER) and set(dumped) == set(
            ADJUDICATION_FIELD_ORDER
        )
        overall = dumped.get("overall_risk_decision")
        recommendation = dumped.get("recommendation_decision")

    e2e_wall_ms = (time.perf_counter() - e2e_start) * 1000.0
    return PipelineRun(
        application_id=application_id,
        repetition=repetition,
        fanout_wall_ms=fanout_wall_ms,
        specialists=specialists,
        synthesis_wall_ms=synthesis_wall_ms,
        synthesis_latency_ms=synthesis_latency_ms,
        synthesis_usage=synthesis_usage,
        synthesis_cost_usd=synthesis_cost,
        synthesis_model_id=synthesis_model,
        synthesis_stop_reason=synthesis_stop,
        synthesis_error=synthesis_error,
        repair_attempts=repair_attempts,
        degraded_domains=degraded,
        adjudication_validated=validated,
        adjudication_keys=keys,
        overall_risk_decision=overall,
        recommendation_decision=recommendation,
        e2e_wall_ms=e2e_wall_ms,
    )


def _under_target_accounting(runs: Sequence[PipelineRun]) -> dict[str, Any]:
    """How many runs, and how many applications, came in under the target.

    Two different questions, both asked, because they have different answers and
    only one of them is "how many of the 14 came in under 60 seconds":

      * ``runs_under_target`` -- of every measured adjudication.
      * ``applications_all_reps_under_target`` -- applications where EVERY
        repetition was under. This is the strict reading and the one to quote.
      * ``applications_any_rep_under_target`` -- applications where at least one
        was. The gap between the two is the applications that straddle the line,
        and naming it is the point: a p50 under 60s is not a promise of 60s.
    """
    per_app: dict[str, list[bool]] = {}
    for run in runs:
        per_app.setdefault(run.application_id, []).append(
            run.e2e_wall_ms / 1000.0 < TARGET_E2E_SECONDS
        )
    all_under = sorted(app for app, flags in per_app.items() if flags and all(flags))
    any_under = sorted(app for app, flags in per_app.items() if any(flags))
    straddling = sorted(set(any_under) - set(all_under))
    return {
        "target_seconds": TARGET_E2E_SECONDS,
        "n_runs": len(runs),
        "runs_under_target": sum(
            1 for run in runs if run.e2e_wall_ms / 1000.0 < TARGET_E2E_SECONDS
        ),
        "n_applications": len(per_app),
        "applications_all_reps_under_target": len(all_under),
        "applications_any_rep_under_target": len(any_under),
        "applications_never_under_target": sorted(set(per_app) - set(any_under)),
        "applications_straddling_target": straddling,
        "per_application_reps_under_target": {
            app: {"n_reps": len(flags), "under": sum(flags)}
            for app, flags in sorted(per_app.items())
        },
        "note": (
            "A count of runs under a threshold is not a percentile and not an SLA. "
            "It is the only form of a 'sub-minute per application' claim this "
            "sample size can support."
        ),
    }


def build_pipeline_report(
    *,
    env: dict[str, Any],
    runs: Sequence[PipelineRun],
    models: dict[str, str],
    application_ids: Sequence[str],
    repetitions: int,
    mock: bool,
    extra_caveats: Sequence[str] = (),
) -> dict[str, Any]:
    """Aggregate pipeline runs into the committed report.

    Everything here is derived from measured members of :class:`PipelineRun`. The
    only judgements the aggregation makes are (a) which runs are complete enough to
    contribute a cost, and (b) which percentiles the sample supports -- and both are
    reported next to the number rather than applied silently.
    """
    e2e = [r.e2e_wall_ms for r in runs]
    fanout = [r.fanout_wall_ms for r in runs]
    synthesis = [r.synthesis_wall_ms for r in runs if r.synthesis_wall_ms is not None]
    speedups = [r.parallel_speedup for r in runs if r.parallel_speedup is not None]
    sums = [r.sum_of_eight_ms for r in runs]

    per_application: dict[str, Any] = {}
    for app_id in sorted({r.application_id for r in runs}):
        rows = [r for r in runs if r.application_id == app_id]
        costs = [r.cost_usd for r in rows if r.cost_usd is not None]
        verdicts = sorted({r.overall_risk_decision for r in rows if r.overall_risk_decision})
        recs = sorted({r.recommendation_decision for r in rows if r.recommendation_decision})
        per_application[app_id] = {
            "n_runs": len(rows),
            "end_to_end": summarize_latencies((r.e2e_wall_ms for r in rows), f"{app_id} e2e"),
            "fan_out": summarize_latencies(
                (r.fanout_wall_ms for r in rows), f"{app_id} fan-out"
            ),
            "synthesis": (
                summarize_latencies(
                    (r.synthesis_wall_ms for r in rows if r.synthesis_wall_ms is not None),
                    f"{app_id} synthesis",
                )
                if any(r.synthesis_wall_ms is not None for r in rows)
                else {"n": 0, "note": "no synthesis wall clock recorded for this application"}
            ),
            "parallel_speedup": summarize_values(
                (r.parallel_speedup for r in rows if r.parallel_speedup is not None),
                f"{app_id} speed-up",
                unit="x",
            ),
            "cost_usd": {
                "n_priced_runs": len(costs),
                "n_unpriced_runs": len(rows) - len(costs),
                "min": min(costs) if costs else None,
                "max": max(costs) if costs else None,
                "mean": statistics.fmean(costs) if costs else None,
                "note": (
                    None
                    if costs
                    else "no run of this application produced real usage for all nine calls"
                ),
            },
            "complete_runs": sum(1 for r in rows if r.complete),
            "degraded_runs": sum(1 for r in rows if r.failed_domains),
            "validated_runs": sum(1 for r in rows if r.adjudication_validated),
            "overall_risk_decisions_observed": verdicts,
            "recommendation_decisions_observed": recs,
            "verdict_stable_across_repetitions": len(verdicts) <= 1 and len(recs) <= 1,
        }

    per_specialist: dict[str, Any] = {}
    for domain in DOMAINS:
        rows = [s for r in runs for s in r.specialists if s.domain == domain]
        if not rows:
            continue
        ok = [s for s in rows if not s.error]
        costs = [s.cost_usd for s in rows if s.cost_usd is not None]
        reported = [s.latency_ms for s in rows if s.latency_ms is not None]
        per_specialist[domain] = {
            "model_id": rows[0].model_id,
            "tier": rows[0].tier,
            "n_calls": len(rows),
            "n_failed": len(rows) - len(ok),
            # Successful calls only: a call that failed after 200ms would otherwise
            # drag the p50 DOWN and make the dimension look fast.
            "wall_clock_successful_only": (
                summarize_latencies((s.wall_ms for s in ok), f"{domain} wall clock")
                if ok
                else {"n": 0, "note": "no successful call for this specialist"}
            ),
            "sdk_reported_latency": (
                summarize_latencies(reported, f"{domain} latencyMs")
                if reported
                else {"n": 0, "note": "no SDK-reported latencyMs (mock run, or omitted)"}
            ),
            "n_slowest_in_run": sum(1 for r in runs if r.slowest_specialist == domain),
            "cost_usd": {
                "n_priced_calls": len(costs),
                "total": sum(costs) if costs else None,
                "mean_per_call": statistics.fmean(costs) if costs else None,
                "note": None if costs else "no real usage measured, so cost is null (not zero)",
            },
            "tokens": _token_totals(s.usage for s in rows),
            "errors": [s.error for s in rows if s.error],
        }

    synthesis_usages = [r.synthesis_usage for r in runs]
    synthesis_costs = [r.synthesis_cost_usd for r in runs if r.synthesis_cost_usd is not None]
    synthesis_block = {
        "model_id": runs[0].synthesis_model_id if runs else models.get("synthesis"),
        "n_calls": len(runs),
        "n_failed": sum(1 for r in runs if r.synthesis_error),
        "wall_clock": (
            summarize_latencies(synthesis, "synthesis wall clock")
            if synthesis
            else {"n": 0, "note": "no synthesis wall clock recorded"}
        ),
        "adapter_reported_latency": (
            summarize_latencies(
                [r.synthesis_latency_ms for r in runs if r.synthesis_latency_ms is not None],
                "synthesis latency_ms",
            )
            if any(r.synthesis_latency_ms is not None for r in runs)
            else {"n": 0, "note": "no adapter-reported latency (mock run)"}
        ),
        "cost_usd": {
            "n_priced_calls": len(synthesis_costs),
            "total": sum(synthesis_costs) if synthesis_costs else None,
            "mean_per_call": (
                statistics.fmean(synthesis_costs) if synthesis_costs else None
            ),
            "note": None if synthesis_costs else "no real usage measured, so cost is null",
        },
        "tokens": _token_totals(synthesis_usages),
        "repair_attempts": {
            "total": sum(r.repair_attempts or 0 for r in runs),
            "runs_needing_a_repair": sum(1 for r in runs if (r.repair_attempts or 0) > 0),
        },
        "errors": [r.synthesis_error for r in runs if r.synthesis_error],
    }

    complete = [r for r in runs if r.complete]
    priced = [r for r in runs if r.cost_usd is not None]
    degraded_runs = [r for r in runs if r.failed_domains or r.synthesis_error]
    failure_counter: dict[str, int] = {}
    for run in runs:
        for kind in run.failure_kinds:
            label = kind.split(": ", 1)[1]
            failure_counter[label] = failure_counter.get(label, 0) + 1

    run_costs = [r.cost_usd for r in priced]
    cost_summary = summarize_values(run_costs, "cost per adjudication", unit="usd")

    caveats: list[str] = [
        "MEASURED: this run called agents.fanout.fan_out and "
        "agents.synthesize.synthesize -- the shipped code path -- once per "
        "(application, repetition). Every latency, token count and cost below was "
        "produced by those calls.",
        "MEASURED: sum_of_eight_ms is the SEQUENTIAL COUNTERFACTUAL, the sum of the "
        "eight specialists' own measured wall clocks in this run. It is what eight "
        "calls cost one after another only if each would take the same time alone; "
        "the terms are measured, the arrangement is hypothetical.",
        "NOT MEASURED: AgentCore runtime overhead. These calls go to Bedrock (and to "
        "bedrock-mantle for the synthesizer) directly from the host named in run.host, "
        "so container cold start, session setup and SSE framing are NOT in the "
        "end-to-end figures.",
        "NOT MEASURED: signal generation. build_payload runs OUTSIDE the timed window "
        "because the customer's architecture precomputes signals; an end-to-end figure "
        "here is agent latency, not pipeline-including-ETL latency.",
        "NOT MEASURED: throughput. Applications were run ONE AT A TIME, so nothing "
        "here says what happens when N adjudications overlap.",
        f"Percentiles are suppressed below {PERCENTILE_MIN_SAMPLES} samples. "
        "Per-application percentiles are computed over "
        f"{repetitions} repetition(s) each and are therefore mostly suppressed by "
        "design; the pooled figures carry the sample count that supports them.",
        "Every number in this file was produced by this run. No figure here is "
        "illustrative, nominal, extrapolated or borrowed from a vendor datasheet.",
    ]
    if mock:
        caveats.insert(
            0,
            "*** MOCK RUN. NO MODEL WAS CALLED. *** Latencies here are this repo's "
            "orchestration overhead only, every cost is null, and no figure in this "
            "file may be shown to the customer as an application-processing time.",
        )
    if degraded_runs:
        caveats.append(
            f"{len(degraded_runs)} of {len(runs)} run(s) lost a specialist or a "
            "synthesis. They REMAIN in the latency samples -- the adjudication really "
            "took that long -- and are EXCLUDED from cost_per_adjudication, which "
            "would otherwise under-report. See failures.by_run."
        )
    if len(runs) < PERCENTILE_MIN_SAMPLES["p95"]:
        caveats.append(
            f"n={len(runs)} pooled run(s) is below the {PERCENTILE_MIN_SAMPLES['p95']} "
            "needed for a p95. The pooled p95 in this report is null with its reason "
            "recorded, and no p95 may be quoted from it."
        )

    return {
        "schema": "pointpredictive.agentcore.pipeline-bench/1",
        # The target is stamped here rather than at the call site so every report,
        # however it was produced, records the threshold its under-target count was
        # measured against. A count with no threshold beside it is not a fact.
        "run": {**env, "target_e2e_seconds": TARGET_E2E_SECONDS},
        "workload": {
            "application_source": "fixtures.loader",
            "application_ids": list(application_ids),
            "repetitions_per_application": repetitions,
            "domains": list(DOMAINS),
            "models": models,
            "calls_per_adjudication": len(DOMAINS) + 1,
            "execution_order": (
                "round-robin: repetition 1 over every application, then repetition 2, "
                "and so on. One application in flight at a time, so an application's "
                "repetitions are spread across the wall-clock window rather than "
                "measured back to back."
            ),
            "mock": mock,
        },
        "totals": {
            "runs": len(runs),
            "expected_runs": len(application_ids) * repetitions,
            "specialist_calls": sum(len(r.specialists) for r in runs),
            "complete_runs": len(complete),
            "degraded_runs": len(degraded_runs),
            "validated_adjudications": sum(1 for r in runs if r.adjudication_validated),
            "total_measured_cost_usd": (
                sum(
                    c
                    for r in runs
                    for c in [
                        *(s.cost_usd for s in r.specialists if s.cost_usd is not None),
                        *([r.synthesis_cost_usd] if r.synthesis_cost_usd is not None else []),
                    ]
                )
                or None
            ),
        },
        "end_to_end": summarize_latencies(e2e, "end-to-end, all applications pooled"),
        "fan_out": summarize_latencies(fanout, "fan-out, all applications pooled"),
        "synthesis": (
            summarize_latencies(synthesis, "synthesis, all applications pooled")
            if synthesis
            else {"n": 0, "note": "no synthesis wall clock recorded"}
        ),
        "sum_of_eight_sequential_counterfactual": summarize_latencies(
            sums, "sum of eight specialists (sequential counterfactual)"
        ),
        "parallel_speedup": {
            **summarize_values(speedups, "fan-out vs sum-of-eight", unit="x"),
            "distribution": [
                {
                    "application_id": r.application_id,
                    "repetition": r.repetition,
                    "speedup": round(r.parallel_speedup, 4),
                    "fanout_ms": round(r.fanout_wall_ms, 1),
                    "sum_of_eight_ms": round(r.sum_of_eight_ms, 1),
                }
                for r in runs
                if r.parallel_speedup is not None
            ],
            "note": (
                "One speed-up per run, not one number for the port. The spread is the "
                "finding: the fan-out is bounded by its slowest dimension, so the "
                "ratio moves with which specialist ran long."
            ),
        },
        "target": _under_target_accounting(runs),
        "cost_per_adjudication_usd": {
            **cost_summary,
            "n_priced_runs": len(priced),
            "n_unpriced_runs": len(runs) - len(priced),
            "variance_drivers": _cost_variance_drivers(priced),
            "note": (
                None
                if priced
                else "null: no run produced real usage for all nine calls, so no "
                "per-application cost was measured"
            ),
        },
        "per_application": per_application,
        "per_specialist": per_specialist,
        "synthesis_detail": synthesis_block,
        "verdict_stability": _verdict_stability(runs),
        "failures": {
            "n_degraded_runs": len(degraded_runs),
            "by_kind": dict(sorted(failure_counter.items())),
            "by_run": [
                {
                    "application_id": r.application_id,
                    "repetition": r.repetition,
                    "failed_domains": r.failed_domains,
                    "failure_kinds": r.failure_kinds,
                    "synthesis_error": r.synthesis_error,
                    "degraded_domains": r.degraded_domains,
                    "adjudication_validated": r.adjudication_validated,
                    "e2e_seconds": round(r.e2e_wall_ms / 1000.0, 3),
                }
                for r in degraded_runs
            ],
            "note": (
                "A degraded run is reported, never averaged away. It stays in the "
                "latency distribution and leaves the cost distribution."
            ),
        },
        "caveats": [*caveats, *extra_caveats],
        "raw_runs": [r.to_json() for r in runs],
    }


def _token_totals(usages: Iterable[dict[str, int] | None]) -> dict[str, Any]:
    """Sum every token class over a set of calls, keeping absence visible.

    All four classes are reported even when zero, because "0 cache reads" and "no
    usage measured" are different statements and the customer's engineer will read
    them differently. ``n_calls_without_usage`` is what separates them.
    """
    rows = list(usages)
    present = [u for u in rows if u]
    totals = {
        "inputTokens": sum(int(u.get("inputTokens", 0) or 0) for u in present),
        "outputTokens": sum(int(u.get("outputTokens", 0) or 0) for u in present),
        "cacheReadInputTokens": sum(
            int(u.get("cacheReadInputTokens", 0) or 0) for u in present
        ),
        "cacheWriteInputTokens": sum(
            int(u.get("cacheWriteInputTokens", 0) or 0) for u in present
        ),
    }
    return {
        "n_calls_with_usage": len(present),
        "n_calls_without_usage": len(rows) - len(present),
        "totals": totals if present else None,
        "mean_per_call": (
            {k: v / len(present) for k, v in totals.items()} if present else None
        ),
        "note": None if present else "no call reported usage, so every total is null",
    }


def _verdict_stability(runs: Sequence[PipelineRun]) -> dict[str, Any]:
    """Did repeated runs of the same application reach the same verdict?

    NOT a latency figure and not an accuracy claim, but it is measured here and it
    conditions everything else in the report: if the same input can yield two
    different bands, a per-application latency is a latency for whichever answer the
    model happened to give. It is also the number the customer's engineer will ask
    for the moment a synthesizer swap is proposed, so recording it in the same file
    is cheaper than discovering it later.

    ``expected`` is the fixture's own pinned band. A disagreement here is reported,
    never reconciled: whether the pin or the model is wrong is the customer's call,
    and this harness only has standing to say the two differ.
    """
    per_app: dict[str, dict[str, Any]] = {}
    for app_id in sorted({r.application_id for r in runs}):
        rows = [r for r in runs if r.application_id == app_id]
        observed = sorted({r.overall_risk_decision for r in rows if r.overall_risk_decision})
        recs = sorted({r.recommendation_decision for r in rows if r.recommendation_decision})
        expected: str | None = None
        try:
            from fixtures.loader import expected_for  # noqa: PLC0415

            expected = expected_for(app_id).get("overall_risk_decision")
        except Exception:
            expected = None
        # Counted per RUN, not per distinct value: "2 of 3 runs matched the pin" and
        # "no run matched the pin" are different findings, and collapsing to a
        # set-equality boolean would report both as a plain mismatch.
        matching = (
            sum(1 for r in rows if r.overall_risk_decision == expected)
            if expected is not None
            else None
        )
        per_app[app_id] = {
            "n_runs": len(rows),
            "expected_overall_risk_decision": expected,
            "observed_overall_risk_decisions": observed,
            "observed_recommendation_decisions": recs,
            "n_runs_matching_expectation": matching,
            "stable": len(observed) <= 1 and len(recs) <= 1,
            "matches_expected": (
                None if expected is None or not observed else observed == [expected]
            ),
        }
    unstable = sorted(a for a, d in per_app.items() if not d["stable"])
    mismatched = sorted(a for a, d in per_app.items() if d["matches_expected"] is False)
    never_matched = sorted(
        a
        for a, d in per_app.items()
        if d["n_runs_matching_expectation"] == 0
    )
    return {
        "n_applications": len(per_app),
        "stable_applications": len(per_app) - len(unstable),
        "unstable_applications": unstable,
        "applications_disagreeing_with_pinned_expectation": mismatched,
        "applications_never_matching_pinned_expectation": never_matched,
        "per_application": per_app,
        "note": (
            "Verdict agreement is reported, not adjudicated. An unstable application "
            "means repeated runs of identical input reached different bands; a "
            "disagreement means the run differs from the fixture's pinned expectation. "
            "Neither is resolved here -- run the calibration judge in evals/evaluators/ "
            "before drawing an accuracy conclusion from either."
        ),
    }


def _cost_variance_drivers(runs: Sequence[PipelineRun]) -> dict[str, Any]:
    """What actually moves the per-application cost, measured rather than asserted.

    Cost is linear in tokens at fixed rates, so the only things that can move it are
    how many tokens each class carried. This reports the token spread of the most and
    least expensive priced runs side by side, so the reader can see which class
    differs instead of taking an explanation on trust.
    """
    if not runs:
        return {"note": "no priced run, so nothing to attribute"}
    cheapest = min(runs, key=lambda r: r.cost_usd)  # type: ignore[arg-type,return-value]
    dearest = max(runs, key=lambda r: r.cost_usd)  # type: ignore[arg-type,return-value]

    def _profile(run: PipelineRun) -> dict[str, Any]:
        spec_in = sum(int((s.usage or {}).get("inputTokens", 0)) for s in run.specialists)
        spec_out = sum(int((s.usage or {}).get("outputTokens", 0)) for s in run.specialists)
        syn = run.synthesis_usage or {}
        return {
            "application_id": run.application_id,
            "repetition": run.repetition,
            "cost_usd": run.cost_usd,
            "specialist_input_tokens": spec_in,
            "specialist_output_tokens": spec_out,
            "synthesis_input_tokens": int(syn.get("inputTokens", 0)),
            "synthesis_output_tokens": int(syn.get("outputTokens", 0)),
            "synthesis_cache_read_tokens": int(syn.get("cacheReadInputTokens", 0)),
            "synthesis_cache_write_tokens": int(syn.get("cacheWriteInputTokens", 0)),
            "specialist_cost_usd": sum(
                s.cost_usd for s in run.specialists if s.cost_usd is not None
            ),
            "synthesis_cost_usd": run.synthesis_cost_usd,
        }

    return {
        "cheapest_run": _profile(cheapest),
        "most_expensive_run": _profile(dearest),
        "spread_usd": (dearest.cost_usd or 0.0) - (cheapest.cost_usd or 0.0),
        "note": (
            "Rates are fixed per model, so the only driver is token volume. Compare "
            "the two profiles: the class that differs is the driver."
        ),
    }


async def run_pipeline(
    application_ids: Sequence[str],
    repetitions: int,
    *,
    mock: bool,
    region: str,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the shipped pipeline over every application, ``repetitions`` times each.

    Round-robin over applications rather than repeating one application before
    moving on: repeating back to back measures one moment of Bedrock's day per
    application, while round-robin spreads each application's repetitions across
    the whole window. Neither removes time-of-day variance; only one of them stops
    it from being confounded with the application id.
    """
    models = resolve_specialist_models()
    runs: list[PipelineRun] = []
    for rep in range(1, repetitions + 1):
        for app_id in application_ids:
            if progress:
                progress(f"rep {rep}/{repetitions}  {app_id} ...")
            run = await _run_one_pipeline(app_id, rep, mock=mock)
            runs.append(run)
            if progress:
                failed = ",".join(run.failed_domains) or "-"
                cost = "n/a" if run.cost_usd is None else f"${run.cost_usd:.4f}"
                progress(
                    f"rep {rep}/{repetitions}  {app_id}  "
                    f"e2e {run.e2e_wall_ms / 1000.0:6.2f}s  "
                    f"fanout {run.fanout_wall_ms / 1000.0:6.2f}s  "
                    f"syn {(run.synthesis_wall_ms or 0.0) / 1000.0:5.2f}s  "
                    f"{cost}  slowest={run.slowest_specialist}  failed={failed}"
                )

    synth_region = region
    try:
        from agents import mantle  # noqa: PLC0415

        if mantle.is_mantle_model(models.get("synthesis", "")):
            synth_region = mantle.resolve_mantle_region(models["synthesis"], region)
    except Exception:
        pass

    env = run_environment(
        "pipeline-mock" if mock else "pipeline-live",
        region=("n/a (no AWS calls made)" if mock else region),
        model_ids=models,
    )
    # Recorded separately from aws_region: the synthesizer is on bedrock-mantle and
    # its variant may not be available in the specialists' region, so the two can
    # legitimately differ and a report that showed only one would be misleading.
    env["synthesizer_region"] = "n/a" if mock else synth_region
    return build_pipeline_report(
        env=env,
        runs=runs,
        models=models,
        application_ids=application_ids,
        repetitions=repetitions,
        mock=mock,
    )


def pipeline_run_from_json(row: dict[str, Any]) -> PipelineRun:
    """Rebuild a :class:`PipelineRun` from a committed report's ``raw_runs`` row.

    WHY THIS EXISTS: live Bedrock calls cost real money, so improving the
    *aggregation* must never require re-running the *measurement*. ``raw_runs``
    carries every measured field, so a new aggregate can be recomputed from the
    same numbers. Only measured fields are read; every derived field
    (``sum_of_eight_ms``, ``parallel_speedup``, ``cost_usd``, ``under_target``) is
    recomputed from them rather than trusted, so a stale derivation in an old file
    cannot survive into a new report.
    """
    specialists = [
        SpecialistMeasurement(
            domain=s["domain"],
            agent_name=s["agent_name"],
            model_id=s["model_id"],
            tier=s["tier"],
            wall_ms=float(s["wall_ms"]),
            latency_ms=s.get("latency_ms"),
            usage=s.get("usage"),
            cost_usd=s.get("cost_usd"),
            stop_reason=s.get("stop_reason"),
            error=s.get("error"),
            analysis_chars=int(s.get("analysis_chars", 0)),
            source=s.get("source", "bedrock"),
        )
        for s in row["specialists"]
    ]
    return PipelineRun(
        application_id=row["application_id"],
        repetition=int(row["repetition"]),
        fanout_wall_ms=float(row["fanout_wall_ms"]),
        specialists=specialists,
        synthesis_wall_ms=row.get("synthesis_wall_ms"),
        synthesis_latency_ms=row.get("synthesis_latency_ms"),
        synthesis_usage=row.get("synthesis_usage"),
        synthesis_cost_usd=row.get("synthesis_cost_usd"),
        synthesis_model_id=row.get("synthesis_model_id", "unknown"),
        synthesis_stop_reason=row.get("synthesis_stop_reason"),
        synthesis_error=row.get("synthesis_error"),
        repair_attempts=row.get("repair_attempts"),
        degraded_domains=list(row.get("degraded_domains") or []),
        adjudication_validated=bool(row.get("adjudication_validated")),
        adjudication_keys=row.get("adjudication_keys"),
        overall_risk_decision=row.get("overall_risk_decision"),
        recommendation_decision=row.get("recommendation_decision"),
        e2e_wall_ms=float(row["e2e_wall_ms"]),
    )


def reaggregate_pipeline_report(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute every aggregate of a committed report from its own raw runs.

    The provenance block is carried through unchanged: the numbers still belong to
    the run that measured them, and re-aggregating must not restamp them with
    today's git sha or a new timestamp. ``reaggregated_at_utc`` records that the
    derivation, and only the derivation, is newer than the measurement.
    """
    runs = [pipeline_run_from_json(row) for row in report["raw_runs"]]
    env = dict(report["run"])
    rebuilt = build_pipeline_report(
        env=env,
        runs=runs,
        models=dict(report["workload"]["models"]),
        application_ids=list(report["workload"]["application_ids"]),
        repetitions=int(report["workload"]["repetitions_per_application"]),
        mock=bool(report["workload"]["mock"]),
    )
    rebuilt["run"]["reaggregated_at_utc"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    rebuilt["caveats"].append(
        "Aggregates in this file were recomputed from raw_runs by "
        "evals.bench.reaggregate_pipeline_report. The measurements are unchanged and "
        "still belong to the run recorded in `run`; only the derived statistics were "
        "rebuilt. No model was called to produce this file."
    )
    return rebuilt


def render_pipeline_human(report: dict[str, Any]) -> str:
    """The table a human reads for a pipeline run."""
    run = report["run"]
    lines: list[str] = ["=" * 96]
    lines.append(f"AgentCore PIPELINE bench -- mode: {run['mode']}")
    if report["workload"]["mock"]:
        lines.append("")
        lines.append("  *** MOCK RUN: NO MODEL WAS CALLED. Latencies are orchestration ***")
        lines.append("  *** overhead on this host only. Every cost is n/a, not zero.   ***")
    lines.append("=" * 96)
    lines.append(f"  timestamp   : {run['timestamp_utc']}")
    lines.append(
        f"  git sha     : {run['git_sha']} ({run['git_branch']})"
        + ("  [WORKING TREE DIRTY]" if run.get("git_dirty") else "")
    )
    lines.append(f"  region      : {run['aws_region']}  "
                 f"(synthesizer: {run.get('synthesizer_region')})")
    lines.append(f"  applications: {len(report['workload']['application_ids'])} x "
                 f"{report['workload']['repetitions_per_application']} repetition(s) "
                 f"= {report['totals']['runs']} run(s)")
    lines.append(f"  complete    : {report['totals']['complete_runs']} of "
                 f"{report['totals']['runs']}   degraded: "
                 f"{report['totals']['degraded_runs']}   validated 21-key: "
                 f"{report['totals']['validated_adjudications']}")
    lines.append("")

    lines.append(f"Pooled latency (seconds), n={report['end_to_end']['n']}")
    lines.append(f"  {'stage':<26}{'n':>5}{'min':>9}{'p50':>9}{'p95':>9}{'max':>9}{'mean':>9}")
    for label, key in (
        ("end to end", "end_to_end"),
        ("fan-out (8 specialists)", "fan_out"),
        ("synthesis", "synthesis"),
        ("sum-of-eight (counterfactual)", "sum_of_eight_sequential_counterfactual"),
    ):
        block = report[key]
        if not block.get("n"):
            lines.append(f"  {label:<26}{0:>5}   {block.get('note', 'not measured')}")
            continue
        lines.append(
            f"  {label:<26}{block['n']:>5}"
            + "".join(
                _fmt_seconds(block.get(k), 9)
                for k in ("min_ms", "p50_ms", "p95_ms", "max_ms", "mean_ms")
            )
        )
    for name, reason in sorted(report["end_to_end"]["suppressed"].items()):
        lines.append(f"  end-to-end {name}: {reason}")
    lines.append("")

    target = report["target"]
    lines.append(f"Under the {target['target_seconds']:.0f}s target")
    lines.append(f"  runs under target        : {target['runs_under_target']} of "
                 f"{target['n_runs']}")
    lines.append(f"  applications, every rep  : "
                 f"{target['applications_all_reps_under_target']} of "
                 f"{target['n_applications']}")
    lines.append(f"  applications, any rep    : "
                 f"{target['applications_any_rep_under_target']} of "
                 f"{target['n_applications']}")
    if target["applications_straddling_target"]:
        lines.append(f"  straddling the line      : "
                     f"{', '.join(target['applications_straddling_target'])}")
    if target["applications_never_under_target"]:
        lines.append(f"  NEVER under target       : "
                     f"{', '.join(target['applications_never_under_target'])}")
    lines.append("")

    speed = report["parallel_speedup"]
    lines.append(f"Parallel speed-up, fan-out vs sum-of-eight (n={speed['n']})")
    if speed["n"]:
        lines.append(
            f"  min {speed['min']:.2f}x   p50 "
            f"{('%.2fx' % speed['p50']) if speed['p50'] is not None else 'n/a'}   "
            f"p95 {('%.2fx' % speed['p95']) if speed['p95'] is not None else 'n/a'}   "
            f"max {speed['max']:.2f}x   mean {speed['mean']:.2f}x"
        )
    lines.append("")

    cost = report["cost_per_adjudication_usd"]
    lines.append(f"Cost per adjudication, priced runs only "
                 f"(n={cost['n_priced_runs']}, unpriced {cost['n_unpriced_runs']})")
    if cost["n_priced_runs"]:
        lines.append(
            f"  min ${cost['min']:.4f}   p50 "
            f"{('$%.4f' % cost['p50']) if cost['p50'] is not None else 'n/a'}   "
            f"p95 {('$%.4f' % cost['p95']) if cost['p95'] is not None else 'n/a'}   "
            f"max ${cost['max']:.4f}   mean ${cost['mean']:.4f}"
        )
    else:
        lines.append(f"  {cost['note']}")
    lines.append("")

    lines.append("Per-specialist wall clock (seconds), successful calls only")
    lines.append(f"  {'domain':<12}{'model':<46}{'n':>4}{'fail':>5}"
                 f"{'min':>8}{'p50':>8}{'p95':>8}{'max':>8}{'slowest':>8}")
    for domain, data in report["per_specialist"].items():
        wall = data["wall_clock_successful_only"]
        lines.append(
            f"  {domain:<12}{data['model_id']:<46}{wall.get('n', 0):>4}"
            f"{data['n_failed']:>5}"
            + "".join(
                _fmt_seconds(wall.get(k), 8) for k in ("min_ms", "p50_ms", "p95_ms", "max_ms")
            )
            + f"{data['n_slowest_in_run']:>8}"
        )
    lines.append("  ('slowest' = how many runs this dimension was the fan-out's tail)")
    lines.append("")

    lines.append("Per-application end-to-end (seconds)")
    lines.append(f"  {'app':<12}{'n':>4}{'min':>9}{'p50':>9}{'max':>9}"
                 f"{'cost mean':>11}  {'verdict':<14}{'stable':>5}")
    for app_id, data in report["per_application"].items():
        e2e = data["end_to_end"]
        cost_mean = data["cost_usd"]["mean"]
        verdict = (data["overall_risk_decisions_observed"] or ["n/a"])[0]
        lines.append(
            f"  {app_id:<12}{e2e['n']:>4}"
            + "".join(_fmt_seconds(e2e.get(k), 9) for k in ("min_ms", "p50_ms", "max_ms"))
            + (f"{cost_mean:>11.4f}" if cost_mean is not None else f"{'n/a':>11}")
            + f"  {verdict:<14}"
            + f"{'yes' if data['verdict_stable_across_repetitions'] else 'NO':>5}"
        )
    lines.append("")

    stability = report["verdict_stability"]
    lines.append(
        f"Verdict agreement across repetitions "
        f"({stability['stable_applications']} of {stability['n_applications']} stable)"
    )
    if stability["unstable_applications"]:
        for app_id in stability["unstable_applications"]:
            data = stability["per_application"][app_id]
            lines.append(
                f"  UNSTABLE {app_id}: "
                f"{' / '.join(data['observed_overall_risk_decisions'])} "
                f"(pinned {data['expected_overall_risk_decision']})"
            )
    if stability["applications_disagreeing_with_pinned_expectation"]:
        for app_id in stability["applications_disagreeing_with_pinned_expectation"]:
            data = stability["per_application"][app_id]
            lines.append(
                f"  DIFFERS FROM PIN {app_id}: matched pinned "
                f"{data['expected_overall_risk_decision']} in "
                f"{data['n_runs_matching_expectation']}/{data['n_runs']} runs "
                f"(observed {' / '.join(data['observed_overall_risk_decisions'])})"
            )
    lines.append(f"  {stability['note']}")
    lines.append("")

    failures = report["failures"]
    lines.append(f"Failures: {failures['n_degraded_runs']} degraded run(s)")
    if failures["by_kind"]:
        for kind, count in failures["by_kind"].items():
            lines.append(f"  {kind:<28}{count}")
        for row in failures["by_run"]:
            lines.append(
                f"  {row['application_id']} rep {row['repetition']}: "
                f"{', '.join(row['failure_kinds']) or row['synthesis_error']}"
            )
    else:
        lines.append("  none")
    lines.append("")

    lines.append("CAVEATS (also in the JSON, verbatim)")
    for caveat in report["caveats"]:
        lines.append(f"  - {caveat}")
    lines.append("=" * 96)
    return "\n".join(lines)


def _fmt_seconds(ms: Any, width: int) -> str:
    """Render a millisecond figure as seconds, or a right-aligned n/a."""
    if ms is None:
        return "n/a".rjust(width)
    return f"{float(ms) / 1000.0:,.2f}".rjust(width)


def render_pipeline_markdown(report: dict[str, Any]) -> str:
    """A README-pasteable summary containing only numbers this run produced."""
    run = report["run"]
    e2e = report["end_to_end"]
    fan = report["fan_out"]
    syn = report["synthesis"]
    target = report["target"]
    speed = report["parallel_speedup"]
    cost = report["cost_per_adjudication_usd"]
    n = report["totals"]["runs"]

    def sec(block: dict[str, Any], key: str) -> str:
        value = block.get(key)
        return "**refused**" if value is None else f"{value / 1000.0:.2f}s"

    def ratio(block: dict[str, Any], key: str) -> str:
        value = block.get(key)
        return "**refused**" if value is None else f"{value:.2f}x"

    def money(block: dict[str, Any], key: str) -> str:
        value = block.get(key)
        return "**refused**" if value is None else f"${value:.4f}"

    lines: list[str] = []
    lines.append(
        f"### Measured pipeline benchmark — {len(report['workload']['application_ids'])} "
        f"applications x {report['workload']['repetitions_per_application']} "
        f"repetitions, n={n} adjudications"
    )
    lines.append("")
    lines.append(
        f"`{run['mode']}`, region `{run['aws_region']}` "
        f"(synthesizer `{run.get('synthesizer_region')}`), git "
        f"`{(run['git_sha'] or '')[:12]}`, {run['timestamp_utc']}."
    )
    lines.append("")
    lines.append("| stage | n | min | p50 | p95 | max |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for label, block in (
        ("end to end", e2e),
        ("fan-out (8 specialists)", fan),
        ("synthesis", syn),
        ("sum-of-eight (sequential counterfactual)",
         report["sum_of_eight_sequential_counterfactual"]),
    ):
        if not block.get("n"):
            continue
        lines.append(
            f"| {label} | {block['n']} | {sec(block, 'min_ms')} | {sec(block, 'p50_ms')} "
            f"| {sec(block, 'p95_ms')} | {sec(block, 'max_ms')} |"
        )
    lines.append("")
    if e2e.get("p95_ms") is None and "p95" in e2e.get("suppressed", {}):
        lines.append(f"p95 is refused, not omitted: {e2e['suppressed']['p95']}")
        lines.append("")
    lines.append(
        f"**Under the {target['target_seconds']:.0f}s target:** "
        f"{target['runs_under_target']} of {target['n_runs']} runs; "
        f"{target['applications_all_reps_under_target']} of "
        f"{target['n_applications']} applications on every repetition, "
        f"{target['applications_any_rep_under_target']} on at least one."
    )
    if target["applications_never_under_target"]:
        lines.append("")
        lines.append(
            "Never under target: "
            + ", ".join(f"`{a}`" for a in target["applications_never_under_target"])
            + "."
        )
    lines.append("")
    lines.append(
        f"**Parallel speed-up** (fan-out vs sum-of-eight, n={speed['n']}): "
        f"min {speed['min']:.2f}x, p50 {ratio(speed, 'p50')}, "
        f"p95 {ratio(speed, 'p95')}, max {speed['max']:.2f}x. "
        "One ratio per run — the fan-out is bounded by its slowest dimension, "
        "so the spread moves with which specialist ran long."
        if speed["n"]
        else "**Parallel speed-up:** not measured in this run."
    )
    lines.append("")
    if cost["n_priced_runs"]:
        lines.append(
            f"**Cost per adjudication** (n={cost['n_priced_runs']} fully priced runs, "
            f"{cost['n_unpriced_runs']} excluded): min ${cost['min']:.4f}, "
            f"p50 {money(cost, 'p50')}, max ${cost['max']:.4f}."
        )
    else:
        lines.append(f"**Cost per adjudication:** {cost['note']}")
    lines.append("")
    lines.append("| specialist | model | n ok | n failed | p50 | p95 | max | tail in N runs |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for domain, data in report["per_specialist"].items():
        wall = data["wall_clock_successful_only"]
        lines.append(
            f"| `{domain}` | `{data['model_id']}` | {wall.get('n', 0)} | "
            f"{data['n_failed']} | {sec(wall, 'p50_ms')} | {sec(wall, 'p95_ms')} | "
            f"{sec(wall, 'max_ms')} | {data['n_slowest_in_run']} |"
        )
    lines.append("")
    stability = report["verdict_stability"]
    lines.append(
        f"**Verdict agreement:** {stability['stable_applications']} of "
        f"{stability['n_applications']} applications returned the same "
        f"`overall_risk_decision` on every repetition."
    )
    if stability["unstable_applications"]:
        lines.append("")
        for app_id in stability["unstable_applications"]:
            data = stability["per_application"][app_id]
            lines.append(
                f"- `{app_id}` was unstable: "
                + " / ".join(data["observed_overall_risk_decisions"])
                + f" across {data['n_runs']} runs (fixture pins "
                f"{data['expected_overall_risk_decision']})."
            )
    if stability["applications_disagreeing_with_pinned_expectation"]:
        lines.append("")
        for app_id in stability["applications_disagreeing_with_pinned_expectation"]:
            data = stability["per_application"][app_id]
            lines.append(
                f"- `{app_id}` matched its pinned "
                f"{data['expected_overall_risk_decision']} in "
                f"{data['n_runs_matching_expectation']} of {data['n_runs']} runs "
                "(observed "
                + " / ".join(data["observed_overall_risk_decisions"])
                + ")."
            )
    lines.append("")
    lines.append(
        "This is reported, not adjudicated. Whether the pin or the model is wrong is "
        "the customer's call; run the calibration judge in `evals/evaluators/` before "
        "drawing an accuracy conclusion."
    )
    lines.append("")
    failures = report["failures"]
    if failures["by_kind"]:
        lines.append(
            f"**Failures:** {failures['n_degraded_runs']} degraded run(s) — "
            + ", ".join(f"{k} x{v}" for k, v in failures["by_kind"].items())
            + ". Degraded runs stay in the latency distribution and are excluded "
            "from the cost distribution."
        )
    else:
        lines.append(
            f"**Failures:** none. {report['totals']['specialist_calls']} specialist "
            f"calls and {n} synthesis calls, no throttling, no truncation, "
            f"{report['totals']['validated_adjudications']} of {n} adjudications "
            "validated against the 21-key contract."
        )
    lines.append("")
    lines.append(
        "Not measured: AgentCore runtime overhead (these calls leave this host "
        "directly), signal generation (precomputed, outside the timed window), and "
        "throughput (one application in flight at a time)."
    )
    return "\n".join(lines)


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
    mode.add_argument(
        "--reaggregate",
        dest="reaggregate_path",
        default=None,
        help=(
            "recompute a committed pipeline report's aggregates from its own raw_runs. "
            "Calls no model and spends nothing; use it when the aggregation changes but "
            "the measurements have not."
        ),
    )
    mode.add_argument(
        "--pipeline",
        action="store_true",
        help=(
            "run the SHIPPED pipeline (agents.fanout + agents.synthesize) over every "
            f"fixture; live unless --mock, and live needs {LIVE_ENV_GATE}=1"
        ),
    )
    parser.add_argument("--mock", action="store_true",
                        help="pipeline only: MOCK_MODE, no model call, no spend")
    parser.add_argument("--repetitions", type=int, default=PIPELINE_DEFAULT_REPETITIONS,
                        help=f"pipeline only: runs per application (default {PIPELINE_DEFAULT_REPETITIONS})")
    parser.add_argument("--markdown", dest="markdown_path", default=None,
                        help="pipeline only: write the README-pasteable summary here")
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


def pipeline_plan(
    application_ids: Sequence[str],
    repetitions: int,
    *,
    mock: bool,
    region: str,
    models: dict[str, str],
    per_application_usd: float | None,
) -> str:
    """The plan and the estimated spend, printed BEFORE anything is called.

    ``per_application_usd`` is a PRIOR from an earlier measured run, not a
    prediction of this one, and the text says so. It exists so a human can approve
    or refuse the spend; the report never carries it.
    """
    runs = len(application_ids) * repetitions
    calls = runs * (len(DOMAINS) + 1)
    lines = [
        "PIPELINE BENCH PLAN",
        f"  mode           : {'MOCK (no model called, no spend)' if mock else 'LIVE Bedrock + bedrock-mantle'}",
        f"  applications   : {len(application_ids)} ({application_ids[0]}..{application_ids[-1]})",
        f"  repetitions    : {repetitions} per application, round-robin",
        f"  adjudications  : {runs}",
        f"  model calls    : {calls}  ({len(DOMAINS)} specialists + 1 synthesis per adjudication)",
        f"  region         : {region}",
        "  models         :",
    ]
    for agent, model_id in models.items():
        lines.append(f"    {agent:<13}{model_id}")
    if mock:
        lines.append("  estimated spend: $0.00 -- no model is called in mock mode")
    elif per_application_usd is None:
        lines.append("  estimated spend: UNKNOWN -- no prior per-application measurement to scale")
    else:
        lines.append(
            f"  estimated spend: ~${per_application_usd * runs:.2f} "
            f"({runs} x ${per_application_usd:.3f}/application). This is a PRIOR from a "
            "previously measured run, used only to size the budget. The report will "
            "carry the cost THIS run measures, not this figure."
        )
    lines.append(
        "  percentiles    : "
        f"pooled n={runs}; p50 needs {PERCENTILE_MIN_SAMPLES['p50']}, "
        f"p95 needs {PERCENTILE_MIN_SAMPLES['p95']}, p99 needs "
        f"{PERCENTILE_MIN_SAMPLES['p99']}. "
        f"Per application n={repetitions}, so per-application percentiles will be "
        "suppressed."
    )
    return "\n".join(lines)


#: A prior from the single measured APP-1004 live run recorded in the repo README,
#: used ONLY to size the budget line in the plan above. It is never copied into a
#: report and never presented as a measurement of a new run.
PRIOR_MEASURED_COST_PER_APPLICATION_USD: Final = 0.245


def _main_pipeline(args: argparse.Namespace) -> int:
    """``--pipeline``: print the plan, gate the spend, run, write both reports."""
    from fixtures.loader import list_application_ids  # noqa: PLC0415

    ids = list(args.applications or list_application_ids())
    if not ids:
        print("no application ids to run", file=sys.stderr)
        return 2
    if args.repetitions < 1:
        print("--repetitions must be >= 1", file=sys.stderr)
        return 2

    mock = bool(args.mock)
    if not mock:
        gate = live_gate_error(args_live=True)
        if gate:
            print(f"refusing to run the live pipeline: {gate}", file=sys.stderr)
            print("  (pass --mock for a no-spend structural run)", file=sys.stderr)
            return 2

    # MOCK_MODE is read at import time by agents.fanout, so it must be set before
    # the first import of that module -- which is why run_pipeline imports lazily.
    os.environ["MOCK_MODE"] = "1" if mock else "0"
    if not mock:
        os.environ.setdefault("AWS_REGION", args.region)

    models = resolve_specialist_models()
    print(
        pipeline_plan(
            ids,
            args.repetitions,
            mock=mock,
            region=args.region,
            models=models,
            per_application_usd=(
                None if mock else PRIOR_MEASURED_COST_PER_APPLICATION_USD
            ),
        ),
        flush=True,
    )
    print("", flush=True)

    def progress(line: str) -> None:
        print(f"  {line}", flush=True)

    report = asyncio.run(
        run_pipeline(ids, args.repetitions, mock=mock, region=args.region, progress=progress)
    )
    print("")
    print(render_pipeline_human(report))
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote machine-readable report to {args.json_path}")
    if args.markdown_path:
        Path(args.markdown_path).write_text(
            render_pipeline_markdown(report) + "\n", encoding="utf-8"
        )
        print(f"wrote markdown summary to {args.markdown_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.reaggregate_path:
        source = Path(args.reaggregate_path)
        report = reaggregate_pipeline_report(
            json.loads(source.read_text(encoding="utf-8"))
        )
        print(render_pipeline_human(report))
        target = Path(args.json_path) if args.json_path else source
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nrebuilt aggregates from {source} -> {target} (no model was called)")
        if args.markdown_path:
            Path(args.markdown_path).write_text(
                render_pipeline_markdown(report) + "\n", encoding="utf-8"
            )
            print(f"wrote markdown summary to {args.markdown_path}")
        return 0

    if args.pipeline:
        return _main_pipeline(args)

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
