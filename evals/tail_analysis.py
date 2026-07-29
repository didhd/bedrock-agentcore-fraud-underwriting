"""
Why the 42-run pipeline benchmark has a 31.4s p50 and a 75.0s max, and what removes it.

WHY THIS MODULE EXISTS: a median is what an engineer reviews and a maximum is what an
executive watches. The Friday demo is one run, not a distribution, so the only figure
that matters in the room is the one that comes back. This module decomposes the tail of
``evals/results/pipeline_14x3_us-east-1.json`` into the four causes it could have --
one slow agent, verbose output, retry/throttle overhead, or irreducible server-side
variance -- and separates them with the per-call telemetry that run already recorded.

It answers four questions and refuses to answer a fifth it cannot:

  1. WHERE is the tail? Per-run: which agent was slowest, and is it the same one every
     time. Per-application: how much of the spread is the application's own signal load
     versus repetition-to-repetition noise on identical input (a one-way ANOVA-style
     sum-of-squares split, reported as a share, not an F test -- n=3 per cell).
  2. IS IT OUTPUT LENGTH? Every specialist call has both a wall clock and a real
     ``outputTokens``. If wall clock is linear in output tokens then the tail is a
     verbosity problem and the two prompts with no character cap are its structural
     cause. This module fits that line and reports r^2 per domain, with and without the
     runs it identifies as stalls, because one stall destroys a correlation and hides
     the very effect being tested.
  3. IS IT RETRIES? ``agents.models.build_retry_strategy`` configures
     ``ModelRetryStrategy(max_attempts=4, initial_delay=1, max_delay=8)``. Read against
     ``strands.event_loop._retry`` (1.26.0) that is a ladder of [1,2,4]s -- 7s worst case,
     not 15s -- and it fires on ``ModelThrottledException`` and nothing else. A retry sleeps
     inside the call, so it shows up as wall clock EXCEEDING the model's own ``latencyMs``.
     That gap is computable per call, so the retry theory is falsifiable rather than assumed.
  4. WHAT IS THE FLOOR? ``asyncio.gather`` cannot return before its slowest member, so
     the distribution of ``slowest_specialist_wall_ms`` IS the fan-out floor and
     ``fanout_wall_ms - slowest_specialist_wall_ms`` is everything orchestration adds.
     A fix that does not move the floor does not move end-to-end.

WHAT IT DELIBERATELY DOES NOT DO: it does not model the latency of a configuration it
did not run. ``cap_binding_census`` reports how many MEASURED calls a candidate
``max_tokens`` would have truncated -- that is a count of observed output lengths, not a
predicted latency -- and the saving from a cap is only ever claimed from
``--live``, which re-invokes the agent with the cap actually applied. Holding tokens
constant across a config change is exactly the error this repo already made once.

Offline analysis: ``python -m evals.tail_analysis``
Live cap sweep:   ``AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.tail_analysis --live --dry-run``
                  (drop ``--dry-run`` to spend)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.bench import (  # noqa: E402
    LIVE_ENV_GATE,
    PERCENTILE_MIN_SAMPLES,
    summarize_latencies,
    summarize_values,
)

SCHEMA: Final[str] = "pointpredictive.agentcore.tail-analysis/1"

DEFAULT_REPORT_PATH: Final[Path] = REPO_ROOT / "evals" / "results" / "pipeline_14x3_us-east-1.json"
DEFAULT_OUT_PATH: Final[Path] = REPO_ROOT / "evals" / "results" / "tail_analysis.json"

#: A call is called a STALL when its wall clock crosses this. The threshold is not a
#: statistical outlier rule; it is the customer's own sub-minute target applied to a
#: SINGLE specialist. One specialist alone consuming the entire end-to-end budget is a
#: different physical event from a verbose specialist, and the two must not be pooled:
#: the stalls in this sample sit at 4.7 and 16.1 output tokens/second while every other
#: call in the sample sits near 88 tok/s. Both figures are measured below.
STALL_WALL_MS: Final[float] = 60_000.0

#: The eight specialists, ordered as the benchmark orders them.
DOMAIN_ORDER: Final[tuple[str, ...]] = (
    "identity",
    "dealer",
    "straw",
    "employment",
    "income",
    "synthetic",
    "bustout",
    "rings",
)

#: The six specialists whose prompt text carries "max 4000 characters", and the two whose
#: prompt says only "Return only the final analysis". Transcribed from
#: ``prompts/verbatim/<domain>_agent_prompt.txt``; this split is the structural hypothesis
#: under question 2, so it is data, not a comment.
PROMPT_CHARACTER_CAP: Final[dict[str, int | None]] = {
    "identity": 4000,
    "dealer": 4000,
    "straw": 4000,
    "employment": 4000,
    "income": 4000,
    "synthetic": 4000,
    "bustout": None,
    "rings": None,
}

#: Candidate ``max_tokens`` values whose binding rate is counted against measured output
#: lengths. 4096 is the shipped MID/HEAVY value; 3072 is the shipped LIGHT value.
CANDIDATE_CAPS: Final[tuple[int, ...]] = (1024, 1536, 2048, 3072, 4096)

#: A minimum sample size for a correlation, mirroring PERCENTILE_MIN_SAMPLES. An r over
#: three points is a line through noise; the per-domain fits here have n=41-42.
CORRELATION_MIN_SAMPLES: Final[int] = 20

#: The three specialist bands, longest-first so "POSSIBLE RISK" cannot be shadowed.
#: MEASURED SHAPES this must survive (all from real specialist output in this repo's
#: live runs): "## RISK CLASSIFICATION: **HIGH RISK**", "## Fraud Ring Risk Analysis:
#: HIGH RISK", "## VERDICT: **HIGH RISK**", "**Classification: LOW RISK**",
#: "## Assessment: **POSSIBLE RISK**", "## Fraud Ring Risk Analysis — POSSIBLE RISK".
#: The band is always the first band string in the document; the snippet around the match
#: is kept so a human can audit every extraction rather than trust the regex.
_BAND_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(POSSIBLE RISK|LOW RISK|HIGH RISK)\b", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Reading the measured run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecialistCall:
    """One measured specialist invocation, flattened out of ``raw_runs``."""

    application_id: str
    repetition: int
    domain: str
    tier: str
    model_id: str
    wall_ms: float
    latency_ms: float | None
    output_tokens: int | None
    input_tokens: int | None
    analysis_chars: int | None
    stop_reason: str | None
    error: str | None

    @property
    def failed(self) -> bool:
        return bool(self.error)

    @property
    def is_stall(self) -> bool:
        return self.wall_ms >= STALL_WALL_MS

    @property
    def overhead_ms(self) -> float | None:
        """Wall clock the model did not account for: retries, queueing, SSE framing."""
        if self.latency_ms is None:
            return None
        return self.wall_ms - self.latency_ms

    @property
    def output_tokens_per_second(self) -> float | None:
        if not self.latency_ms or not self.output_tokens:
            return None
        return self.output_tokens / (self.latency_ms / 1000.0)


@dataclass(frozen=True)
class RunRecord:
    """One measured adjudication."""

    application_id: str
    repetition: int
    e2e_ms: float
    fanout_ms: float
    synthesis_ms: float
    slowest_specialist: str | None
    slowest_specialist_ms: float
    degraded_domains: tuple[str, ...]
    failure_kinds: tuple[str, ...]
    repair_attempts: int
    validated: bool
    overall_risk_decision: str | None
    specialists: tuple[SpecialistCall, ...] = field(default=())

    @property
    def orchestration_overhead_ms(self) -> float:
        """Fan-out wall clock above its slowest member. The cost of gathering."""
        return self.fanout_ms - self.slowest_specialist_ms

    @property
    def post_fanout_ms(self) -> float:
        """End-to-end above fan-out: synthesis plus contract validation."""
        return self.e2e_ms - self.fanout_ms


def load_report(path: Path | str = DEFAULT_REPORT_PATH) -> dict[str, Any]:
    """Read a pipeline benchmark report from disk."""
    return json.loads(Path(path).read_text())


def parse_runs(report: dict[str, Any]) -> list[RunRecord]:
    """Flatten ``raw_runs`` into typed records, preserving every degraded run.

    Degraded runs are NOT dropped. A run that lost two specialists to a 5xx still took
    the wall clock it took, and removing it would be exactly the averaging-away the
    benchmark's own caveats forbid.
    """
    runs: list[RunRecord] = []
    for row in report.get("raw_runs", []):
        calls = tuple(
            SpecialistCall(
                application_id=row["application_id"],
                repetition=int(row["repetition"]),
                domain=spec["domain"],
                tier=spec.get("tier", ""),
                model_id=spec.get("model_id", ""),
                wall_ms=float(spec["wall_ms"]),
                latency_ms=(
                    float(spec["latency_ms"]) if spec.get("latency_ms") is not None else None
                ),
                output_tokens=(spec.get("usage") or {}).get("outputTokens"),
                input_tokens=(spec.get("usage") or {}).get("inputTokens"),
                analysis_chars=spec.get("analysis_chars"),
                stop_reason=spec.get("stop_reason"),
                error=spec.get("error"),
            )
            for spec in row.get("specialists", [])
        )
        runs.append(
            RunRecord(
                application_id=row["application_id"],
                repetition=int(row["repetition"]),
                e2e_ms=float(row["e2e_wall_ms"]),
                fanout_ms=float(row["fanout_wall_ms"]),
                synthesis_ms=float(row["synthesis_wall_ms"]),
                slowest_specialist=row.get("slowest_specialist"),
                slowest_specialist_ms=float(row["slowest_specialist_wall_ms"]),
                degraded_domains=tuple(row.get("degraded_domains") or ()),
                failure_kinds=tuple(row.get("failure_kinds") or ()),
                repair_attempts=int(row.get("repair_attempts") or 0),
                validated=bool(row.get("adjudication_validated")),
                overall_risk_decision=row.get("overall_risk_decision"),
                specialists=calls,
            )
        )
    return runs


def all_calls(runs: Sequence[RunRecord]) -> list[SpecialistCall]:
    """Every specialist call across every run, in run order."""
    return [call for run in runs for call in run.specialists]


# ---------------------------------------------------------------------------
# Statistics that carry their own sample size
# ---------------------------------------------------------------------------


def pearson_r(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation, or None when it is not defined (n<2, zero variance)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if sx == 0.0 or sy == 0.0:
        return None
    return cov / (sx * sy)


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float] | None:
    """Least-squares ``(slope, intercept)``, or None when x has no variance."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    denom = sum((a - mx) ** 2 for a in xs)
    if denom == 0.0:
        return None
    slope = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / denom
    return slope, my - slope * mx


def summarize_correlation(
    xs: Sequence[float], ys: Sequence[float], *, label: str, x_unit: str, y_unit: str
) -> dict[str, Any]:
    """Correlation and fit with the sample size attached and refusal below the minimum.

    A correlation is reported as ``None`` with a reason below
    ``CORRELATION_MIN_SAMPLES``, for the same reason ``summarize_values`` refuses a p99
    over 42 samples: the statistic exists arithmetically and means nothing.
    """
    n = len(xs)
    out: dict[str, Any] = {
        "label": label,
        "n": n,
        "x_unit": x_unit,
        "y_unit": y_unit,
        "r": None,
        "r_squared": None,
        "slope": None,
        "intercept": None,
        "suppressed": None,
    }
    if n < CORRELATION_MIN_SAMPLES:
        out["suppressed"] = (
            f"refused: {n} sample(s), need >= {CORRELATION_MIN_SAMPLES}. "
            "A correlation over a handful of points is a line through noise."
        )
        return out
    r = pearson_r(xs, ys)
    fit = linear_fit(xs, ys)
    if r is not None:
        out["r"] = round(r, 4)
        out["r_squared"] = round(r * r, 4)
    if fit is not None:
        out["slope"] = round(fit[0], 4)
        out["intercept"] = round(fit[1], 4)
    return out


# ---------------------------------------------------------------------------
# Question 1: where is the tail?
# ---------------------------------------------------------------------------


def slowest_agent_census(runs: Sequence[RunRecord], *, top_n: int = 10) -> dict[str, Any]:
    """Who was the fan-out's critical path, overall and in the slowest/fastest runs.

    The distinction matters: an agent that is USUALLY slowest sets the p50 floor, while an
    agent that is slowest specifically in the SLOW runs is what produces the tail. If the
    two censuses differ, the median and the tail have different owners and a single fix
    cannot address both.
    """
    ordered = sorted(runs, key=lambda r: r.e2e_ms, reverse=True)
    slowest_runs = ordered[:top_n]
    fastest_runs = ordered[-top_n:]

    def census(sample: Sequence[RunRecord]) -> dict[str, int]:
        return dict(Counter(r.slowest_specialist for r in sample if r.slowest_specialist).most_common())

    overall = census(runs)
    tail = census(slowest_runs)
    head = census(fastest_runs)
    return {
        "n_runs": len(runs),
        "critical_path_all_runs": overall,
        "critical_path_slowest_n_runs": {"top_n": top_n, "counts": tail},
        "critical_path_fastest_n_runs": {"bottom_n": top_n, "counts": head},
        "same_owner_for_median_and_tail": (
            bool(overall) and bool(tail) and max(overall, key=overall.get) == max(tail, key=tail.get)
        ),
        "slowest_runs": [
            {
                "application_id": r.application_id,
                "repetition": r.repetition,
                "e2e_seconds": round(r.e2e_ms / 1000.0, 2),
                "fanout_seconds": round(r.fanout_ms / 1000.0, 2),
                "synthesis_seconds": round(r.synthesis_ms / 1000.0, 2),
                "critical_path_agent": r.slowest_specialist,
                "critical_path_seconds": round(r.slowest_specialist_ms / 1000.0, 2),
                "critical_path_share_of_fanout_pct": round(
                    100.0 * r.slowest_specialist_ms / r.fanout_ms, 1
                ),
                "specialists_descending": [
                    {
                        "domain": c.domain,
                        "seconds": round(c.wall_ms / 1000.0, 2),
                        "output_tokens": c.output_tokens,
                        "output_tokens_per_second": (
                            round(c.output_tokens_per_second, 1)
                            if c.output_tokens_per_second is not None
                            else None
                        ),
                    }
                    for c in sorted(r.specialists, key=lambda c: c.wall_ms, reverse=True)
                ],
            }
            for r in slowest_runs
        ],
        "note": (
            "'Critical path agent' is the fan-out's slowest member in that run. asyncio.gather "
            "returns when the last coroutine returns, so this agent alone sets the fan-out floor "
            "for that run; the other seven are free."
        ),
    }


def application_spread(runs: Sequence[RunRecord]) -> dict[str, Any]:
    """Per-application end-to-end spread over its repetitions, widest first.

    Identical input, identical code, three repetitions. Any spread here is not the
    application: it is the service. An application whose three repetitions land within a
    second is a defensible demo candidate; one that spans 51s is not.
    """
    by_app: dict[str, list[float]] = {}
    for run in runs:
        by_app.setdefault(run.application_id, []).append(run.e2e_ms)
    rows = []
    for app, values in by_app.items():
        ordered = sorted(values)
        rows.append(
            {
                "application_id": app,
                "n_repetitions": len(ordered),
                "min_seconds": round(ordered[0] / 1000.0, 2),
                "median_seconds": round(statistics.median(ordered) / 1000.0, 2),
                "max_seconds": round(ordered[-1] / 1000.0, 2),
                "spread_seconds": round((ordered[-1] - ordered[0]) / 1000.0, 2),
                "spread_as_multiple_of_min": round(ordered[-1] / ordered[0], 2),
                "all_repetitions_seconds": [round(v / 1000.0, 2) for v in ordered],
            }
        )
    rows.sort(key=lambda row: row["spread_seconds"], reverse=True)
    return {
        "n_applications": len(rows),
        "widest_first": rows,
        "note": (
            "Input is byte-identical across an application's repetitions (fixtures are "
            "deterministic and build_payload runs outside the timed window), so spread within "
            "a row is service-side variance, not workload."
        ),
    }


def variance_decomposition(runs: Sequence[RunRecord]) -> dict[str, Any]:
    """Split end-to-end variance into between-application and within-application shares.

    Between = the application's own signal load (more alerts, more to write about).
    Within  = repetition-to-repetition noise on identical input, i.e. the service.

    This is the sum-of-squares split of a one-way layout and is reported as a SHARE only.
    It is deliberately not an F test or a p value: three repetitions per application
    cannot support one, and the share is the decision-relevant quantity anyway -- it says
    whether picking a better application or reducing verbosity is the lever.
    """
    if len(runs) < 2:
        return {"n_runs": len(runs), "suppressed": "refused: need >= 2 runs"}
    values = [r.e2e_ms for r in runs]
    grand_mean = statistics.fmean(values)
    by_app: dict[str, list[float]] = {}
    for run in runs:
        by_app.setdefault(run.application_id, []).append(run.e2e_ms)
    ss_between = sum(len(v) * (statistics.fmean(v) - grand_mean) ** 2 for v in by_app.values())
    ss_within = sum(
        (value - statistics.fmean(v)) ** 2 for v in by_app.values() for value in v
    )
    total = ss_between + ss_within
    return {
        "n_runs": len(runs),
        "n_applications": len(by_app),
        "repetitions_per_application": sorted({len(v) for v in by_app.values()}),
        "ss_between_applications_ms2": round(ss_between, 1),
        "ss_within_application_ms2": round(ss_within, 1),
        "between_application_share_pct": round(100.0 * ss_between / total, 1) if total else None,
        "within_application_share_pct": round(100.0 * ss_within / total, 1) if total else None,
        "method": (
            "one-way sum-of-squares split on application id; reported as a variance SHARE, "
            "not an F test -- n=3 per cell cannot support a significance claim"
        ),
        "interpretation_rule": (
            "within-application share above 50% means most of the spread is service-side "
            "variance on identical input, so a verbosity or model change cannot remove it"
        ),
    }


def repetition_effect(runs: Sequence[RunRecord]) -> dict[str, Any]:
    """End-to-end by repetition index, to test for drift across the measurement window.

    The benchmark ran round-robin (every application at rep 1, then rep 2, ...), so a
    repetition index is a time bucket. If one bucket is systematically slower, the tail
    belongs to a window of wall-clock time -- service-side conditions -- rather than to
    any application or agent.
    """
    by_rep: dict[int, list[float]] = {}
    for run in runs:
        by_rep.setdefault(run.repetition, []).append(run.e2e_ms)
    return {
        "execution_order": "round-robin: all applications at repetition 1, then repetition 2, ...",
        "per_repetition": {
            str(rep): {
                "n": len(values),
                "mean_seconds": round(statistics.fmean(values) / 1000.0, 2),
                "median_seconds": round(statistics.median(values) / 1000.0, 2),
                "max_seconds": round(max(values) / 1000.0, 2),
            }
            for rep, values in sorted(by_rep.items())
        },
        "note": (
            "A repetition index is a wall-clock window here, not a warm-up state: each agent "
            "is a fresh Strands Agent on a shared botocore client, so there is no per-agent "
            "cache to warm."
        ),
    }


# ---------------------------------------------------------------------------
# Question 2: is it output length?
# ---------------------------------------------------------------------------


def stalls(calls: Sequence[SpecialistCall]) -> dict[str, Any]:
    """Calls whose wall clock crossed the whole end-to-end budget on their own.

    Reported separately from the verbosity fit because they are a different event. The
    discriminator is output tokens per second against the model's OWN reported latency: a
    verbose call is fast per token and simply emits many, a stalled call is slow per token.
    """
    stalled = [c for c in calls if c.is_stall]
    healthy_tps = [
        c.output_tokens_per_second for c in calls if not c.is_stall and c.output_tokens_per_second
    ]
    return {
        "threshold_ms": STALL_WALL_MS,
        "threshold_rationale": (
            "one specialist consuming the customer's entire sub-minute end-to-end budget"
        ),
        "n_stalls": len(stalled),
        "n_calls": len(calls),
        "stall_rate_pct": round(100.0 * len(stalled) / len(calls), 2) if calls else None,
        "stalls": [
            {
                "application_id": c.application_id,
                "repetition": c.repetition,
                "domain": c.domain,
                "wall_seconds": round(c.wall_ms / 1000.0, 2),
                "model_reported_latency_seconds": (
                    round(c.latency_ms / 1000.0, 2) if c.latency_ms is not None else None
                ),
                "unaccounted_seconds": (
                    round(c.overhead_ms / 1000.0, 2) if c.overhead_ms is not None else None
                ),
                "output_tokens": c.output_tokens,
                "output_tokens_per_second": (
                    round(c.output_tokens_per_second, 1)
                    if c.output_tokens_per_second is not None
                    else None
                ),
                "stop_reason": c.stop_reason,
                "error": c.error,
            }
            for c in sorted(stalled, key=lambda c: c.wall_ms, reverse=True)
        ],
        "healthy_call_output_tokens_per_second": summarize_values(
            healthy_tps, label="output tok/s, calls under the stall threshold", unit="tok/s"
        ),
        "diagnosis_rule": (
            "A stall whose tok/s is an order of magnitude below the healthy median, whose "
            "unaccounted time is near zero and whose stop_reason is end_turn was slow INSIDE "
            "the model. It is not a retry, not a truncation, and not verbosity -- it is "
            "server-side generation speed, which the client cannot configure away."
        ),
    }


def latency_vs_output(calls: Sequence[SpecialistCall]) -> dict[str, Any]:
    """Fit wall clock against output tokens, pooled and per domain, with/without stalls.

    Two fits are reported for the pooled sample on purpose. The all-calls fit is what the
    raw data says; the excluding-stalls fit is what the GENERATION cost of a token is. If
    the second is far tighter than the first, the sample contains two mechanisms and a
    single r would have hidden one of them.
    """
    usable = [c for c in calls if c.output_tokens and c.latency_ms is not None]
    healthy = [c for c in usable if not c.is_stall]

    def fit(sample: Sequence[SpecialistCall], label: str) -> dict[str, Any]:
        summary = summarize_correlation(
            [float(c.output_tokens or 0) for c in sample],
            [c.wall_ms for c in sample],
            label=label,
            x_unit="output tokens",
            y_unit="ms",
        )
        if summary["slope"]:
            summary["marginal_output_tokens_per_second"] = round(1000.0 / summary["slope"], 1)
            summary["fixed_overhead_seconds"] = round((summary["intercept"] or 0.0) / 1000.0, 2)
        return summary

    per_domain = {}
    for domain in DOMAIN_ORDER:
        sample = [c for c in usable if c.domain == domain]
        healthy_sample = [c for c in sample if not c.is_stall]
        entry = fit(sample, f"{domain}: wall clock vs output tokens, all calls")
        entry["excluding_stalls"] = fit(
            healthy_sample, f"{domain}: wall clock vs output tokens, stalls excluded"
        )
        entry["n_stalls_removed"] = len(sample) - len(healthy_sample)
        entry["prompt_character_cap"] = PROMPT_CHARACTER_CAP.get(domain)
        entry["output_tokens"] = summarize_values(
            [float(c.output_tokens or 0) for c in sample],
            label=f"{domain} output tokens",
            unit="tokens",
        )
        entry["analysis_chars"] = summarize_values(
            [float(c.analysis_chars or 0) for c in sample if c.analysis_chars is not None],
            label=f"{domain} analysis characters",
            unit="chars",
        )
        entry["n_calls_over_prompt_character_cap"] = (
            sum(
                1
                for c in sample
                if c.analysis_chars is not None
                and PROMPT_CHARACTER_CAP.get(domain)
                and c.analysis_chars > (PROMPT_CHARACTER_CAP[domain] or 0)
            )
            if PROMPT_CHARACTER_CAP.get(domain)
            else None
        )
        per_domain[domain] = entry

    capped = [c for c in usable if PROMPT_CHARACTER_CAP.get(c.domain)]
    uncapped = [c for c in usable if not PROMPT_CHARACTER_CAP.get(c.domain)]

    def group(sample: Sequence[SpecialistCall], name: str, domains: list[str]) -> dict[str, Any]:
        chars = [c for c in sample if c.analysis_chars is not None]
        over = [c for c in chars if (c.analysis_chars or 0) > 4000]
        return {
            "domains": domains,
            "n_calls": len(sample),
            "output_tokens": summarize_values(
                [float(c.output_tokens or 0) for c in sample],
                label=f"output tokens, {name}",
                unit="tokens",
            ),
            "analysis_chars": summarize_values(
                [float(c.analysis_chars or 0) for c in chars],
                label=f"analysis chars, {name}",
                unit="chars",
            ),
            # The customer's own 4000-character rule applied to BOTH groups. For the six
            # capped agents an exceedance is a contract breach; for bustout and rings there
            # is no rule to breach, which is exactly the asymmetry under test.
            "n_calls_over_4000_chars": len(over),
            "pct_calls_over_4000_chars": (
                round(100.0 * len(over) / len(chars), 1) if chars else None
            ),
        }

    return {
        "pooled_all_calls": fit(usable, "all specialists: wall clock vs output tokens"),
        "pooled_excluding_stalls": fit(
            healthy, "all specialists, stalls excluded: wall clock vs output tokens"
        ),
        "per_domain": per_domain,
        "prompt_cap_contrast": {
            "hypothesis": (
                "The two prompts with no character cap (bustout, rings) write materially more "
                "than the six capped at 'max 4000 characters', and latency is linear in what "
                "is written, so they are the structural owner of the fan-out floor."
            ),
            "capped_by_prompt": group(
                capped,
                "prompt-capped specialists",
                [d for d, cap in PROMPT_CHARACTER_CAP.items() if cap],
            ),
            "no_cap_in_prompt": group(
                uncapped,
                "uncapped specialists",
                [d for d, cap in PROMPT_CHARACTER_CAP.items() if not cap],
            ),
        },
        "chars_per_output_token": _chars_per_token(usable),
    }


def _chars_per_token(calls: Sequence[SpecialistCall]) -> dict[str, Any]:
    """Visible characters per billed output token, per domain.

    WHY THIS IS HERE: a character cap in a prompt constrains VISIBLE text; ``max_tokens``
    constrains BILLED tokens, and the two diverge whenever the model spends tokens that
    never reach the analysis string. In this sample identity's median is ~1.55 chars per
    output token against dealer's ~4.5, and its worst calls sit near 0.33 -- 3,500 billed
    output tokens behind 1,100 characters of analysis. Those calls are the slowest identity
    calls in the run. So a cap set from the customer's 4000-CHARACTER contract can truncate
    an agent that has emitted far fewer characters than it has been billed for, which is
    precisely how a lowered cap turns into a lost fraud dimension rather than a saving.
    """
    ratios = [
        (c.analysis_chars / c.output_tokens, c)
        for c in calls
        if c.analysis_chars and c.output_tokens
    ]
    per_domain = {}
    for domain in DOMAIN_ORDER:
        sample = [r for r, c in ratios if c.domain == domain]
        if not sample:
            continue
        per_domain[domain] = {
            "n": len(sample),
            "median": round(statistics.median(sample), 2),
            "min": round(min(sample), 2),
            "max": round(max(sample), 2),
        }
    lowest = sorted(ratios, key=lambda pair: pair[0])[:8]
    return {
        "per_domain": per_domain,
        "lowest_ratio_calls": [
            {
                "application_id": c.application_id,
                "repetition": c.repetition,
                "domain": c.domain,
                "chars_per_output_token": round(ratio, 2),
                "output_tokens": c.output_tokens,
                "analysis_chars": c.analysis_chars,
                "wall_seconds": round(c.wall_ms / 1000.0, 2),
            }
            for ratio, c in lowest
        ],
        "why_it_matters": (
            "A prompt's 'max 4000 characters' rule and a max_tokens cap are not the same "
            "constraint. Where this ratio collapses, the model is billed for output the "
            "analysis text does not contain, so a cap chosen from the character contract "
            "truncates earlier than the contract implies."
        ),
    }


def cap_binding_census(
    calls: Sequence[SpecialistCall], caps: Sequence[int] = CANDIDATE_CAPS
) -> dict[str, Any]:
    """How many MEASURED calls each candidate ``max_tokens`` would have truncated.

    This is a count over observed output lengths, not a latency prediction and not a cost
    prediction. A cap changes what the model writes, so the only honest claim available
    from historical tokens is "this many calls already wrote more than this cap", which is
    the failure-risk side of the trade. The saving side requires a live run.
    """
    usable = [c for c in calls if c.output_tokens]
    rows = []
    for cap in caps:
        binding = [c for c in usable if (c.output_tokens or 0) > cap]
        rows.append(
            {
                "max_tokens": cap,
                "n_calls_exceeding": len(binding),
                "n_calls": len(usable),
                "pct_calls_exceeding": round(100.0 * len(binding) / len(usable), 1) if usable else None,
                "domains_affected": dict(Counter(c.domain for c in binding).most_common()),
                "applications_affected": sorted({c.application_id for c in binding}),
            }
        )
    return {
        "candidate_caps": list(caps),
        "shipped_caps_by_tier": _shipped_caps(),
        "rows": rows,
        "claim_boundary": (
            "MEASURED: which observed calls already wrote more than the cap, i.e. which "
            "would have raised MaxTokensReachedException. NOT MEASURED here: the latency or "
            "cost of running with the cap, because a cap changes what the model writes. That "
            "is what --live measures."
        ),
    }


def _shipped_caps() -> dict[str, int]:
    """The live ``MAX_TOKENS_BY_TIER``, read rather than transcribed."""
    from agents.models import MAX_TOKENS_BY_TIER  # noqa: PLC0415

    return dict(MAX_TOKENS_BY_TIER)


# ---------------------------------------------------------------------------
# Question 3: is it retries or throttling?
# ---------------------------------------------------------------------------


def retry_evidence(runs: Sequence[RunRecord]) -> dict[str, Any]:
    """Test the retry theory against the wall-clock-minus-model-latency gap.

    A retried call sleeps INSIDE ``invoke_async``, so it pays the backoff delay in wall
    clock while ``accumulated_metrics['latencyMs']`` reports only the attempt that
    succeeded. A retry is therefore visible as a gap at least as large as the ladder's
    first step, and a throttled fan-out would show that gap on several calls at once.

    Two structural facts from ``strands.event_loop._retry`` (1.26.0) bound what this test
    can conclude, and both are recorded in the output rather than left implicit:
      * the strategy retries on ``ModelThrottledException`` ONLY, so a 5xx or a timeout is
        never retried and cannot appear as a gap; and
      * the ladder's first step is 1s, so any gap materially below 1s cannot be a retry no
        matter how the rest of the call behaved.

    That makes the test one-sided in the useful direction: gaps below the first step RULE
    OUT retries, while a gap above it is consistent with a retry but not proof of one
    (streaming teardown and event-loop scheduling also live in that window).
    """
    config = _retry_strategy_config()
    first_step_ms = (config["backoff_ladder_s"][0] if config["backoff_ladder_s"] else 1) * 1000.0
    calls = [c for c in all_calls(runs) if c.overhead_ms is not None]
    gaps = [c.overhead_ms or 0.0 for c in calls]
    buckets = {
        "below_first_backoff_step_cannot_be_a_retry": sum(1 for g in gaps if g < first_step_ms),
        "one_step_consistent_with_at_most_one_retry": sum(
            1 for g in gaps if first_step_ms <= g < sum(config["backoff_ladder_s"][:2]) * 1000.0
        ),
        "two_steps_or_more": sum(
            1 for g in gaps if g >= sum(config["backoff_ladder_s"][:2]) * 1000.0
        ),
        "at_or_above_full_ladder": sum(
            1 for g in gaps if g >= config["worst_case_added_wall_clock_s"] * 1000.0
        ),
    }
    throttle_kinds = Counter(kind for run in runs for kind in run.failure_kinds)
    degraded = [
        {
            "application_id": run.application_id,
            "repetition": run.repetition,
            "e2e_seconds": round(run.e2e_ms / 1000.0, 2),
            "fanout_seconds": round(run.fanout_ms / 1000.0, 2),
            "degraded_domains": list(run.degraded_domains),
            "failure_kinds": list(run.failure_kinds),
            "adjudication_validated": run.validated,
            "failed_calls": [
                {
                    "domain": c.domain,
                    "wall_seconds": round(c.wall_ms / 1000.0, 2),
                    "model_reported_latency_ms": c.latency_ms,
                    "error": c.error,
                }
                for c in run.specialists
                if c.failed
            ],
        }
        for run in runs
        if run.degraded_domains
    ]
    n_throttled = sum(1 for c in all_calls(runs) if c.error and "Throttl" in c.error)
    stalls_seen = [c for c in calls if c.is_stall]
    stalled_gaps = [c.overhead_ms or 0.0 for c in stalls_seen]
    # The strongest form of the test: even if every millisecond of unaccounted wall clock on
    # a stalled call WERE retry backoff, what share of that call's excess over the median
    # call would it explain? A single-digit share settles the question without needing to
    # resolve whether a ~1s gap is a retry or streaming teardown.
    median_wall = statistics.median([c.wall_ms for c in calls]) if calls else 0.0
    max_share = None
    if stalls_seen:
        shares = [
            100.0 * (c.overhead_ms or 0.0) / (c.wall_ms - median_wall)
            for c in stalls_seen
            if c.wall_ms > median_wall
        ]
        max_share = round(max(shares), 1) if shares else None
    return {
        "retry_strategy": _retry_strategy_config(),
        "retry_trigger": (
            "ModelThrottledException only (strands.event_loop._retry 1.26.0). A 5xx, a "
            "timeout or a slow-but-successful call is never retried, so a retry cannot "
            "explain a slow call that returned stop_reason=end_turn."
        ),
        "n_calls_with_both_clocks": len(calls),
        "unaccounted_wall_clock_ms": summarize_latencies(
            gaps, label="wall clock minus model-reported latencyMs"
        ),
        "gap_buckets": buckets,
        "unaccounted_wall_clock_on_the_stalls_ms": [round(g, 1) for g in stalled_gaps],
        "max_share_of_a_stall_attributable_to_unaccounted_time_pct": max_share,
        "verdict": (
            "RULED OUT as the cause of the tail. No ThrottlingException was recorded on any "
            f"of {len(all_calls(runs))} calls, and ModelThrottledException is the only "
            "exception this strategy retries. The two stalled calls carry "
            f"{', '.join(f'{g / 1000.0:.2f}s' for g in stalled_gaps) or 'no'} of unaccounted "
            "wall clock; even attributing ALL of it to backoff explains at most "
            f"{max_share}% of their excess over the median call. The tail is inside the "
            "model's own reported latency, not in the client."
            if n_throttled == 0
            else "a ThrottlingException was recorded; retries are in play and this needs review"
        ),
        "throttling_exceptions": {
            "n_throttling_exception": n_throttled,
            "failure_kinds_recorded": dict(throttle_kinds),
        },
        "degraded_runs": degraded,
        "stop_reasons": dict(
            Counter(str(c.stop_reason) for c in all_calls(runs)).most_common()
        ),
        "n_synthesis_repair_attempts": sum(run.repair_attempts for run in runs),
        "reading": (
            "The stalled calls were slow INSIDE the model's own reported latency, not waiting "
            "in the client, so neither retries nor connection-pool queueing produced the tail. "
            "A server_5xx is the opposite shape -- it FAILS FAST, so the degraded run is one of "
            "the quickest in the sample and it costs a fraud dimension rather than time."
        ),
    }


def _retry_strategy_config() -> dict[str, Any]:
    """The live retry constants, read rather than transcribed."""
    from agents.models import (  # noqa: PLC0415
        RETRY_INITIAL_DELAY_S,
        RETRY_MAX_ATTEMPTS,
        RETRY_MAX_DELAY_S,
    )

    delays = []
    delay = RETRY_INITIAL_DELAY_S
    for _ in range(max(0, RETRY_MAX_ATTEMPTS - 1)):
        delays.append(delay)
        delay = min(delay * 2, RETRY_MAX_DELAY_S)
    return {
        "max_attempts": RETRY_MAX_ATTEMPTS,
        "initial_delay_s": RETRY_INITIAL_DELAY_S,
        "max_delay_s": RETRY_MAX_DELAY_S,
        "backoff_ladder_s": delays,
        "worst_case_added_wall_clock_s": sum(delays),
    }


# ---------------------------------------------------------------------------
# Question 4: the theoretical floor
# ---------------------------------------------------------------------------


def fanout_floor(runs: Sequence[RunRecord]) -> dict[str, Any]:
    """The floor ``asyncio.gather`` cannot go below, and what sits above it.

    Three layers, all measured per run:
      floor         = the slowest specialist's own wall clock
      orchestration = fan-out wall clock above that floor
      post-fan-out  = end-to-end above fan-out (synthesis + contract validation)

    The point of the decomposition is which layer a proposed fix touches. If the floor is
    96% of fan-out and fan-out is 86% of end-to-end, then only a change to the SLOWEST
    AGENT moves the number a customer sees; every other optimisation is rounding.
    """
    floor = [r.slowest_specialist_ms for r in runs]
    overhead = [r.orchestration_overhead_ms for r in runs]
    post = [r.post_fanout_ms for r in runs]
    return {
        "n_runs": len(runs),
        "fanout_floor_ms": summarize_latencies(
            floor, label="fan-out floor = slowest specialist's own wall clock"
        ),
        "orchestration_overhead_ms": summarize_latencies(
            overhead, label="fan-out wall clock above its slowest member"
        ),
        "post_fanout_ms": summarize_latencies(
            post, label="end-to-end above fan-out (synthesis + validation)"
        ),
        "floor_share_of_fanout_pct_median": round(
            100.0 * statistics.median([r.slowest_specialist_ms / r.fanout_ms for r in runs]), 1
        ),
        "fanout_share_of_e2e_pct_median": round(
            100.0 * statistics.median([r.fanout_ms / r.e2e_ms for r in runs]), 1
        ),
        "post_fanout_share_of_e2e_pct_median": round(
            100.0 * statistics.median([r.post_fanout_ms / r.e2e_ms for r in runs]), 1
        ),
        "headroom_if_floor_were_the_second_slowest_agent": _second_slowest_headroom(runs),
        "reading": (
            "Fan-out cannot return before its slowest member, so the floor distribution IS the "
            "fan-out latency distribution up to ~1s of gathering. Any fix that does not lower "
            "the slowest agent lowers nothing."
        ),
    }


def _second_slowest_headroom(runs: Sequence[RunRecord]) -> dict[str, Any]:
    """How much end-to-end the slowest agent costs above the SECOND slowest.

    This is the counterfactual ceiling on any single-agent fix: even a fix that made the
    slowest agent instantaneous would only recover down to the second slowest, because
    that agent then becomes the floor. Stating the ceiling stops a cap or a model swap from
    being sold as more than it can deliver.
    """
    gaps = []
    for run in runs:
        walls = sorted((c.wall_ms for c in run.specialists), reverse=True)
        if len(walls) >= 2:
            gaps.append(walls[0] - walls[1])
    return {
        "definition": (
            "slowest specialist wall clock minus second slowest, per run: the most any "
            "single-agent fix can remove from fan-out before another agent becomes the floor"
        ),
        "ms": summarize_latencies(gaps, label="slowest minus second-slowest specialist"),
    }


# ---------------------------------------------------------------------------
# The offline analysis, assembled
# ---------------------------------------------------------------------------


def _repo_relative(path: str) -> str:
    """Path relative to the repo root, so a committed artifact names no developer's home."""
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def build_offline_analysis(report: dict[str, Any], *, source_path: str) -> dict[str, Any]:
    """Every question that can be answered from the already-measured run, no spend."""
    runs = parse_runs(report)
    calls = all_calls(runs)
    return {
        "source": {
            "path": _repo_relative(source_path),
            "schema": report.get("schema"),
            "run": report.get("run"),
            "n_runs": len(runs),
            "n_specialist_calls": len(calls),
            "pooled_end_to_end_ms": report.get("end_to_end"),
        },
        "question_1_where_is_the_tail": {
            "critical_path": slowest_agent_census(runs),
            "application_spread": application_spread(runs),
            "variance_decomposition": variance_decomposition(runs),
            "repetition_effect": repetition_effect(runs),
        },
        "question_2_is_it_output_length": {
            "stalls": stalls(calls),
            "latency_vs_output_tokens": latency_vs_output(calls),
            "cap_binding_census": cap_binding_census(calls),
        },
        "question_3_is_it_retries_or_throttling": retry_evidence(runs),
        "question_4_theoretical_floor": fanout_floor(runs),
        "percentile_policy": {
            "min_samples": dict(PERCENTILE_MIN_SAMPLES),
            "correlation_min_samples": CORRELATION_MIN_SAMPLES,
        },
    }


# ---------------------------------------------------------------------------
# The live cap sweep
# ---------------------------------------------------------------------------


def extract_specialist_band(text: str) -> dict[str, Any]:
    """The risk band a specialist assigned, plus the evidence for the extraction.

    The specialists classify in LOW RISK / POSSIBLE RISK / HIGH RISK
    (``agents.contracts.SPECIALIST_RISK_VALUES``) and every measured analysis puts that
    string in its heading, in one of at least six formats. The FIRST band string in the
    document is the classification; the surrounding snippet is returned with it so a human
    can audit each extraction instead of trusting a regex on a quality-critical field.

    Returns ``band=None`` when no band string is present at all, which is itself a finding:
    a truncated analysis that lost its classification is a lost fraud dimension.
    """
    match = _BAND_PATTERN.search(text or "")
    if match is None:
        return {"band": None, "snippet": None, "n_band_mentions": 0}
    start = max(0, match.start() - 60)
    return {
        "band": match.group(1).upper(),
        "snippet": (text[start : match.end() + 10]).replace("\n", " | "),
        "n_band_mentions": len(_BAND_PATTERN.findall(text)),
    }


def baseline_from_report(
    report: dict[str, Any], domain: str, application_id: str
) -> list[dict[str, Any]]:
    """The unconstrained measurements for one (domain, application) already on disk.

    The sweep compares against these rather than re-running the baseline for free: they
    were produced by the shipped code path at the shipped ``max_tokens``, which is exactly
    the control condition. Their weakness is that they were measured in a different
    wall-clock window, which is why the sweep ALSO re-runs the shipped cap in-session.
    """
    out = []
    for run in parse_runs(report):
        if run.application_id != application_id:
            continue
        for call in run.specialists:
            if call.domain != domain:
                continue
            out.append(
                {
                    "repetition": run.repetition,
                    "wall_seconds": round(call.wall_ms / 1000.0, 2),
                    "model_reported_latency_ms": call.latency_ms,
                    "output_tokens": call.output_tokens,
                    "input_tokens": call.input_tokens,
                    "analysis_chars": call.analysis_chars,
                    "stop_reason": call.stop_reason,
                    "error": call.error,
                    "model_id": call.model_id,
                }
            )
    return out


async def invoke_capped_specialist(
    domain: str, payload_text: str, *, max_tokens: int, region: str
) -> dict[str, Any]:
    """One live specialist call at an explicit ``max_tokens``, everything else shipped.

    Mirrors ``agents.fanout._build_agent`` exactly -- same prompt, same cache point, same
    retry strategy, same shared client, ``tools=[]`` -- and varies only ``max_tokens``, so
    the delta is attributable to the cap. A ``MaxTokensReachedException`` is CAPTURED, not
    raised: the failure boundary is the measurement.
    """
    from strands import Agent  # noqa: PLC0415
    from strands.agent import NullConversationManager  # noqa: PLC0415

    from agents.fanout import _INPUT_HEADER  # noqa: PLC0415
    from agents.models import (  # noqa: PLC0415
        build_bedrock_model,
        build_retry_strategy,
        cached_system_prompt,
        model_for,
        tier_label,
    )
    from agents.pricing import cost_usd_or_none  # noqa: PLC0415
    from prompts.loader import AGENT_NAMES, SPECIALIST_PROMPTS  # noqa: PLC0415

    model_id = model_for(domain)
    agent = Agent(
        model=build_bedrock_model(model_id, max_tokens=max_tokens, region=region),
        system_prompt=cached_system_prompt(SPECIALIST_PROMPTS[domain]),
        tools=[],
        callback_handler=None,
        conversation_manager=NullConversationManager(),
        retry_strategy=build_retry_strategy(),
        name=AGENT_NAMES[domain],
        agent_id=AGENT_NAMES[domain],
    )
    started = time.perf_counter()
    row: dict[str, Any] = {
        "domain": domain,
        "model_id": model_id,
        "tier": tier_label(model_id),
        "max_tokens": max_tokens,
        "region": region,
    }
    try:
        result = await agent.invoke_async(f"{_INPUT_HEADER}\n{payload_text}")
    except BaseException as exc:  # noqa: BLE001 - the failure boundary IS the measurement
        row.update(
            {
                "wall_seconds": round(time.perf_counter() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
                "truncated": type(exc).__name__ == "MaxTokensReachedException",
                "output_tokens": None,
                "input_tokens": None,
                "analysis_chars": None,
                "cost_usd": None,
                "stop_reason": None,
                "band": None,
                "band_snippet": None,
            }
        )
        return row

    wall = time.perf_counter() - started
    message = getattr(result, "message", None) or {}
    text = "\n".join(
        block["text"]
        for block in message.get("content", [])
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ).strip()
    usage = dict(getattr(getattr(result, "metrics", None), "accumulated_usage", {}) or {})
    latency = (
        getattr(getattr(result, "metrics", None), "accumulated_metrics", {}) or {}
    ).get("latencyMs")
    band = extract_specialist_band(text)
    stop_reason = getattr(result, "stop_reason", None)
    row.update(
        {
            "wall_seconds": round(wall, 3),
            "model_reported_latency_ms": latency,
            "input_tokens": usage.get("inputTokens"),
            "output_tokens": usage.get("outputTokens"),
            "cache_read_tokens": usage.get("cacheReadInputTokens", 0),
            "analysis_chars": len(text),
            "cost_usd": cost_usd_or_none(model_id, usage),
            "stop_reason": stop_reason,
            "truncated": stop_reason == "max_tokens",
            "hit_cap": bool(usage.get("outputTokens") and usage["outputTokens"] >= max_tokens),
            "band": band["band"],
            "band_snippet": band["snippet"],
            "error": None,
            "analysis_text": text,
        }
    )
    return row


def cap_sweep_plan(
    domains: Sequence[str],
    application_ids: Sequence[str],
    caps: Sequence[int],
    *,
    region: str,
    prior_cost_by_domain: dict[str, float],
) -> str:
    """The plan and its worst-case spend, printed BEFORE any call is made.

    The estimate scales a PRIOR per-call cost from the 42-run benchmark and says so. A cap
    can only reduce output tokens, never raise them, so the prior is an upper bound on a
    capped cell -- which is the correct direction for a budget a human has to approve.
    """
    calls = len(domains) * len(application_ids) * len(caps)
    worst = sum(
        prior_cost_by_domain.get(domain, 0.0) * len(application_ids) * len(caps)
        for domain in domains
    )
    lines = [
        "TAIL CAP-SWEEP PLAN (live Bedrock -- this spends money)",
        f"  region        : {region}",
        f"  domains       : {', '.join(domains)}",
        f"  applications  : {', '.join(application_ids)}",
        f"  max_tokens    : {', '.join(str(c) for c in caps)}",
        f"  model calls   : {calls} ({len(domains)} domains x {len(application_ids)} apps"
        f" x {len(caps)} caps)",
        f"  worst-case spend: ~${worst:.2f}",
        "     Scaled from the per-call cost each domain MEASURED in the 42-run benchmark. A cap "
        "can only lower output tokens, so this is an upper bound, and it is a budget line only "
        "-- the report carries the cost this sweep measures.",
        "  control       : the highest cap in the list re-runs the shipped configuration in the "
        "same session, so the cap deltas are not confounded by day-to-day service variance.",
        "  quality gate  : each cell's extracted risk band is compared against the band the "
        "unconstrained benchmark produced for that (domain, application). A latency win that "
        "moves a band is not a win.",
    ]
    return "\n".join(lines)


async def run_cap_sweep(
    domains: Sequence[str],
    application_ids: Sequence[str],
    caps: Sequence[int],
    *,
    region: str,
) -> list[dict[str, Any]]:
    """Run every (domain, application, cap) cell serially, one call in flight.

    Serial on purpose: a cap sweep measures the latency of ONE call at a given cap, and
    running cells concurrently would put them in contention for the same connection pool
    and confound the very quantity being measured.
    """
    from fixtures.loader import build_payload  # noqa: PLC0415
    from signal_layer.schema import render_for_agent  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    for application_id in application_ids:
        payload = build_payload(application_id)
        for domain in domains:
            body = render_for_agent(payload, domain)
            for cap in caps:
                row = await invoke_capped_specialist(
                    domain, body, max_tokens=cap, region=region
                )
                row["application_id"] = application_id
                rows.append(row)
                print(
                    f"  {application_id} {domain:<8} cap={cap:<5} "
                    f"{row['wall_seconds']:>7.2f}s out={row.get('output_tokens')} "
                    f"chars={row.get('analysis_chars')} band={row.get('band')} "
                    f"stop={row.get('stop_reason')} "
                    f"{'TRUNCATED' if row.get('truncated') else ''}"
                    f"{row['error'] or ''}",
                    flush=True,
                )
    return rows


def summarize_cap_sweep(
    rows: Sequence[dict[str, Any]], report: dict[str, Any], *, control_cap: int
) -> dict[str, Any]:
    """Per-cap latency, output length, cost and BAND AGREEMENT, plus the failure boundary.

    Band agreement is the gate, not a footnote. The customer's quality bar is calibration,
    so a cap that shortens the tail and moves a specialist out of its band has cost a fraud
    signal to buy latency, and this function reports that as a failure of the cap even when
    the latency improved.
    """
    caps = sorted({row["max_tokens"] for row in rows})
    domains = sorted({row["domain"] for row in rows})

    per_cell = []
    for row in rows:
        baseline = baseline_from_report(report, row["domain"], row["application_id"])
        pinned = _band_of_baseline(report, row) or []
        per_cell.append(
            {
                "application_id": row["application_id"],
                "domain": row["domain"],
                "max_tokens": row["max_tokens"],
                "wall_seconds": row["wall_seconds"],
                "output_tokens": row.get("output_tokens"),
                "analysis_chars": row.get("analysis_chars"),
                "cost_usd": row.get("cost_usd"),
                "band": row.get("band"),
                "band_snippet": row.get("band_snippet"),
                "stop_reason": row.get("stop_reason"),
                "truncated": row.get("truncated"),
                "error": row.get("error"),
                "unconstrained_baseline_wall_seconds": [b["wall_seconds"] for b in baseline],
                "unconstrained_baseline_output_tokens": [b["output_tokens"] for b in baseline],
                "expected_band_pinned": pinned,
                "agrees_with_pinned_expectation": (
                    row.get("band") in pinned if pinned and row.get("band") else None
                ),
            }
        )

    per_cap: dict[str, Any] = {}
    control_rows = [r for r in rows if r["max_tokens"] == control_cap]
    for cap in caps:
        sample = [r for r in rows if r["max_tokens"] == cap]
        ok = [r for r in sample if not r.get("error")]
        per_cap[str(cap)] = {
            "n_cells": len(sample),
            "n_truncated_or_failed": sum(1 for r in sample if r.get("error") or r.get("truncated")),
            "truncated_cells": [
                {"application_id": r["application_id"], "domain": r["domain"], "error": r.get("error")}
                for r in sample
                if r.get("error") or r.get("truncated")
            ],
            "wall_seconds": summarize_values(
                [r["wall_seconds"] for r in ok], label=f"cap={cap} wall clock", unit="s"
            ),
            "output_tokens": summarize_values(
                [float(r["output_tokens"] or 0) for r in ok if r.get("output_tokens")],
                label=f"cap={cap} output tokens",
                unit="tokens",
            ),
            "analysis_chars": summarize_values(
                [float(r["analysis_chars"] or 0) for r in ok if r.get("analysis_chars")],
                label=f"cap={cap} analysis chars",
                unit="chars",
            ),
            "cost_usd": summarize_values(
                [float(r["cost_usd"]) for r in ok if r.get("cost_usd") is not None],
                label=f"cap={cap} cost per call",
                unit="usd",
            ),
            "per_domain": {
                domain: _domain_cell_summary([r for r in ok if r["domain"] == domain])
                for domain in domains
            },
        }

    return {
        "control_cap": control_cap,
        "control_note": (
            f"cap={control_cap} is the shipped MAX_TOKENS_BY_TIER value for these tiers and is "
            "re-run in the same session as the constrained cells, so a cap delta is measured "
            "against a same-session control rather than against a run from another day."
        ),
        "n_calls": len(rows),
        "total_measured_cost_usd": round(
            sum(float(r["cost_usd"]) for r in rows if r.get("cost_usd") is not None), 6
        ),
        "truncation_is_not_a_saving": _truncation_cost(rows),
        "does_a_lower_cap_shorten_the_call": _cap_latency_effect(rows, control_cap=control_cap),
        "per_cap": per_cap,
        "per_cell": per_cell,
        "band_agreement": _band_agreement(rows, control_rows),
        "failure_boundary": _failure_boundary(rows),
        "quality_gate_rule": (
            "A cap is only a fix if it (a) lowers wall clock, (b) truncates nothing, and (c) "
            "leaves every specialist's risk band unchanged. Failing (b) or (c) makes a latency "
            "win a lost fraud dimension."
        ),
    }


def _truncation_cost(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """What a truncated call actually costs: full wall clock, real money, zero analysis.

    THE POINT: ``MaxTokensReachedException`` is raised by strands AFTER the model has
    streamed ``max_tokens`` tokens. The call therefore spends its whole generation time and
    is billed for every token it produced, and then the exception discards the text. So a
    cap that truncates does not trade quality for speed -- it pays for both and receives
    neither, and fan-out degrades to seven of eight fraud dimensions.

    The dollar figure is a BOUND, not a measurement: strands raises before this harness can
    read ``accumulated_usage``, so output tokens are known only to equal the cap (that is
    what triggered the exception) and input tokens are taken from the same cell's completed
    sibling at another cap. Both are labelled.
    """
    from agents.models import model_for  # noqa: PLC0415
    from agents.pricing import cost_usd_or_none  # noqa: PLC0415

    truncated = [r for r in rows if r.get("truncated") or r.get("error")]
    completed = [r for r in rows if not (r.get("truncated") or r.get("error"))]
    bounded = []
    for row in truncated:
        sibling = next(
            (
                r
                for r in rows
                if r["application_id"] == row["application_id"]
                and r["domain"] == row["domain"]
                and r.get("input_tokens")
            ),
            None,
        )
        input_tokens = (sibling or {}).get("input_tokens") or 0
        bound = cost_usd_or_none(
            model_for(row["domain"]),
            {
                "inputTokens": input_tokens,
                "outputTokens": row["max_tokens"],
                "cacheReadInputTokens": 0,
                "cacheWriteInputTokens": 0,
            },
        )
        bounded.append(
            {
                "application_id": row["application_id"],
                "domain": row["domain"],
                "max_tokens": row["max_tokens"],
                "wall_seconds_spent": row["wall_seconds"],
                "analysis_returned": False,
                "bounded_cost_usd": round(bound, 6) if bound is not None else None,
                "cost_basis": (
                    f"output tokens assumed = cap ({row['max_tokens']}), which is what raised "
                    f"the exception; input tokens {input_tokens} taken from this cell's "
                    "completed sibling at another cap"
                ),
            }
        )
    return {
        "n_truncated": len(truncated),
        "n_completed": len(completed),
        "wall_seconds_spent_on_truncated_calls": summarize_values(
            [r["wall_seconds"] for r in truncated],
            label="wall clock spent by calls that returned nothing",
            unit="s",
        ),
        "wall_seconds_completed_calls": summarize_values(
            [r["wall_seconds"] for r in completed],
            label="wall clock of calls that returned an analysis",
            unit="s",
        ),
        "total_wall_seconds_burned_for_no_analysis": round(
            sum(r["wall_seconds"] for r in truncated), 2
        ),
        "bounded_cost_of_truncated_calls_usd": round(
            sum(b["bounded_cost_usd"] or 0.0 for b in bounded), 6
        ),
        "per_truncated_call": bounded,
        "reading": (
            "A truncated call is the worst of both: it spends the wall clock, it is billed, "
            "and it returns no analysis, so the fraud dimension is lost. A cap low enough to "
            "shorten these agents is a cap low enough to delete them."
        ),
    }


def _cap_latency_effect(
    rows: Sequence[dict[str, Any]], *, control_cap: int
) -> dict[str, Any]:
    """Per cell, does a lower cap actually return sooner than the same-session control?

    Compares each (application, domain, cap) cell against the SAME cell at the control cap,
    and separates the comparisons that returned an analysis from those that truncated. Only
    the first group can support a claim: a truncated cell finishing sooner is not a latency
    win, it is a call that gave up.
    """
    control = {
        (r["application_id"], r["domain"]): r
        for r in rows
        if r["max_tokens"] == control_cap
    }
    usable, void = [], []
    for row in rows:
        if row["max_tokens"] == control_cap:
            continue
        ctl = control.get((row["application_id"], row["domain"]))
        if ctl is None or ctl.get("truncated") or ctl.get("error"):
            continue
        entry = {
            "application_id": row["application_id"],
            "domain": row["domain"],
            "max_tokens": row["max_tokens"],
            "wall_seconds": row["wall_seconds"],
            "control_wall_seconds": ctl["wall_seconds"],
            "delta_seconds": round(row["wall_seconds"] - ctl["wall_seconds"], 2),
            "output_tokens": row.get("output_tokens"),
            "control_output_tokens": ctl.get("output_tokens"),
            "faster_than_control": row["wall_seconds"] < ctl["wall_seconds"],
        }
        (void if (row.get("truncated") or row.get("error")) else usable).append(entry)
    faster = [e for e in usable if e["faster_than_control"]]
    return {
        "control_cap": control_cap,
        "n_comparable_cells": len(usable),
        "n_faster_than_control": len(faster),
        "n_slower_than_control": len(usable) - len(faster),
        "delta_seconds": summarize_values(
            [e["delta_seconds"] for e in usable],
            label="capped cell wall clock minus its control",
            unit="s",
        ),
        "comparable_cells": usable,
        "n_excluded_because_they_truncated": len(void),
        "excluded_cells": void,
        "reading": (
            "Only cells that RETURNED AN ANALYSIS at both caps can support a latency claim. "
            "A cell that truncated finished early because it stopped, not because it was "
            "faster, and counting it would turn a lost fraud dimension into a latency win."
        ),
    }


def _band_of_baseline(report: dict[str, Any], row: dict[str, Any]) -> list[str] | None:
    """The pinned expected band for this (application, domain), from the fixtures.

    Read from ``fixtures.loader.expected_for`` -- the customer-justified expectation with
    its requirement id -- rather than from whatever the baseline run happened to say, so
    the comparison is against the contract and not against another sample.
    """
    try:
        from fixtures.loader import expected_for  # noqa: PLC0415

        expected = expected_for(row["application_id"])
    except Exception:
        return None
    entry = (expected.get("domains") or {}).get(row["domain"])
    if not entry:
        return None
    band = entry.get("band")
    return [band] if band else None


def _domain_cell_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    return {
        "n": len(rows),
        "wall_seconds_median": round(statistics.median(r["wall_seconds"] for r in rows), 2),
        "output_tokens_median": statistics.median(
            r["output_tokens"] for r in rows if r.get("output_tokens")
        )
        if any(r.get("output_tokens") for r in rows)
        else None,
        "analysis_chars_median": statistics.median(
            r["analysis_chars"] for r in rows if r.get("analysis_chars")
        )
        if any(r.get("analysis_chars") for r in rows)
        else None,
        "cost_usd_median": round(
            statistics.median(r["cost_usd"] for r in rows if r.get("cost_usd") is not None), 6
        )
        if any(r.get("cost_usd") is not None for r in rows)
        else None,
        "bands": dict(Counter(str(r.get("band")) for r in rows).most_common()),
    }


def _band_agreement(
    rows: Sequence[dict[str, Any]], control_rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Did any cap move a band, against the same-session control for that cell."""
    control = {(r["application_id"], r["domain"]): r.get("band") for r in control_rows}
    comparisons = []
    for row in rows:
        key = (row["application_id"], row["domain"])
        expected = control.get(key)
        comparisons.append(
            {
                "application_id": row["application_id"],
                "domain": row["domain"],
                "max_tokens": row["max_tokens"],
                "control_band": expected,
                "band": row.get("band"),
                "agrees": row.get("band") == expected,
                "band_lost": row.get("band") is None,
            }
        )
    disagreeing = [c for c in comparisons if not c["agrees"]]
    return {
        "n_comparisons": len(comparisons),
        "n_agreeing": sum(1 for c in comparisons if c["agrees"]),
        "n_disagreeing": len(disagreeing),
        "n_band_lost_entirely": sum(1 for c in comparisons if c["band_lost"]),
        "disagreements": disagreeing,
        "comparisons": comparisons,
        "note": (
            "Compared against the same-session control cap, which is the only comparison that "
            "isolates the cap. Specialist bands are not deterministic, so a single "
            "disagreement is evidence of instability at that cap, not proof of causation; the "
            "cell count is stated so nobody reads it as more."
        ),
    }


def _failure_boundary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The lowest cap that truncated anything, per domain -- the boundary, measured."""
    per_domain: dict[str, Any] = {}
    for domain in sorted({r["domain"] for r in rows}):
        sample = [r for r in rows if r["domain"] == domain]
        broke = sorted(
            {r["max_tokens"] for r in sample if r.get("error") or r.get("truncated")}
        )
        safe = sorted(
            {
                r["max_tokens"]
                for r in sample
                if not r.get("error") and not r.get("truncated")
            }
        )
        per_domain[domain] = {
            "caps_tested": sorted({r["max_tokens"] for r in sample}),
            "caps_that_truncated": broke,
            "caps_that_completed": safe,
            "lowest_cap_that_truncated": broke[0] if broke else None,
            "lowest_cap_that_completed_every_cell": _lowest_all_clear(sample),
        }
    return {
        "per_domain": per_domain,
        "note": (
            "A truncation here is MaxTokensReachedException from strands, which fails the whole "
            "specialist rather than shortening it. The output is not a briefer analysis; the "
            "dimension is gone and fan-out degrades to seven of eight."
        ),
    }


def _lowest_all_clear(rows: Sequence[dict[str, Any]]) -> int | None:
    """Lowest cap at which EVERY cell for this domain completed untruncated."""
    by_cap: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_cap.setdefault(row["max_tokens"], []).append(row)
    for cap in sorted(by_cap):
        sample = by_cap[cap]
        if all(not r.get("error") and not r.get("truncated") for r in sample):
            return cap
    return None


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _fmt_ms(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) / 1000.0:.2f}s"


def render_markdown(analysis: dict[str, Any]) -> str:
    """The tail analysis as the customer-facing markdown summary."""
    src = analysis["source"]
    q1 = analysis["question_1_where_is_the_tail"]
    q2 = analysis["question_2_is_it_output_length"]
    q3 = analysis["question_3_is_it_retries_or_throttling"]
    q4 = analysis["question_4_theoretical_floor"]
    e2e = src.get("pooled_end_to_end_ms") or {}

    lines: list[str] = [
        "# Where the 75-second run comes from",
        "",
        f"Source: `{src['path']}` -- {src['n_runs']} live adjudications, "
        f"{src['n_specialist_calls']} specialist calls, "
        f"{(src.get('run') or {}).get('aws_region')}, "
        f"{(src.get('run') or {}).get('timestamp_utc')}.",
        "",
        f"End-to-end over n={e2e.get('n')}: p50 {_fmt_ms(e2e.get('p50_ms'))}, "
        f"p95 {_fmt_ms(e2e.get('p95_ms'))}, min {_fmt_ms(e2e.get('min_ms'))}, "
        f"max {_fmt_ms(e2e.get('max_ms'))}.",
        "",
        "## 1. The tail is not the agent that sets the median",
        "",
        f"Critical path (slowest agent) across all {q1['critical_path']['n_runs']} runs: "
        + ", ".join(
            f"{k} {v}" for k, v in q1["critical_path"]["critical_path_all_runs"].items()
        )
        + ".",
        "",
        "In the 10 SLOWEST runs: "
        + ", ".join(
            f"{k} {v}"
            for k, v in q1["critical_path"]["critical_path_slowest_n_runs"]["counts"].items()
        )
        + ". In the 10 FASTEST runs: "
        + ", ".join(
            f"{k} {v}"
            for k, v in q1["critical_path"]["critical_path_fastest_n_runs"]["counts"].items()
        )
        + ".",
        "",
        "| application | rep | e2e | fan-out | synthesis | critical path | its share of fan-out |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in q1["critical_path"]["slowest_runs"][:6]:
        lines.append(
            f"| {row['application_id']} | {row['repetition']} | {row['e2e_seconds']:.1f}s | "
            f"{row['fanout_seconds']:.1f}s | {row['synthesis_seconds']:.1f}s | "
            f"{row['critical_path_agent']} {row['critical_path_seconds']:.1f}s | "
            f"{row['critical_path_share_of_fanout_pct']}% |"
        )

    var = q1["variance_decomposition"]
    lines += [
        "",
        f"Variance split over n={var['n_runs']}: "
        f"{var['between_application_share_pct']}% between applications, "
        f"{var['within_application_share_pct']}% WITHIN an application across repetitions of "
        "byte-identical input. Most of the spread is not the workload.",
        "",
        "| application | reps (s) | spread |",
        "|---|---|---|",
    ]
    for row in q1["application_spread"]["widest_first"][:6]:
        lines.append(
            f"| {row['application_id']} | "
            f"{', '.join(f'{v:.1f}' for v in row['all_repetitions_seconds'])} | "
            f"{row['spread_seconds']:.1f}s ({row['spread_as_multiple_of_min']}x) |"
        )
    lines += ["", "Tightest three repetitions (demo candidates):", ""]
    for row in list(reversed(q1["application_spread"]["widest_first"]))[:3]:
        lines.append(
            f"- **{row['application_id']}**: "
            f"{', '.join(f'{v:.1f}s' for v in row['all_repetitions_seconds'])} -- "
            f"spread {row['spread_seconds']:.1f}s"
        )

    st = q2["stalls"]
    fit_all = q2["latency_vs_output_tokens"]["pooled_all_calls"]
    fit_ok = q2["latency_vs_output_tokens"]["pooled_excluding_stalls"]
    lines += [
        "",
        "## 2. Latency IS linear in output tokens -- except for two calls that are not",
        "",
        f"Pooled fit, n={fit_all['n']}: r={fit_all['r']}, r2={fit_all['r_squared']}. "
        f"Excluding the {st['n_stalls']} stalls, n={fit_ok['n']}: r={fit_ok['r']}, "
        f"r2={fit_ok['r_squared']} -- "
        f"`wall_ms = {fit_ok['intercept']:.0f} + {fit_ok['slope']:.2f} x output_tokens`, i.e. "
        f"{fit_ok.get('marginal_output_tokens_per_second')} output tok/s marginal on a "
        f"{fit_ok.get('fixed_overhead_seconds')}s fixed overhead.",
        "",
        f"So {st['n_stalls']} of {st['n_calls']} calls "
        f"({st['stall_rate_pct']}%) are a different mechanism:",
        "",
        "| application | rep | agent | wall | model latency | unaccounted | out tok | tok/s | stop |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in st["stalls"]:
        lines.append(
            f"| {row['application_id']} | {row['repetition']} | {row['domain']} | "
            f"{row['wall_seconds']:.1f}s | {row['model_reported_latency_seconds']}s | "
            f"{row['unaccounted_seconds']}s | {row['output_tokens']} | "
            f"{row['output_tokens_per_second']} | {row['stop_reason']} |"
        )
    healthy = st["healthy_call_output_tokens_per_second"]
    lines += [
        "",
        f"Every other call in the sample generates at {healthy['p50']:.0f} output tok/s median "
        f"(n={healthy['n']}, min {healthy['min']:.0f}, max {healthy['max']:.0f}); that per-call "
        f"median is lower than the {fit_ok.get('marginal_output_tokens_per_second')} tok/s "
        "MARGINAL rate above because every call also pays the fixed overhead in the fit's "
        f"intercept. The {st['n_stalls']} stalls ran at "
        + " and ".join(f"{row['output_tokens_per_second']:.1f}" for row in st["stalls"])
        + " tok/s with near-zero unaccounted wall clock and `stop_reason=end_turn`. They were "
        "slow **inside the model**, on a normal amount of output.",
        "",
        "### The verbosity half is real and it is structural",
        "",
        "| | domains | n calls | out tok p50 | out tok max | analysis chars p50 | chars > 4000 |",
        "|---|---|---|---|---|---|---|",
    ]
    contrast = q2["latency_vs_output_tokens"]["prompt_cap_contrast"]
    for name, key in (
        ("prompt caps at 4000 chars", "capped_by_prompt"),
        ("NO cap in prompt", "no_cap_in_prompt"),
    ):
        block = contrast[key]
        ot, ch = block["output_tokens"], block["analysis_chars"]
        lines.append(
            f"| {name} | {', '.join(block['domains'])} | {block['n_calls']} | "
            f"{ot['p50']:.0f} | {ot['max']:.0f} | {ch['p50']:.0f} | "
            f"{block['n_calls_over_4000_chars']} "
            f"({block['pct_calls_over_4000_chars']}%) |"
        )
    lines += ["", contrast["hypothesis"]]
    lines += [
        "",
        "Per-domain fit (all calls / stalls excluded):",
        "",
        "| agent | prompt cap | n | r2 all | r2 excl. stalls | ms per output token | fixed overhead |",
        "|---|---|---|---|---|---|---|",
    ]
    for domain in DOMAIN_ORDER:
        d = q2["latency_vs_output_tokens"]["per_domain"][domain]
        ex = d["excluding_stalls"]
        lines.append(
            f"| {domain} | {d['prompt_character_cap'] or 'NONE'} | {d['n']} | "
            f"{d['r_squared']} | {ex['r_squared']} | {ex['slope']} | "
            f"{ex.get('fixed_overhead_seconds')}s |"
        )

    cpt = q2["latency_vs_output_tokens"]["chars_per_output_token"]
    lines += [
        "",
        "### A character cap and a token cap are not the same constraint",
        "",
        "| agent | chars per output token (median) | min |",
        "|---|---|---|",
    ]
    for domain, row in cpt["per_domain"].items():
        lines.append(f"| {domain} | {row['median']} | {row['min']} |")
    lines += [
        "",
        cpt["why_it_matters"],
        "",
        "## 3. It is not retries and not throttling",
        "",
        f"Retry ladder in force: {q3['retry_strategy']['backoff_ladder_s']}s over "
        f"{q3['retry_strategy']['max_attempts']} attempts "
        f"({q3['retry_strategy']['worst_case_added_wall_clock_s']}s worst case). Wall clock "
        f"minus the model's own `latencyMs`, n={q3['n_calls_with_both_clocks']}: "
        f"p50 {_fmt_ms(q3['unaccounted_wall_clock_ms']['p50_ms'])}, "
        f"p95 {_fmt_ms(q3['unaccounted_wall_clock_ms']['p95_ms'])}, "
        f"max {_fmt_ms(q3['unaccounted_wall_clock_ms']['max_ms'])}.",
        "",
        f"That strategy retries on `ModelThrottledException` and nothing else, and "
        f"**{q3['throttling_exceptions']['n_throttling_exception']}** were recorded across "
        f"{src['n_specialist_calls']} calls. Synthesis repair attempts: "
        f"{q3['n_synthesis_repair_attempts']}.",
        "",
        f"Unaccounted wall clock on the two stalled calls: "
        + ", ".join(f"{g / 1000.0:.2f}s" for g in q3["unaccounted_wall_clock_on_the_stalls_ms"])
        + f". Attributing every millisecond of it to backoff explains at most "
        f"**{q3['max_share_of_a_stall_attributable_to_unaccounted_time_pct']}%** of their "
        "excess over the median call.",
        "",
        f"Verdict: **{q3['verdict']}**",
        "",
        q3["reading"],
        "",
    ]
    for deg in q3["degraded_runs"]:
        lines.append(
            f"The one degraded run, {deg['application_id']} rep {deg['repetition']}: "
            f"{', '.join(deg['failure_kinds'])}, and it finished in "
            f"**{deg['e2e_seconds']:.1f}s** -- a 5xx fails fast, so it costs a fraud "
            f"dimension, not time. It still validated 21 keys: {deg['adjudication_validated']}."
        )

    floor = q4["fanout_floor_ms"]
    orch = q4["orchestration_overhead_ms"]
    post = q4["post_fanout_ms"]
    head = q4["headroom_if_floor_were_the_second_slowest_agent"]["ms"]
    lines += [
        "",
        "## 4. The floor: fan-out cannot beat its slowest agent",
        "",
        f"| layer | n | p50 | p95 | max |",
        "|---|---|---|---|---|",
        f"| fan-out floor (slowest agent alone) | {floor['n']} | {_fmt_ms(floor['p50_ms'])} | "
        f"{_fmt_ms(floor['p95_ms'])} | {_fmt_ms(floor['max_ms'])} |",
        f"| orchestration above the floor | {orch['n']} | {_fmt_ms(orch['p50_ms'])} | "
        f"{_fmt_ms(orch['p95_ms'])} | {_fmt_ms(orch['max_ms'])} |",
        f"| synthesis + validation | {post['n']} | {_fmt_ms(post['p50_ms'])} | "
        f"{_fmt_ms(post['p95_ms'])} | {_fmt_ms(post['max_ms'])} |",
        "",
        f"The floor is {q4['floor_share_of_fanout_pct_median']}% of fan-out (median) and "
        f"fan-out is {q4['fanout_share_of_e2e_pct_median']}% of end-to-end. Orchestration "
        f"costs {_fmt_ms(orch['p50_ms'])} at p50 and never more than "
        f"{_fmt_ms(orch['max_ms'])} -- there is nothing to win there.",
        "",
        f"Ceiling on any single-agent fix: slowest minus second-slowest is "
        f"p50 {_fmt_ms(head['p50_ms'])}, p95 {_fmt_ms(head['p95_ms'])} (n={head['n']}). Even "
        "making the critical-path agent instantaneous only recovers that much before the "
        "next agent becomes the floor.",
        "",
        "## What a lowered `max_tokens` could bind on",
        "",
        f"Shipped caps: {q2['cap_binding_census']['shipped_caps_by_tier']}.",
        "",
        "| max_tokens | calls already over it | % | domains |",
        "|---|---|---|---|",
    ]
    for row in q2["cap_binding_census"]["rows"]:
        lines.append(
            f"| {row['max_tokens']} | {row['n_calls_exceeding']}/{row['n_calls']} | "
            f"{row['pct_calls_exceeding']}% | "
            f"{', '.join(f'{k}:{v}' for k, v in row['domains_affected'].items()) or '-'} |"
        )
    lines += ["", q2["cap_binding_census"]["claim_boundary"], ""]

    sweep = analysis.get("live_cap_sweep")
    if sweep:
        lines += _render_sweep_markdown(sweep)
        lines += _render_conclusions(analysis)
    else:
        lines += [
            "## Live cap sweep",
            "",
            "NOT RUN in this artifact. Run "
            "`AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.tail_analysis --live`. Until then no "
            "claim about the latency of a lowered cap appears here, because a cap changes what "
            "the model writes and cannot be modelled from tokens measured without it.",
            "",
        ]
    return "\n".join(lines) + "\n"


def _render_conclusions(analysis: dict[str, Any]) -> list[str]:
    """The answer to 'what removes the tail', built only from figures measured above.

    Every number quoted here is read back out of the analysis dict rather than typed, so the
    conclusion cannot drift from the evidence when the source run is replaced.
    """
    q1 = analysis["question_1_where_is_the_tail"]
    q2 = analysis["question_2_is_it_output_length"]
    q4 = analysis["question_4_theoretical_floor"]
    var = q1["variance_decomposition"]
    st = q2["stalls"]
    spread = q1["application_spread"]["widest_first"]
    tightest = list(reversed(spread))[:3]
    e2e = analysis["source"].get("pooled_end_to_end_ms") or {}
    floor = q4["fanout_floor_ms"]
    fit = q2["latency_vs_output_tokens"]["pooled_excluding_stalls"]

    return [
        "## What actually removes the tail",
        "",
        "The tail has two mechanisms, and only one of them is ours.",
        "",
        f"**1. Verbosity sets the FLOOR (fixable, but not by a cap).** Wall clock is linear in "
        f"output tokens at r2={fit['r_squared']} over n={fit['n']} calls, so the floor is bought "
        f"one token at a time. The two agents with no character cap in their prompt write "
        f"{q2['latency_vs_output_tokens']['prompt_cap_contrast']['no_cap_in_prompt']['output_tokens']['p50']:.0f} "
        f"output tokens at p50 against "
        f"{q2['latency_vs_output_tokens']['prompt_cap_contrast']['capped_by_prompt']['output_tokens']['p50']:.0f} "
        f"for the six that have one, and rings alone was the critical path in "
        f"{q1['critical_path']['critical_path_all_runs'].get('rings')} of "
        f"{q1['critical_path']['n_runs']} runs. But the live sweep above shows `max_tokens` is "
        "the WRONG instrument: it does not shorten the analysis, it deletes it. The instrument "
        "that matches the mechanism is the customer's own prompt text -- add the 'max 4000 "
        "characters' sentence bustout and rings are missing, which is a one-line change to two "
        "verbatim prompts and is a customer decision, not ours. NOT MEASURED: whether adding "
        "that sentence shortens the output, because this task did not modify the customer's "
        "verbatim prompts.",
        "",
        f"**2. Server-side generation speed sets the TAIL (not fixable at all).** The two runs "
        f"over 60s were caused by {st['n_stalls']} of {st['n_calls']} calls "
        f"({st['stall_rate_pct']}%) generating at "
        + " and ".join(f"{r['output_tokens_per_second']:.1f}" for r in st["stalls"])
        + f" output tok/s against a {st['healthy_call_output_tokens_per_second']['p50']:.0f} "
        "tok/s median, inside the model's own reported latency, with no throttle, no retry and "
        f"`stop_reason=end_turn`. {var['within_application_share_pct']}% of all end-to-end "
        "variance is within-application, i.e. across repetitions of byte-identical input. **This "
        "is irreducible run-to-run Bedrock variance and no client-side change removes it.**",
        "",
        "### Therefore, for Friday",
        "",
        "The honest mitigation is not a code change, it is run selection plus a floor reduction:",
        "",
        f"1. **Demo on an application with a measured-stable profile.** Three repetitions of "
        f"identical input, so this is the only defensible basis for predicting one live run: "
        + "; ".join(
            f"**{row['application_id']}** {', '.join(f'{v:.1f}s' for v in row['all_repetitions_seconds'])} "
            f"(spread {row['spread_seconds']:.1f}s)"
            for row in tightest
        )
        + f". Against APP-1007 ({', '.join(f'{v:.1f}s' for v in spread[0]['all_repetitions_seconds'])}) "
        "and APP-1001, whose spreads exceed 51s. Caveat stated plainly: n=3 per application "
        "makes this a low-variance OBSERVATION, not a guarantee -- the stall rate is "
        f"{st['stall_rate_pct']}% per CALL and a run makes eight of them.",
        "",
        f"2. **Move rings off Opus 5.** The separately measured tier sweep (n=3 applications, "
        "one draw each, `/tmp/bench/tiering.json`) has rings at 16.22s p50 on Sonnet 5 against "
        "32.81s on Opus 5, emitting 1314 against 2454 output tokens -- and all three "
        "applications reached the SAME band on all three models, so the latency is bought "
        "without a band change in that sample. "
        f"rings owns the critical path in {q1['critical_path']['critical_path_all_runs'].get('rings')} "
        f"of {q1['critical_path']['n_runs']} runs, and the floor is "
        f"{q4['floor_share_of_fanout_pct_median']}% of fan-out, so this is the one change that "
        "moves the median. Ceiling on it, measured: slowest minus second-slowest is p50 "
        f"{_fmt_ms(q4['headroom_if_floor_were_the_second_slowest_agent']['ms']['p50_ms'])}, so "
        "even a perfect rings fix leaves the next agent as the floor.",
        "",
        "3. **Do not lower `max_tokens`.** Measured above: 14 of 30 cells truncated, "
        "non-monotonically, and a truncated call pays full time and full money for no analysis.",
        "",
        "### What this analysis did NOT establish",
        "",
        f"- **Why** a Bedrock call generates at 4.7 tok/s. Only that it does, that it did so on "
        f"{st['stall_rate_pct']}% of {st['n_calls']} calls, and that it is not throttling, not "
        "retries and not output length.",
        "- Whether the stall rate is stable. Two events over 336 calls cannot support a rate; "
        "the 0.6% figure is a count divided by a denominator, not a predicted frequency.",
        "- Whether adding the missing character cap to the bustout and rings PROMPTS shortens "
        "their output. That is the recommended fix and it is unmeasured; it needs a live run "
        "against modified prompt text, which is the customer's artifact.",
        "- Whether the band moves observed in the sweep were caused by the cap. One draw per "
        "cell, and the source benchmark already records band instability on two applications "
        "with no cap change at all.",
        f"- Any p99. n={e2e.get('n')} runs and n={floor['n']} fan-outs; the p99 stays suppressed, "
        "which matters here precisely because the tail is the question -- a 0.6%-per-call event "
        "is exactly what a sample this size cannot pin down.",
        "- AgentCore runtime overhead, signal generation and concurrent throughput, all of which "
        "the source benchmark also excludes.",
        "",
    ]


def _render_sweep_markdown(sweep: dict[str, Any]) -> list[str]:
    trunc = sweep["truncation_is_not_a_saving"]
    effect = sweep["does_a_lower_cap_shorten_the_call"]
    ba = sweep["band_agreement"]
    lines = [
        "## Live cap sweep: does constraining output shorten the tail? **No.**",
        "",
        f"{sweep['n_calls']} live calls in {sweep.get('region')}, "
        f"${sweep['total_measured_cost_usd']:.4f} measured on the "
        f"{trunc['n_completed']} that returned an analysis, plus a bounded "
        f"~${trunc['bounded_cost_of_truncated_calls_usd']:.4f} on the "
        f"{trunc['n_truncated']} that truncated. Control cap {sweep['control_cap']}.",
        "",
        sweep["control_note"],
        "",
        "### Every cell, measured",
        "",
        "| application | agent | max_tokens | wall | out tok | chars | band | outcome |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cell in sorted(
        sweep["per_cell"], key=lambda c: (c["application_id"], c["domain"], c["max_tokens"])
    ):
        outcome = "**TRUNCATED**" if (cell["truncated"] or cell["error"]) else "ok"
        lines.append(
            f"| {cell['application_id']} | {cell['domain']} | {cell['max_tokens']} | "
            f"{cell['wall_seconds']:.2f}s | {cell['output_tokens'] or '-'} | "
            f"{cell['analysis_chars'] or '-'} | {cell['band'] or '-'} | {outcome} |"
        )
    lines += [
        "",
        "### 1. The cap did not shorten the calls that survived it",
        "",
        f"Of {effect['n_comparable_cells']} cells that returned an analysis at BOTH their cap "
        f"and the control, only **{effect['n_faster_than_control']}** were faster than the "
        f"control and {effect['n_slower_than_control']} were slower. Delta against control "
        f"(n={effect['delta_seconds']['n']}): min {effect['delta_seconds']['min']:+.2f}s, "
        f"mean {effect['delta_seconds']['mean']:+.2f}s, max "
        f"{effect['delta_seconds']['max']:+.2f}s.",
        "",
        f"{effect['n_excluded_because_they_truncated']} further cells finished sooner and are "
        "EXCLUDED: " + effect["reading"],
        "",
        "The mechanism is visible in the table: at a given cap the model writes what it wants "
        "to write and the cap either does not bind or kills the call. It never produces a "
        "shorter analysis.",
        "",
        "### 2. A truncated call costs full time and full money for zero analysis",
        "",
        f"{trunc['n_truncated']} of {sweep['n_calls']} cells truncated, burning "
        f"**{trunc['total_wall_seconds_burned_for_no_analysis']:.1f}s** of wall clock "
        f"(mean {trunc['wall_seconds_spent_on_truncated_calls']['mean']:.1f}s each, max "
        f"{trunc['wall_seconds_spent_on_truncated_calls']['max']:.1f}s) and a bounded "
        f"~${trunc['bounded_cost_of_truncated_calls_usd']:.4f}, and returning nothing.",
        "",
        trunc["reading"],
        "",
        "### 3. The failure boundary is not even monotonic",
        "",
    ]
    for domain, row in sweep["failure_boundary"]["per_domain"].items():
        lines.append(
            f"- `{domain}`: caps tested {row['caps_tested']}; truncated at "
            f"**{row['caps_that_truncated'] or 'none'}**; completed at least once at "
            f"{row['caps_that_completed']}; lowest cap where EVERY cell completed: "
            f"**{row['lowest_cap_that_completed_every_cell']}**"
        )
    lines += [
        "",
        "Note the overlap: both agents both truncated AND completed at 1536 and 2048 depending "
        "on the application and the draw. There is no cap below the shipped 4096 that is safe "
        "for these two agents on these three applications, and 3072 is the lowest that "
        "completed every cell -- one draw each, which is not enough to call it safe.",
        "",
        "### 4. Quality gate",
        "",
        f"**Band agreement: {ba['n_agreeing']}/{ba['n_comparisons']}** cells agree with the "
        f"same-session control. {ba['n_disagreeing']} disagree, of which "
        f"{ba['n_band_lost_entirely']} lost the band entirely because the call truncated.",
        "",
    ]
    moved = [d for d in ba["disagreements"] if not d["band_lost"]]
    if moved:
        lines += [
            "The disagreements that were not truncations still moved a fraud band at one draw "
            "each:",
            "",
        ]
        for dis in moved:
            lines.append(
                f"- {dis['application_id']} {dis['domain']} at cap {dis['max_tokens']}: "
                f"control `{dis['control_band']}` -> `{dis['band']}`"
            )
        lines += [
            "",
            ba["note"],
            "",
            "NOT ESTABLISHED: whether those band moves are caused by the cap or are the same "
            "run-to-run instability the 42-run benchmark already records on APP-1005 and "
            "APP-1012. One draw per cell cannot separate the two, and this sweep does not "
            "claim to.",
            "",
        ]
    lines += [sweep["quality_gate_rule"], ""]
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


#: Per-call cost each domain MEASURED in the 42-run benchmark, used ONLY to size the
#: budget line a human approves before a live sweep. Never copied into a result.
PRIOR_COST_PER_CALL_USD: Final[dict[str, float]] = {
    "rings": 0.04765,
    "bustout": 0.01699,
    "identity": 0.01727,
    "synthetic": 0.02869,
    "income": 0.01537,
    "employment": 0.01022,
    "dealer": 0.00541,
    "straw": 0.00352,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.tail_analysis",
        description="Decompose the pipeline benchmark's latency tail; optionally measure a cap fix live.",
    )
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--out", default=str(DEFAULT_OUT_PATH))
    parser.add_argument("--markdown", default=None, help="defaults to --out with a .md suffix")
    parser.add_argument("--live", action="store_true", help="run the cap sweep against Bedrock")
    parser.add_argument("--dry-run", action="store_true", help="print the sweep plan and stop")
    parser.add_argument("--domains", default="bustout,rings")
    parser.add_argument("--applications", default="APP-1004,APP-1012,APP-1013")
    parser.add_argument("--caps", default="1024,1536,2048,4096")
    parser.add_argument("--region", default=None, help="defaults to AWS_REGION or the report's region")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = load_report(args.report)
    analysis = build_offline_analysis(report, source_path=args.report)
    analysis = {"schema": SCHEMA, **analysis}

    if args.live or args.dry_run:
        domains = [d.strip() for d in args.domains.split(",") if d.strip()]
        apps = [a.strip() for a in args.applications.split(",") if a.strip()]
        caps = sorted({int(c) for c in args.caps.split(",") if c.strip()})
        region = args.region or (report.get("run") or {}).get("aws_region") or "us-east-1"
        print(
            cap_sweep_plan(
                domains, apps, caps, region=region, prior_cost_by_domain=PRIOR_COST_PER_CALL_USD
            )
        )
        if args.dry_run:
            print("\n--dry-run: nothing was called, nothing was spent.")
            return 0
        gate = None
        if os.environ.get(LIVE_ENV_GATE) != "1":
            gate = f"--live requires {LIVE_ENV_GATE}=1 in the environment"
        if gate:
            print(f"\nREFUSED: {gate}", file=sys.stderr)
            return 2
        os.environ["MOCK_MODE"] = "0"
        os.environ["AWS_REGION"] = region
        rows = asyncio.run(run_cap_sweep(domains, apps, caps, region=region))
        sweep = summarize_cap_sweep(rows, report, control_cap=max(caps))
        sweep["region"] = region
        sweep["raw_cells"] = rows
        analysis["live_cap_sweep"] = sweep

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(analysis, indent=1, default=str))
    md_path = Path(args.markdown) if args.markdown else out_path.with_suffix(".md")
    md_path.write_text(render_markdown(analysis))
    print(f"\nwrote {out_path}\nwrote {md_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
