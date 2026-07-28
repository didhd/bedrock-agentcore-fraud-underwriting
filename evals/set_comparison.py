"""
Is the GPT set as good at the fraud analysis as the Claude set?

WHY THIS FILE EXISTS. A first probe (``/tmp/bench/setcmp.py``, 30 analyses) measured that
GPT-5.6 Luna answers the rings specialist ~8x faster and ~12x cheaper than the incumbent
Opus 5. Latency and cost are deal-critical for this customer, so that result is loud - and
it is not a reason to switch anything. The product is fraud detection: a cheaper, faster
wrong answer is worth less than nothing, because the cost of a false positive is a declined
good loan and the cost of a false negative is a funded fraud. This module exists to settle
the only question the latency probe cannot: **does the GPT set reach the same fraud
judgement?**

It refuses three tempting shortcuts.

*Not by output length.* Luna writes 425 output tokens on rings where Opus writes 2,080. The
obvious reading - "GPT is 5x less thorough" - is an assumption, not a measurement, and the
customer's own master prompt contradicts it ("the amount of analysis written by specialized
agents does NOT necessarily indicate fraud severity", MSTR-018). So the length gap is
measured as *evidence per unit length*, paired on the same application, in
:func:`length_gap`: distinct signals cited, distinct ``_context`` fields validated, and
whether the band's justification is complete. Brevity that drops evidence is a finding
against GPT. Brevity that drops padding is not.

*Not by one judge.* See :data:`JUDGE_CONFLICT_NOTE`. The prior tier study
(``evals/results/tier_quality.json``) measured its Opus 5 judge preferring its own family by
**+0.31 calibration / +0.78 substance** while the deterministic band margin moved only
-0.026. A single judge drawn from either family would decide this comparison by its own
identity. So every analysis is judged **twice** - once by a Claude judge, once by a GPT
judge - and the two verdicts are combined by :func:`two_judge_agreement` under one rule:
**agreement between two oppositely-biased judges is the signal; disagreement is "not
established"**. That rule is what makes this study hard to fool, and it is also why the
study is allowed to return "no verdict" on an axis.

*Not by the judges alone.* The scores that decide the recommendation are deterministic and
the judges cannot touch them: band agreement against ``fixtures.loader.expected_for``,
false positives on the anti-false-positive fixtures (:data:`ANTI_FP_APPS` - the customer's
stated goal is reducing them, so :func:`per_set_verdict` weights this above every other
axis), false negatives, ``_context`` grounding, and ``agents.output_contract.check``. The
judges add calibration and substance, which are genuinely subjective and are reported as
subjective.

WHAT THE SAMPLE CAN AND CANNOT SETTLE. Six applications per (agent, model) cell against
:data:`MIN_APPS_FOR_A_BAND_CLAIM`. That is enough to state a band-agreement rate and to name
a reproduced calibration failure; it is not enough for a p95, and
``evals.bench.summarize_values`` refuses those rather than printing them. Every rate carries
its denominator. :func:`per_set_verdict` is mechanical on purpose - the honest answer to
several of these questions is "n is too small", and that answer must not depend on the
author's mood.

Offline (no calls, re-scores the committed analyses):

    PYTHONPATH=. python3 -m evals.set_comparison --rescore

Live (billed; prints the plan and the estimated spend first):

    AGENTCORE_BENCH_ALLOW_LIVE=1 PYTHONPATH=. python3 -m evals.set_comparison \
        --sweep rings,synthetic,bustout,identity --judge --live
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents import mantle  # noqa: E402
from agents import output_contract  # noqa: E402
from agents.pricing import cost_usd_or_none  # noqa: E402
from evals.bench import LIVE_ENV_GATE, summarize_values  # noqa: E402
from evals.tier_quality import (  # noqa: E402
    _JUDGE_INSTRUCTIONS,
    BAND_ALIASES,
    AnalysisRow,
    anonymise,
    context_grounding,
    contract_findings,
    evidence_density,
    extract_declared_band,
    judge_analysis,
    sentence_count,
)
from fixtures.loader import build_payload, expected_for  # noqa: E402
from prompts.loader import AGENT_NAMES, SPECIALIST_PROMPTS  # noqa: E402
from signal_layer.schema import render_for_agent  # noqa: E402

__all__ = [
    "ANTI_FP_APPS",
    "CLAUDE_SET",
    "GPT_SET",
    "JUDGE_CONFLICT_NOTE",
    "JUDGES",
    "KNOWN_SHARED_FAILURES",
    "MIN_APPS_FOR_A_BAND_CLAIM",
    "SCHEMA",
    "SHUFFLE_SEED",
    "SWEEP_APPS",
    "SWEEP_DOMAINS",
    "aggregate",
    "build_report",
    "declared_band",
    "evaluate_row",
    "judge_one",
    "known_failure_reproduction",
    "length_gap",
    "load_analyses",
    "per_set_verdict",
    "render_markdown",
    "run_sweep",
    "set_of",
    "shared_signal_names",
    "signals_cited",
    "spend_estimate",
    "two_judge_agreement",
]

SCHEMA: Final[str] = "pointpredictive.agentcore.set-comparison/1"
ANALYSES_SCHEMA: Final[str] = "pointpredictive.agentcore.set-comparison-analyses/1"
VERDICTS_SCHEMA: Final[str] = "pointpredictive.agentcore.set-comparison-verdicts/1"


# ---------------------------------------------------------------------------
# What is being compared
# ---------------------------------------------------------------------------

#: The Claude candidates. Opus 5 is the *incumbent* on rings and synthetic and is the
#: thing a switch would displace; Sonnet 5 is what the prior tier study moved those two
#: agents to and is therefore the Claude number GPT actually has to beat.
CLAUDE_SET: Final[dict[str, str]] = {
    "opus-5": "global.anthropic.claude-opus-5",
    "sonnet-5": "global.anthropic.claude-sonnet-5",
}

#: The GPT candidates, in descending price. Luna is the one that matters commercially;
#: Terra and Sol are here to show the tier ordering *within* the GPT family, because
#: "GPT is fine" and "the cheapest GPT is fine" are different claims and only the
#: second one is worth any money.
GPT_SET: Final[dict[str, str]] = {
    "luna": "openai.gpt-5.6-luna",
    "terra": "openai.gpt-5.6-terra",
    "sol": "openai.gpt-5.6-sol",
}

MODELS: Final[dict[str, str]] = {**CLAUDE_SET, **GPT_SET}

#: Short label -> which set. Used everywhere instead of substring-matching an id.
SET_BY_LABEL: Final[dict[str, str]] = {
    **{label: "claude" for label in CLAUDE_SET},
    **{label: "gpt" for label in GPT_SET},
}

#: The four agents swept. rings and synthetic because the prior study moved them and the
#: latency probe covered them; bustout and identity because they carry the widest
#: remaining latency spread, so they are where a switch would pay next.
SWEEP_DOMAINS: Final[tuple[str, ...]] = ("rings", "synthetic", "bustout", "identity")

#: The six applications, chosen for what they can *falsify* rather than for coverage.
#:
#: * APP-1009 and APP-1010 are anti-false-positive fixtures (:data:`ANTI_FP_APPS`) and are
#:   the customer's real quality bar: APP-1010 fires eleven alerts that all share one
#:   underlying behaviour, so any model that counts alerts escalates it.
#: * APP-1014 rings and APP-1004 bustout are the two calibration failures EVERY Claude
#:   tier shared (:data:`KNOWN_SHARED_FAILURES`); if GPT repeats them the fault is the
#:   prompt, and if it does not, that is a point for GPT that no latency table shows.
#: * APP-1004 carries genuinely corroborated fraud, so the sample is not all-benign - a
#:   model that says LOW to everything must not be able to score well here.
#: * APP-1012 and APP-1013 supply the POSSIBLE band, which is the one both families are
#:   most likely to skip past in either direction.
SWEEP_APPS: Final[tuple[str, ...]] = (
    "APP-1004",
    "APP-1009",
    "APP-1010",
    "APP-1012",
    "APP-1013",
    "APP-1014",
)

#: The fixtures built to catch a model that escalates a benign application
#: (``fixtures/_scenarios_fp.py``). Only two of the five touch the four agents swept
#: here, which is a limit of this sample and is reported as one.
ANTI_FP_APPS: Final[tuple[str, ...]] = (
    "APP-1006",
    "APP-1007",
    "APP-1008",
    "APP-1009",
    "APP-1010",
)

#: (application, domain) where every Claude tier in the prior study declared the wrong
#: band, and the judge named the same fault both times: alerts arising from ONE
#: underlying behaviour treated as independent corroboration. Since all three tiers
#: failed, this is not a model-selection defect, so whether GPT repeats it is a real
#: question about the other family rather than a re-run of a known result.
KNOWN_SHARED_FAILURES: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("APP-1014", "rings", "LOW RISK", "POSSIBLE RISK"),
    ("APP-1004", "bustout", "POSSIBLE RISK", "HIGH RISK"),
)

#: Output cap for every specialist call, identical across both families on purpose.
#: The length gap is the central question, so a per-tier cap would put this module's
#: own configuration inside the measurement.
SPECIALIST_MAX_TOKENS: Final[int] = 4096

#: Applications per cell below which a band-agreement difference is not a finding. At
#: six, one case moves a rate by 17 points; at three it moves it by 33, which is why the
#: n=3 probe could not settle this and this sweep exists.
MIN_APPS_FOR_A_BAND_CLAIM: Final[int] = 5

#: Fixed so the blind order is reproducible and no set is systematically judged first.
SHUFFLE_SEED: Final[int] = 20260728


def set_of(model: str) -> str:
    """Which set a short label or full model id belongs to."""
    if model in SET_BY_LABEL:
        return SET_BY_LABEL[model]
    if mantle.is_mantle_model(model):
        return "gpt"
    for label, full in MODELS.items():
        if model == full:
            return SET_BY_LABEL[label]
    return "claude" if "anthropic" in model else "unknown"


def label_of(model_id: str) -> str:
    """Short label for a full model id, for tables."""
    for label, full in MODELS.items():
        if model_id == full:
            return label
    return model_id


# ---------------------------------------------------------------------------
# The two judges, and the conflict that forces there to be two
# ---------------------------------------------------------------------------

#: judge id -> the family it shares an identity with, i.e. the direction it is expected
#: to be biased in.
JUDGES: Final[dict[str, str]] = {
    "global.anthropic.claude-opus-5": "claude",
    "openai.gpt-5.5": "gpt",
}

JUDGE_CONFLICT_NOTE: Final[str] = (
    "This comparison cannot be judged by one model without the judge's own identity "
    "deciding it. The prior tier study measured its Opus 5 judge scoring its own family "
    "+0.31 on calibration and +0.78 on substance while the deterministic band-agreement "
    "margin moved only -0.026 - i.e. the judge-scored margin was mostly preference. A GPT "
    "judge has the mirror problem. So both are used: global.anthropic.claude-opus-5 (which "
    "is ALSO a candidate here, the strongest possible form of the conflict, and is expected "
    "to favour Claude) and openai.gpt-5.5 (NOT a candidate - the candidates are the 5.6 "
    "variants - but same family and vendor, so expected to favour GPT). Where the two "
    "agree, the finding survives both biases and is reported as established. Where they "
    "disagree, the axis is reported as NOT ESTABLISHED and no recommendation rests on it. "
    "Neither margin is 'corrected' by subtracting a bias estimate: that would produce a "
    "number that looks adjusted and was never measured. The deterministic layers - band "
    "agreement, anti-false-positive escalation, grounding, output contract - involve no "
    "judge at all and are what the recommendation actually rests on."
)

#: The verdict schema, appended to the shared judge prompt for the GPT judge.
#:
#: The Claude judge gets its verdict through strands structured output (a tool schema);
#: mantle's Responses path has no equivalent here, so the GPT judge is asked for a bare
#: JSON object and the reply is parsed and validated by :func:`_parse_gpt_verdict`. The
#: FIELD NAMES, the enums and the score anchors are identical to the Claude judge's
#: pydantic model, because two judges scoring on differently-worded rubrics would not be
#: comparable and their agreement would mean nothing.
_GPT_VERDICT_SCHEMA: Final[str] = """

Return ONLY a single raw JSON object. No prose, no explanation, no markdown code fence.
Exactly these ten keys:

{"band_read_from_analysis": one of "LOW RISK" / "POSSIBLE RISK" / "HIGH RISK" / "UNCLEAR",
 "band_matches_expectation": true or false - whether the band you read equals the pinned band,
 "calibration_score": integer 1-5, where 1 = miscalibrated (escalates a routine application \
or clears a corroborated one); 2 = defensible band but the reasoning contradicts the prompt's \
thresholds; 3 = correct band, thin reasoning; 4 = correct band with the specific mitigating or \
corroborating evidence named; 5 = as 4 and it explicitly weighs the benign explanation the \
prompt names for this signal before concluding, and treats alerts from one behaviour as one signal,
 "false_positive_verdict": one of "Pass" / "PassWithNote" / "FalsePositive" / "FalseNegative", \
per the customer's anti-false-positive rules quoted above,
 "escalation_drift": one of "aligned" / "escalates" / "dismisses" - whether the analysis pushes \
above, sits at, or falls below the pinned expectation,
 "substance_score": integer 1-5 for how much of the length is load-bearing. 1 = mostly \
restatement, scaffolding or repetition of the same finding; 3 = mixed; 5 = every section adds a \
distinct fact or a distinct inference. Judge density, NOT length: a short analysis that carries \
its evidence scores high, and a long one that repeats itself scores low,
 "padding_observed": true or false - true if material portions restate the same finding, echo \
the prompt's own calibration text, or add sections with no new evidence,
 "cites_context_fields": true or false - whether the reasoning validates signals against the \
paired _context evidence rather than asserting the raw signal value,
 "contract_notes": string - output-contract deviations you can see (invented title, length, \
risk labels in section headers, numeric alert citations, emoji). Empty string if none,
 "justification": string under 1200 characters - point at the specific text that drove the \
scores. Terse: the scores are the finding, this is the citation for them.}"""

#: Reasoning effort for the GPT judge. The judging task is an instruction-following audit
#: against a supplied standard rather than a puzzle, but it does require weighing a
#: benign explanation against an escalation, so this sits one step above the ``low``
#: default that ``agents.mantle`` uses for the synthesizer.
GPT_JUDGE_EFFORT: Final[str] = "medium"

JUDGE_MAX_TOKENS: Final[int] = 4096

_BAND_VALUES: Final[tuple[str, ...]] = ("LOW RISK", "POSSIBLE RISK", "HIGH RISK", "UNCLEAR")
_FP_VALUES: Final[tuple[str, ...]] = ("Pass", "PassWithNote", "FalsePositive", "FalseNegative")
_DRIFT_VALUES: Final[tuple[str, ...]] = ("aligned", "escalates", "dismisses")

#: Field -> validator. A judge reply that fails any of these is an ERROR, not a verdict
#: coerced into range: silently clamping a score would invent agreement between the two
#: judges, which is the one thing this design must not do.
_VERDICT_FIELDS: Final[tuple[str, ...]] = (
    "band_read_from_analysis",
    "band_matches_expectation",
    "calibration_score",
    "false_positive_verdict",
    "escalation_drift",
    "substance_score",
    "padding_observed",
    "cites_context_fields",
    "contract_notes",
    "justification",
)


def _parse_gpt_verdict(text: str) -> dict[str, Any]:
    """Parse and VALIDATE the GPT judge's JSON reply, or raise.

    Raising rather than repairing is deliberate. This module's central claim is
    "the two judges agreed", so a malformed or out-of-range verdict must become a
    recorded judge error - visible in the run's failure count - rather than a value
    nudged into the enum until it happens to match the other judge.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("GPT judge returned empty text")
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in GPT judge reply: {stripped[:160]!r}")
    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("GPT judge reply is not a JSON object")
    missing = [f for f in _VERDICT_FIELDS if f not in parsed]
    if missing:
        raise ValueError(f"GPT judge verdict is missing {missing}")
    band = str(parsed["band_read_from_analysis"]).upper()
    if band not in _BAND_VALUES:
        raise ValueError(f"band_read_from_analysis {band!r} is not one of {_BAND_VALUES}")
    if parsed["false_positive_verdict"] not in _FP_VALUES:
        raise ValueError(f"false_positive_verdict {parsed['false_positive_verdict']!r} invalid")
    if parsed["escalation_drift"] not in _DRIFT_VALUES:
        raise ValueError(f"escalation_drift {parsed['escalation_drift']!r} invalid")
    scores: dict[str, int] = {}
    for field in ("calibration_score", "substance_score"):
        value = parsed[field]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise ValueError(f"{field} {value!r} is not an integer in 1..5")
        scores[field] = value
    flags: dict[str, bool] = {}
    for field in ("band_matches_expectation", "padding_observed", "cites_context_fields"):
        value = parsed[field]
        if not isinstance(value, bool):
            raise ValueError(f"{field} {value!r} is not a boolean")
        flags[field] = value
    return {
        "band_read_from_analysis": band,
        "false_positive_verdict": parsed["false_positive_verdict"],
        "escalation_drift": parsed["escalation_drift"],
        "contract_notes": str(parsed["contract_notes"])[:2000],
        "justification": str(parsed["justification"])[:2000],
        **scores,
        **flags,
    }


def judge_prompt(row: AnalysisRow) -> str:
    """The blind judging prompt for one row: the agent's own prompt + the pinned band.

    Shared verbatim with the Claude judge (``evals.tier_quality._JUDGE_INSTRUCTIONS``) so
    the two judges are answering the same question about the same anonymised text.
    """
    expectation = expected_for(row.app)["domains"][row.domain]
    return _JUDGE_INSTRUCTIONS.format(
        domain=row.domain,
        prompt=SPECIALIST_PROMPTS[row.domain],
        app=row.app,
        expected_band=expectation["band"],
        requirement_id=expectation["requirement_id"],
        customer_rule=expectation["customer_rule"],
        analysis=anonymise(row.text),
    )


async def judge_gpt(row: AnalysisRow, *, model_id: str) -> dict[str, Any]:
    """One blind judgement by a GPT judge over bedrock-mantle."""
    prompt = judge_prompt(row) + _GPT_VERDICT_SCHEMA
    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(
            mantle.complete,
            model_id,
            prompt,
            max_output_tokens=JUDGE_MAX_TOKENS,
            effort=GPT_JUDGE_EFFORT,
        )
    except Exception as exc:
        return {
            "error": f"{type(exc).__name__}: {exc}"[:400],
            "judge_model": model_id,
            "judge_wall_s": round(time.perf_counter() - started, 3),
        }
    try:
        verdict = _parse_gpt_verdict(result["text"])
    except Exception as exc:
        return {
            "error": f"unparsable verdict: {type(exc).__name__}: {exc}"[:400],
            "judge_model": model_id,
            "judge_wall_s": round(time.perf_counter() - started, 3),
            "judge_usage": result.get("usage"),
            "judge_cost_usd": result.get("cost_usd"),
        }
    return {
        "verdict": verdict,
        "judge_model": model_id,
        "judge_wall_s": round(time.perf_counter() - started, 3),
        "judge_usage": result.get("usage"),
        "judge_cost_usd": result.get("cost_usd"),
    }


async def judge_one(row: AnalysisRow, *, model_id: str) -> dict[str, Any]:
    """Dispatch one blind judgement to whichever family the judge belongs to.

    The Claude path is ``evals.tier_quality.judge_analysis`` unchanged - reused rather
    than reimplemented so the 48 verdicts already paid for in
    ``evals/results/tier_quality_verdicts.json`` remain comparable with the ones this
    module adds.
    """
    if mantle.is_mantle_model(model_id):
        return await judge_gpt(row, model_id=model_id)
    return await judge_analysis(row, model_id=model_id)


def blind_order(rows: Sequence[AnalysisRow], seed: int = SHUFFLE_SEED) -> list[AnalysisRow]:
    """Rows in a fixed shuffled order, so neither set is systematically judged first."""
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    return shuffled


async def judge_all(
    rows: Sequence[AnalysisRow],
    *,
    judges: Sequence[str],
    progress: Any = None,
    existing: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Judge every row with every judge, blind. Keyed ``judge|domain|model|app``.

    A row that already has a SUCCESSFUL verdict from that judge is not re-judged, so a
    partial failure costs only the failures to recover and the successful verdicts stay
    bit-identical instead of becoming a second sample of a non-deterministic judge.
    """
    verdicts: dict[str, dict[str, Any]] = dict(existing or {})
    ordered = blind_order(rows)
    total = len(ordered) * len(judges)
    index = 0
    for judge in judges:
        for row in ordered:
            index += 1
            key = f"{judge}|{row.key}"
            cached = verdicts.get(key)
            if cached and "error" not in cached:
                if progress:
                    progress(f"[{index}/{total}] {key:<86} cached")
                continue
            outcome = await judge_one(row, model_id=judge)
            verdicts[key] = outcome
            if progress:
                summary = (
                    f"FAIL {outcome['error'][:70]}"
                    if "error" in outcome
                    else f"band={outcome['verdict']['band_read_from_analysis']:<14}"
                    f"cal={outcome['verdict']['calibration_score']} "
                    f"sub={outcome['verdict']['substance_score']} "
                    f"fp={outcome['verdict']['false_positive_verdict']}"
                )
                progress(f"[{index}/{total}] {key:<86} {summary}")
    return verdicts


# ---------------------------------------------------------------------------
# The live sweep: one payload, both families
# ---------------------------------------------------------------------------


def _live_gate_error(live: bool) -> str | None:
    if not live:
        return "live mode not requested (pass --live)"
    if os.environ.get(LIVE_ENV_GATE) != "1":
        return f"--live also requires {LIVE_ENV_GATE}=1 in the environment"
    return None


async def _one_claude(domain: str, model_id: str, app: str, body: str) -> AnalysisRow:
    from strands import Agent
    from strands.agent import NullConversationManager

    from agents.models import build_bedrock_model, build_retry_strategy, cached_system_prompt

    agent = Agent(
        model=build_bedrock_model(model_id, max_tokens=SPECIALIST_MAX_TOKENS),
        system_prompt=cached_system_prompt(SPECIALIST_PROMPTS[domain]),
        tools=[],
        callback_handler=None,
        conversation_manager=NullConversationManager(),
        retry_strategy=build_retry_strategy(),
        name=AGENT_NAMES[domain],
        agent_id=AGENT_NAMES[domain],
    )
    started = time.perf_counter()
    result = await agent.invoke_async(
        f"Analyze this application for {domain} fraud risk.\n\n{body}"
    )
    wall = time.perf_counter() - started
    raw = dict(result.metrics.accumulated_usage)
    usage = {
        "inputTokens": int(raw.get("inputTokens", 0)),
        "outputTokens": int(raw.get("outputTokens", 0)),
        "cacheReadInputTokens": int(raw.get("cacheReadInputTokens", 0)),
        "cacheWriteInputTokens": int(raw.get("cacheWriteInputTokens", 0)),
    }
    return AnalysisRow(
        app=app,
        domain=domain,
        model=model_id,
        text=str(result),
        wall_s=round(wall, 3),
        usage=usage,
        cost_usd=cost_usd_or_none(model_id, usage),
    )


async def _one_gpt(domain: str, model_id: str, app: str, body: str) -> AnalysisRow:
    """One GPT specialist call.

    ``agents/fanout.py`` is Converse-only and cannot route here, and GPT-5.x is not on
    Converse at all, so the call goes through ``agents.mantle``. The prompt is the
    verbatim specialist prompt CONCATENATED with the user turn rather than sent as a
    system message, which is how ``agents.synthesize`` already drives mantle - keeping
    one shape means a difference in the analysis is a difference in the model.
    """
    prompt = (
        f"{SPECIALIST_PROMPTS[domain]}\n\n"
        f"Analyze this application for {domain} fraud risk.\n\n{body}"
    )
    started = time.perf_counter()
    result = await asyncio.to_thread(
        mantle.complete, model_id, prompt, max_output_tokens=SPECIALIST_MAX_TOKENS
    )
    return AnalysisRow(
        app=app,
        domain=domain,
        model=model_id,
        text=result["text"],
        wall_s=round(time.perf_counter() - started, 3),
        usage=result.get("usage"),
        cost_usd=result.get("cost_usd"),
    )


async def run_sweep(
    plan: Sequence[tuple[str, str, str]], *, progress: Any = None
) -> list[AnalysisRow]:
    """Run the planned (domain, model, app) cells serially.

    Serial because wall clock has to stay attributable to the model: concurrency would
    put botocore queueing and mantle-side admission into the number. The fan-out
    benchmark is where concurrency is measured.
    """
    rows: list[AnalysisRow] = []
    for domain, model_id, app in plan:
        body = render_for_agent(build_payload(app), domain)
        started = time.perf_counter()
        try:
            if mantle.is_mantle_model(model_id):
                row = await _one_gpt(domain, model_id, app, body)
            else:
                row = await _one_claude(domain, model_id, app, body)
        except Exception as exc:  # one failed cell must not lose the sweep
            row = AnalysisRow(
                app=app,
                domain=domain,
                model=model_id,
                text="",
                wall_s=round(time.perf_counter() - started, 3),
                error=f"{type(exc).__name__}: {exc}"[:400],
            )
        rows.append(row)
        if progress:
            progress(
                f"{row.app} {row.domain:<10}{set_of(row.model):<7}{label_of(row.model):<9}"
                + (
                    f"FAIL {row.error[:70]}"
                    if row.error
                    else f"{row.wall_s:>6.2f}s out={(row.usage or {}).get('outputTokens'):<5} "
                    f"chars={len(row.text):<6}${(row.cost_usd or 0):.5f}"
                )
            )
    return rows


def load_analyses(path: str | Path) -> list[AnalysisRow]:
    """Load analyses already paid for by an earlier sweep.

    Reads both shapes present in this repo: the bare list the ad-hoc probe wrote
    (``/tmp/bench/setcmp.json``, short labels like ``luna``) and the schema'd envelopes
    in ``evals/results/`` (full model ids). Short labels are expanded through
    :data:`MODELS`, so a GPT label maps to its mantle id rather than being guessed at by
    substring against the Claude candidate list.
    """
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = loaded["analyses"] if isinstance(loaded, dict) else loaded
    rows: list[AnalysisRow] = []
    for entry in raw:
        if not entry.get("text"):
            continue
        model = MODELS.get(entry["model"], entry["model"])
        usage = None
        if entry.get("in_tok") is not None:
            usage = {
                "inputTokens": int(entry["in_tok"]),
                "outputTokens": int(entry.get("out_tok") or 0),
                "cacheReadInputTokens": int(entry.get("cache_r") or 0),
                "cacheWriteInputTokens": int(entry.get("cache_w") or 0),
            }
        rows.append(
            AnalysisRow(
                app=entry["app"],
                domain=entry["domain"],
                model=model,
                text=entry["text"],
                wall_s=entry.get("wall_s"),
                usage=usage,
                cost_usd=entry.get("cost"),
                source=f"reused:{Path(path).name}",
            )
        )
    return rows


#: Judge call size for the pre-run estimate. Claude: MEASURED in the prior tier study
#: (6,655 in / 1,120 out). GPT: MEASURED on a live smoke judge of the APP-1004 rings
#: Opus analysis by openai.gpt-5.5 on 2026-07-28 (2,587 in / 811 out). The two differ
#: because the Claude path also carries the structured-output tool schema, which the
#: mantle JSON path does not.
JUDGE_TOKENS_ESTIMATE: Final[dict[str, tuple[int, int]]] = {
    "global.anthropic.claude-opus-5": (6655, 1120),
    "openai.gpt-5.5": (2587, 811),
}

#: Mean (input, output) tokens per specialist call, MEASURED. rings/synthetic come from
#: this workload's own probe (``/tmp/bench/setcmp.json``); bustout/identity from the 14x3
#: pipeline benchmark. Priced per model to size a PLAN only: a swap changes output
#: length, so this is a spend ceiling and never a claim about a model's verbosity.
MEASURED_MEAN_TOKENS: Final[dict[str, tuple[int, int]]] = {
    "rings": (1750, 1600),
    "synthetic": (2730, 900),
    "bustout": (1786, 1600),
    "identity": (3110, 1200),
}


def _judge_cost_estimate(judge: str) -> float:
    tokens = JUDGE_TOKENS_ESTIMATE.get(judge)
    if tokens is None:
        return 0.0
    usage = {
        "inputTokens": tokens[0],
        "outputTokens": tokens[1],
        "cacheReadInputTokens": 0,
        "cacheWriteInputTokens": 0,
    }
    if mantle.is_mantle_model(judge):
        return mantle.cost_usd(judge, usage) or 0.0
    return cost_usd_or_none(judge, usage) or 0.0


def spend_estimate(
    plan: Sequence[tuple[str, str, str]], judge_calls: dict[str, int]
) -> dict[str, Any]:
    """Priced call plan, built only from token counts that were actually measured.

    A domain with no measured token counts is priced ``None`` and counted in
    ``specialist_calls_unpriced`` rather than guessed.
    """
    total = 0.0
    unpriced = 0
    per_call: list[dict[str, Any]] = []
    for domain, model_id, app in plan:
        tokens = MEASURED_MEAN_TOKENS.get(domain)
        if tokens is None:
            per_call.append({"domain": domain, "model": model_id, "app": app, "usd": None})
            unpriced += 1
            continue
        usage = {
            "inputTokens": tokens[0],
            "outputTokens": tokens[1],
            "cacheReadInputTokens": 0,
            "cacheWriteInputTokens": 0,
        }
        usd = (
            mantle.cost_usd(model_id, usage)
            if mantle.is_mantle_model(model_id)
            else cost_usd_or_none(model_id, usage)
        )
        per_call.append({"domain": domain, "model": model_id, "app": app, "usd": usd})
        if usd is None:
            unpriced += 1
        else:
            total += usd
    judge_total = 0.0
    per_judge: dict[str, Any] = {}
    for judge, count in judge_calls.items():
        each = _judge_cost_estimate(judge)
        per_judge[judge] = {"calls": count, "usd_each": round(each, 5), "usd": round(each * count, 4)}
        judge_total += each * count
    return {
        "specialist_calls": len(plan),
        "specialist_usd_estimate": round(total, 4),
        "specialist_calls_unpriced": unpriced,
        "judges": per_judge,
        "judge_usd_estimate": round(judge_total, 4),
        "total_usd_estimate": round(total + judge_total, 4),
        "basis": (
            "specialist tokens are MEASURED per-domain means (rings/synthetic from this "
            "workload's own probe, bustout/identity from the 14x3 pipeline benchmark), "
            "priced at each candidate's published rate including the 1.25x cache-WRITE "
            "term that GPT-5.6 implicit caching makes dominant. A swap changes output "
            "length, so this is a planning ceiling, NOT a prediction. Every reported cost "
            "is the call's own measured usage."
        ),
        "per_call": per_call,
    }


# ---------------------------------------------------------------------------
# Deterministic scoring: the layer no judge can influence
# ---------------------------------------------------------------------------

_BAND_RANK: Final[dict[str, int]] = {"LOW RISK": 0, "POSSIBLE RISK": 1, "HIGH RISK": 2}

#: A band standing ALONE on the analysis's opening line: "HIGH RISK", "**LOW RISK**",
#: "# POSSIBLE RISK", "LOW RISK." - and nothing else on the line.
_LEAD_BAND = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?(?:\*\*|__|\*)?\s*"
    r"(LOW|POSSIBLE|MEDIUM|MODERATE|HIGH)\s+RISK\b\s*(?:\*\*|__|\*)?\s*(?P<tail>.*)$"
)

#: What may follow the band on that line and still leave it a BARE declaration: nothing,
#: or closing punctuation and markdown only. Matching the whole remainder (``$``) is
#: load-bearing - an earlier version checked only the first character, which accepted
#: "LOW RISK, however, HIGH RISK applies here" as a bare LOW declaration because the tail
#: merely STARTED with a comma. A line with a trailing sentence is prose, and prose is the
#: ``bold-lead`` case or nothing.
_LEAD_TAIL_OK = re.compile(r"^[.:;,)\]\-—–!?*_\s]*$")

#: A band opening the first body line as its own statement, then prose: "**LOW RISK.** The
#: only supported indicator is ...", "LOW RISK. The address-reuse signal is explained by
#: ...", "Low risk — 96 identities across 240 units.". The band is not alone on the line so
#: ``_LEAD_BAND`` rejects it, and it is not a heading so the primary extractor's heading
#: branch misses it, but a line that OPENS with the band and a terminator is a declaration
#: by any reading.
#:
#: The terminator is what keeps this from matching prose. "Low risk indicators are present
#: but insufficient" and "This application clears as low risk for identity fraud" have the
#: band token without one and are correctly not read; so is "LOW RISK outcomes remain the
#: most common band", which is the prompt's own calibration text quoted back.
_SENTENCE_LEAD_BAND = re.compile(
    r"^\s{0,3}(?:\*\*|__)?\s*(LOW|POSSIBLE|MEDIUM|MODERATE|HIGH)\s+RISK\b\s*"
    r"(?:\*\*|__)?\s*[.:!—-]",
    re.IGNORECASE,
)

#: A title line that may precede the declaration: a markdown heading, or a whole line of
#: bold text ("``**Identity Fraud Risk Analysis**``"). At most one is skipped.
_TITLE_LINE = re.compile(r"^\s{0,3}(?:#{1,6}\s+\S|\*\*[^*]+\*\*\s*$|__[^_]+__\s*$)")


def declared_band(text: str) -> tuple[str | None, str]:
    """The band the analysis declares, with a fallback the primary extractor lacks.

    WHY THIS WRAPPER EXISTS - it is a correction to a measurement artifact that was
    silently penalising the set under test. ``evals.tier_quality.extract_declared_band``
    finds a band after a classification word ("Risk Classification: HIGH RISK"), after an
    arrow, or inside a heading. GPT frequently opens with the band ALONE on the first
    line and no label at all::

        HIGH RISK

        The precomputed ring signals are strongly corroborated by ...

    That is an unambiguous declaration by any reader, and the primary extractor returned
    None for it. Because it is a GPT stylistic habit, the misses were not random: 6 of the
    50 committed analyses were unreadable and ALL SIX were GPT, which dropped whole cells
    out of the band-agreement denominator (rings/terra fell to n=0) and would have read as
    "GPT fails to state a classification". Scoring the comparison on that would have been
    an artifact of the extractor, not a finding about the model.

    So the fallback is added HERE rather than by editing the shared extractor, and it is
    deliberately narrow. Two shapes are accepted, and ONLY at the start of the analysis
    (optionally after a single title line):

    * ``lead-line`` - the band alone on the line, nothing after it but punctuation.
    * ``sentence-lead`` - the band opening the line as its own statement, closed by a
      terminator, then prose ("``LOW RISK. The address-reuse signal is explained by ...``").

    VALIDATED BOTH WAYS over the 168 committed analyses in ``evals/results/`` (this study's
    120 plus the prior tier study's 48): together the two shapes recover 25 rows the primary
    extractor could not read, and on every analysis where the primary extractor DOES read a
    band they agree with it - **0 contradictions**. The primary extractor always wins when
    it finds anything.

    None remains a real outcome and MUST: four distinct analyses declare no locatable band
    at all. APP-1004 identity/sonnet-5 says only "support a high-risk finding" in lower-case
    prose; APP-1010 and APP-1013 identity/sonnet-5 open straight into findings; APP-1014
    identity/terra opens "Conclusion: Possible identity fraud requiring verification", which
    names no band from the customer's vocabulary. Those are genuine misses of "reasoning,
    classification, and conclusion must logically align", they are counted per cell as
    ``n_unreadable_band`` rather than dropped silently, and repairing them into an agreement
    would be inventing data. Note the residue does not favour the set under test: three of
    the four are Claude.
    """
    band, source = extract_declared_band(text)
    if band is not None:
        return band, source
    seen_title = False
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _LEAD_BAND.match(line)
        if match and _LEAD_TAIL_OK.match(match.group("tail").strip()):
            return BAND_ALIASES[f"{match.group(1).upper()} RISK"], "lead-line"
        sentence = _SENTENCE_LEAD_BAND.match(line)
        if sentence:
            return BAND_ALIASES[f"{sentence.group(1).upper()} RISK"], "sentence-lead"
        # A single title line may sit above the declaration. Anything else means the
        # analysis began its prose without declaring a band, and a band appearing further
        # down would be a section heading or a quoted rule, not the verdict.
        if not seen_title and _TITLE_LINE.match(line):
            seen_title = True
            continue
        break
    return None, "none"


def shared_signal_names(domain: str, app: str) -> tuple[str, ...]:
    """The signal names the payload for ``(app, domain)`` actually carries."""
    return tuple(build_payload(app)["views"][domain]["signals"])


def signals_cited(app: str, domain: str, text: str) -> dict[str, Any]:
    """How many of THIS view's signals the analysis names, by name or in prose.

    Deliberately narrower than ``evidence_density``, which counts any snake_case token
    and so rewards a model that happens to write in identifiers. Here the denominator is
    the payload's own signal list, and a signal counts as cited if its identifier appears
    or if every word of its de-underscored name appears - so "six distinct SSNs are linked
    to the address" scores ``address_reuse_distinct_ssn_count`` for a model that writes
    prose, and a model that writes identifiers gets no bonus for style.

    This is the measure the length question turns on: if GPT's shorter analysis cites
    fewer signals it is dropping evidence, and if it cites the same number it is dropping
    words.
    """
    names = shared_signal_names(domain, app)
    lowered = text.lower()
    cited: list[str] = []
    for name in names:
        if name in lowered:
            cited.append(name)
            continue
        words = [w for w in name.split("_") if len(w) > 3 and w not in ("count", "distinct")]
        if words and all(w in lowered for w in words):
            cited.append(name)
    return {
        "signals_in_view": len(names),
        "signals_cited": len(cited),
        "cited_fraction": round(len(cited) / len(names), 3) if names else None,
        "cited": cited,
    }


def band_justified(text: str, band: str | None) -> bool | None:
    """Whether the declared band comes with a stated reason, not just a label.

    The customer's complaint about a bare classification is that it cannot be actioned by
    an underwriter. So this looks for a causal connective in the same text as the
    declaration - "because", "driven by", "corroborat", "explains", "consistent with",
    "warrants", "insufficient" - which is a weak test on purpose: it detects a naked
    label, not the quality of the reason, and the judges score the quality.
    """
    if band is None:
        return None
    markers = (
        "because",
        "driven by",
        "corroborat",
        "explain",
        "consistent with",
        "warrant",
        "insufficient",
        "given ",
        "supported by",
        "attributable",
        "no evidence",
        "single",
        "one underlying",
    )
    lowered = text.lower()
    return any(m in lowered for m in markers)


def evaluate_row(row: AnalysisRow, verdicts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Everything measured about one analysis: deterministic layers + both judges.

    ``verdicts`` is keyed ``judge|domain|model|app``; each judge's verdict is kept
    SEPARATE and never averaged into one score, because the whole design rests on being
    able to see the two judges disagree.
    """
    expectation = expected_for(row.app)["domains"][row.domain]
    expected_band = expectation["band"]
    band, band_source = declared_band(row.text) if row.text else (None, "no-text")
    contract = output_contract.check(row.domain, row.text) if row.text else None
    findings = contract_findings(row.domain, row.text, band) if row.text else []
    declared_rank = _BAND_RANK.get(band or "")
    expected_rank = _BAND_RANK[expected_band]
    judged: dict[str, Any] = {}
    for judge in JUDGES:
        entry = verdicts.get(f"{judge}|{row.key}") or {}
        judged[judge] = {
            "verdict": entry.get("verdict"),
            "error": entry.get("error"),
            "cost_usd": entry.get("judge_cost_usd"),
        }
    return {
        "app": row.app,
        "domain": row.domain,
        "model": row.model,
        "model_label": label_of(row.model),
        "set": set_of(row.model),
        "source": row.source,
        "error": row.error,
        "expected_band": expected_band,
        "requirement_id": expectation["requirement_id"],
        "customer_rule": expectation["customer_rule"],
        "declared_band": band,
        "band_source": band_source,
        "band_agrees": None if band is None else band == expected_band,
        # Direction matters more than agreement to this customer: an escalation on a
        # benign application is the false positive she is paying to reduce, and a
        # dismissal of a corroborated one is the fraud she funds.
        "escalates": None if declared_rank is None else declared_rank > expected_rank,
        "dismisses": None if declared_rank is None else declared_rank < expected_rank,
        "is_anti_fp_fixture": row.app in ANTI_FP_APPS,
        "band_justified": band_justified(row.text, band),
        "contract_violations": (
            [{"rule": v.rule, "detail": v.detail} for v in contract.violations] if contract else []
        ),
        "contract_violation_count": len(contract.violations) if contract else 0,
        "contract_not_applicable": list(contract.not_applicable) if contract else [],
        "tier_quality_contract_findings": [
            {"rule": f.rule, "detail": f.detail} for f in findings
        ],
        "grounding": context_grounding(row.app, row.domain, row.text) if row.text else None,
        "signals": signals_cited(row.app, row.domain, row.text) if row.text else None,
        "density": evidence_density(row.text) if row.text else None,
        "sentences": sentence_count(row.text) if row.text else None,
        "wall_s": row.wall_s,
        "usage": row.usage,
        "cost_usd": row.cost_usd,
        "judges": judged,
    }


def _median_or_none(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.median(present), 4) if present else None


def _mean_or_none(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.fmean(present), 2) if present else None


def _judge_scores(rows: Sequence[dict[str, Any]], judge: str) -> list[dict[str, Any]]:
    return [r["judges"][judge]["verdict"] for r in rows if (r["judges"].get(judge) or {}).get("verdict")]


def aggregate(evaluated: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per (agent, model): the deterministic scores, then each judge separately.

    THREE DIFFERENT DENOMINATORS, deliberately not one. An analysis that never declares a
    locatable band still has a real wall clock, a real cost and a real output contract, so:

    * ``answered`` (every non-error row) is what latency, cost, length, grounding and
      contract violations are measured over. Scoring those over the band-readable subset
      only would silently discard measurements that were genuinely taken - a model whose
      prose is hard to parse would look FASTER because its slow rows were dropped.
    * ``with_band`` (band readable) is what band agreement, escalation and dismissal are
      measured over, because those are undefined without a band. ``n_unreadable_band``
      publishes the gap so a reader can see how much of the cell that subset covers.
    * ``anti_fp`` is the anti-false-positive fixtures inside ``with_band``.

    Percentiles go through ``evals.bench.summarize_values``, which refuses a p50 below 5
    samples and a p95 below 20 and records the refusal. At six applications per cell the
    medians stand and the p95s are refused, which is the truthful shape of this sample.
    """
    cells: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in evaluated:
        cells.setdefault((row["domain"], row["model_label"]), []).append(row)

    out: list[dict[str, Any]] = []
    for (domain, label), rows in sorted(cells.items()):
        answered = [r for r in rows if not r["error"]]
        usable = answered
        with_band = [r for r in answered if r["declared_band"] is not None]
        anti_fp = [r for r in with_band if r["is_anti_fp_fixture"]]
        cell: dict[str, Any] = {
            "domain": domain,
            "model_label": label,
            "model": MODELS.get(label, label),
            "set": set_of(label),
            "n_calls": len(rows),
            # Rows that produced an analysis at all. This is the denominator for latency,
            # cost, length, grounding and contract.
            "n_usable": len(answered),
            "n_failed": sum(1 for r in rows if r["error"]),
            # Rows that answered but declared no locatable band. Reported, never hidden:
            # it is both a real quality defect and the size of the band subset's shortfall.
            "n_unreadable_band": len(answered) - len(with_band),
            "unreadable_band_apps": [r["app"] for r in answered if r["declared_band"] is None],
            "band": {
                "n": len(with_band),
                "agreements": sum(1 for r in with_band if r["band_agrees"]),
                "agreement_rate": (
                    round(sum(1 for r in with_band if r["band_agrees"]) / len(with_band), 3)
                    if with_band
                    else None
                ),
                "escalations": sum(1 for r in with_band if r["escalates"]),
                "dismissals": sum(1 for r in with_band if r["dismisses"]),
                "disagreements": [
                    {
                        "app": r["app"],
                        "declared": r["declared_band"],
                        "expected": r["expected_band"],
                        "direction": "escalates" if r["escalates"] else "dismisses",
                        "requirement_id": r["requirement_id"],
                    }
                    for r in with_band
                    if not r["band_agrees"]
                ],
            },
            # The customer's stated goal. Deterministic: an escalation above the pinned
            # band on a fixture built to be benign IS the false positive, whatever a
            # judge thinks of the prose.
            "anti_false_positive": {
                "n_fixtures": len(anti_fp),
                "false_positives": sum(1 for r in anti_fp if r["escalates"]),
                "apps_escalated": [r["app"] for r in anti_fp if r["escalates"]],
            },
            "false_negatives": {
                "n_at_risk": sum(1 for r in with_band if r["expected_band"] != "LOW RISK"),
                "count": sum(1 for r in with_band if r["dismisses"]),
                "apps": [r["app"] for r in with_band if r["dismisses"]],
            },
            "contract": {
                "total_violations": sum(r["contract_violation_count"] for r in usable),
                "rows_clean": sum(1 for r in usable if r["contract_violation_count"] == 0),
                "by_rule": _violations_by_rule(usable),
            },
            "measured": {
                "wall_s_median": _median_or_none(r["wall_s"] for r in usable),
                "wall_s": summarize_values(
                    [r["wall_s"] for r in usable if r["wall_s"] is not None],
                    label=f"{domain}/{label} wall clock",
                    unit="s",
                ),
                "cost_usd_median": _median_or_none(r["cost_usd"] for r in usable),
                "out_tok_median": _median_or_none(
                    (r["usage"] or {}).get("outputTokens") for r in usable
                ),
                "chars_median": _median_or_none((r["density"] or {}).get("chars") for r in usable),
                "evidence_items_median": _median_or_none(
                    (r["density"] or {}).get("evidence_items") for r in usable
                ),
                "evidence_per_1k_chars_median": _median_or_none(
                    (r["density"] or {}).get("evidence_per_1k_chars") for r in usable
                ),
                "signals_cited_median": _median_or_none(
                    (r["signals"] or {}).get("signals_cited") for r in usable
                ),
                "grounded_fraction_median": _median_or_none(
                    (r["grounding"] or {}).get("grounded_fraction") for r in usable
                ),
                "grounded_signals_median": _median_or_none(
                    (r["grounding"] or {}).get("signals_grounded_in_context") for r in usable
                ),
                "band_justified_rate": (
                    round(sum(1 for r in usable if r["band_justified"]) / len(usable), 3)
                    if usable
                    else None
                ),
            },
            "judges": {},
        }
        for judge in JUDGES:
            scored = _judge_scores(usable, judge)
            cell["judges"][judge] = {
                "n_judged": len(scored),
                "n_errors": sum(
                    1 for r in usable if (r["judges"].get(judge) or {}).get("error")
                ),
                "calibration_mean": _mean_or_none(v["calibration_score"] for v in scored),
                "calibration_min": min((v["calibration_score"] for v in scored), default=None),
                "substance_mean": _mean_or_none(v["substance_score"] for v in scored),
                "false_positive": sum(
                    1 for v in scored if v["false_positive_verdict"] == "FalsePositive"
                ),
                "false_negative": sum(
                    1 for v in scored if v["false_positive_verdict"] == "FalseNegative"
                ),
                "escalates": sum(1 for v in scored if v["escalation_drift"] == "escalates"),
                "dismisses": sum(1 for v in scored if v["escalation_drift"] == "dismisses"),
                "padding_observed": sum(1 for v in scored if v["padding_observed"]),
                "cites_context_fields": sum(1 for v in scored if v["cites_context_fields"]),
            }
        out.append(cell)
    return out


def _violations_by_rule(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for violation in row["contract_violations"]:
            counts[violation["rule"]] = counts.get(violation["rule"], 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# The two-judge rule: agreement is the signal, disagreement is no finding
# ---------------------------------------------------------------------------


def _set_rows(evaluated: Sequence[dict[str, Any]], which: str, domain: str | None) -> list[dict[str, Any]]:
    return [
        r
        for r in evaluated
        if r["set"] == which
        and not r["error"]
        and r["declared_band"] is not None
        and (domain is None or r["domain"] == domain)
    ]


def _judge_margin(
    evaluated: Sequence[dict[str, Any]], judge: str, field: str, domain: str | None = None
) -> float | None:
    """GPT-minus-Claude mean on ``field`` as scored by one judge. None if either side is empty."""
    gpt = [
        v[field]
        for v in _judge_scores(_set_rows(evaluated, "gpt", domain), judge)
    ]
    claude = [
        v[field]
        for v in _judge_scores(_set_rows(evaluated, "claude", domain), judge)
    ]
    if not gpt or not claude:
        return None
    return round(statistics.fmean(gpt) - statistics.fmean(claude), 3)


def judge_self_preference(evaluated: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Each judge's measured margin FOR ITS OWN FAMILY, on the same 120 analyses.

    This is the number that decides how much of any judged difference to believe. Both
    judges score the same anonymised texts, so if the two margins point in opposite
    directions the difference between them is judge identity, not analysis quality - and
    the deterministic cross-check (band agreement, contract violations, grounding, which
    no judge touches) says which way the underlying reality actually goes.

    Reported as a caution, never subtracted. A "bias-corrected" score would look
    adjusted and would never have been measured.
    """
    out: dict[str, Any] = {"note": JUDGE_CONFLICT_NOTE, "per_judge": {}}
    for judge, family in JUDGES.items():
        calibration = _judge_margin(evaluated, judge, "calibration_score")
        substance = _judge_margin(evaluated, judge, "substance_score")
        if calibration is None or substance is None:
            out["per_judge"][judge] = {"family": family, "assessable": False}
            continue
        # A positive gpt_minus_claude margin favours GPT. Convert to "favours own family".
        sign = 1.0 if family == "gpt" else -1.0
        out["per_judge"][judge] = {
            "family": family,
            "assessable": True,
            "gpt_minus_claude": {"calibration": calibration, "substance": substance},
            "own_family_margin": {
                "calibration": round(calibration * sign, 3),
                "substance": round(substance * sign, 3),
            },
        }
    gpt_rows = _set_rows(evaluated, "gpt", None)
    claude_rows = _set_rows(evaluated, "claude", None)
    out["deterministic_cross_check"] = {
        "band_agreement_gpt_minus_claude": (
            round(
                statistics.fmean(1.0 if r["band_agrees"] else 0.0 for r in gpt_rows)
                - statistics.fmean(1.0 if r["band_agrees"] else 0.0 for r in claude_rows),
                3,
            )
            if gpt_rows and claude_rows
            else None
        ),
        "contract_violations_gpt_minus_claude": (
            round(
                statistics.fmean(r["contract_violation_count"] for r in gpt_rows)
                - statistics.fmean(r["contract_violation_count"] for r in claude_rows),
                3,
            )
            if gpt_rows and claude_rows
            else None
        ),
        "grounded_fraction_gpt_minus_claude": (
            round(
                statistics.fmean(
                    (r["grounding"] or {}).get("grounded_fraction") or 0.0 for r in gpt_rows
                )
                - statistics.fmean(
                    (r["grounding"] or {}).get("grounded_fraction") or 0.0 for r in claude_rows
                ),
                3,
            )
            if gpt_rows and claude_rows
            else None
        ),
        "note": (
            "computed with no judge involved. A judge-scored margin unaccompanied by a "
            "deterministic margin in the same direction is more likely preference than quality."
        ),
    }
    return out


#: A judged margin smaller than this is treated as no difference, whichever way it
#: points. It is not a confidence interval - the sample is far too small for one - it is
#: the prior study's MEASURED self-preference on calibration (+0.31), used as the floor
#: below which a judge's opinion cannot be distinguished from its own identity.
JUDGE_MARGIN_FLOOR: Final[float] = 0.31


def two_judge_agreement(evaluated: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Do the two oppositely-biased judges agree on which set is better?

    Per axis and per agent, the rule is mechanical:

      * both margins favour GPT by more than :data:`JUDGE_MARGIN_FLOOR` -> "GPT better".
      * both favour Claude by more than the floor -> "Claude better".
      * both inside the floor -> "no difference" (a real finding, and the likeliest one:
        it is what "as good at the fraud analysis" looks like when it is true).
      * they point opposite ways, or only one clears the floor -> "NOT ESTABLISHED".
        This is the case the design exists to catch, and no recommendation may rest on it.
    """
    judges = list(JUDGES)
    domains = sorted({r["domain"] for r in evaluated})
    rows: list[dict[str, Any]] = []
    for field in ("calibration_score", "substance_score"):
        for domain in [None, *domains]:
            margins = {j: _judge_margin(evaluated, j, field, domain) for j in judges}
            present = [m for m in margins.values() if m is not None]
            if len(present) < len(judges):
                verdict = "NOT ESTABLISHED - at least one judge produced no verdict here"
            elif all(m > JUDGE_MARGIN_FLOOR for m in present):
                verdict = "GPT better on both judges"
            elif all(m < -JUDGE_MARGIN_FLOOR for m in present):
                verdict = "CLAUDE better on both judges"
            elif all(abs(m) <= JUDGE_MARGIN_FLOOR for m in present):
                verdict = "NO DIFFERENCE - both margins inside the measured bias floor"
            else:
                verdict = "NOT ESTABLISHED - the two judges disagree in sign or magnitude"
            rows.append(
                {
                    "axis": field,
                    "scope": domain or "ALL AGENTS POOLED",
                    "gpt_minus_claude_by_judge": margins,
                    "bias_floor": JUDGE_MARGIN_FLOOR,
                    "verdict": verdict,
                }
            )
    return {
        "rule": (
            "Agreement between two judges biased in OPPOSITE directions is the signal; "
            f"disagreement is 'not established'. Margins within +/-{JUDGE_MARGIN_FLOOR} (the "
            "prior study's measured self-preference on calibration) are read as no "
            "difference rather than as a small effect."
        ),
        "axes": rows,
    }


# ---------------------------------------------------------------------------
# The length gap: is GPT's brevity dropping evidence or dropping padding?
# ---------------------------------------------------------------------------


def length_gap(evaluated: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The strongest argument against GPT, tested rather than asserted.

    Luna writes ~425 output tokens on rings against Opus's ~2,080. Pairing is by
    (application, domain), so only analyses of the SAME payload are compared and a length
    difference cannot be an artifact of one model drawing easier applications.

    Four ratios, because they can disagree and the disagreement is the answer:

      * ``chars_ratio`` - how much longer the Claude analysis is.
      * ``evidence_ratio`` - distinct numeric facts plus signal identifiers.
      * ``signals_cited_ratio`` - how many of the payload's OWN signals each names. This
        is the one that decides the question: a ratio at 1.0 with chars at 4.0 means the
        extra length carried no extra signal.
      * ``grounded_ratio`` - ``_context`` fields validated, i.e. the instruction the
        customer cares most about and the one brevity is most likely to sacrifice.

    Plus ``band_justified_both``, so a shorter analysis cannot pass by declaring a band
    with no reason attached.
    """
    pairs: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in evaluated:
        if row["error"] or not row["density"] or row["declared_band"] is None:
            continue
        pairs.setdefault((row["app"], row["domain"]), {})[row["model_label"]] = row

    domains = sorted({domain for _, domain in pairs})
    comparisons: list[dict[str, Any]] = []
    for claude_label in CLAUDE_SET:
        for gpt_label in GPT_SET:
            for domain in [None, *domains]:
                comparison = _paired(pairs, claude_label, gpt_label, domain)
                if comparison:
                    comparisons.append(comparison)
    return {
        "method": (
            "paired by (application, domain); only cells where BOTH models produced a "
            "readable analysis are compared. 'signals cited' counts the payload's own "
            "signal names (identifier or de-underscored prose form), so a model is not "
            "rewarded for writing in identifiers. Reported pooled AND per agent, because "
            "pooling four agents can cancel out the one that motivated the question."
        ),
        "reading": (
            "chars_ratio well above signals_cited_ratio and grounded_ratio means the "
            "longer (Claude) analysis is padding: same evidence, more words. chars_ratio "
            "at or below those ratios means the shorter (GPT) analysis really is dropping "
            "evidence, which outweighs any latency or cost win."
        ),
        "comparisons": comparisons,
    }


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    return round(numerator / max(denominator, 1), 2)


def _paired(
    pairs: dict[tuple[str, str], dict[str, dict[str, Any]]],
    claude_label: str,
    gpt_label: str,
    domain: str | None,
) -> dict[str, Any] | None:
    both = [
        p
        for (_, dom), p in sorted(pairs.items())
        if claude_label in p and gpt_label in p and (domain is None or dom == domain)
    ]
    if not both:
        return None
    deltas = []
    for pair in both:
        claude, gpt = pair[claude_label], pair[gpt_label]
        deltas.append(
            {
                "app": claude["app"],
                "domain": claude["domain"],
                "chars_ratio": _ratio(claude["density"]["chars"], gpt["density"]["chars"]),
                "evidence_ratio": _ratio(
                    claude["density"]["evidence_items"], gpt["density"]["evidence_items"]
                ),
                "signals_cited_ratio": _ratio(
                    (claude["signals"] or {}).get("signals_cited"),
                    (gpt["signals"] or {}).get("signals_cited"),
                ),
                "grounded_ratio": _ratio(
                    (claude["grounding"] or {}).get("signals_grounded_in_context"),
                    (gpt["grounding"] or {}).get("signals_grounded_in_context"),
                ),
                "signals_cited": {
                    claude_label: (claude["signals"] or {}).get("signals_cited"),
                    gpt_label: (gpt["signals"] or {}).get("signals_cited"),
                },
                "band_justified_both": bool(claude["band_justified"]) and bool(gpt["band_justified"]),
                "same_band": claude["declared_band"] == gpt["declared_band"],
            }
        )

    def median(field: str) -> float | None:
        values = [d[field] for d in deltas if d[field] is not None]
        return round(statistics.median(values), 2) if values else None

    chars = median("chars_ratio")
    signals = median("signals_cited_ratio")
    grounded = median("grounded_ratio")
    if chars is None or signals is None:
        finding = "not computable on this pairing"
    elif chars > signals * 1.25 and (grounded is None or chars > grounded * 1.25):
        finding = (
            f"PADDING: the Claude analysis is {chars}x longer but names only {signals}x "
            "the payload's signals"
        )
    elif signals > chars:
        finding = (
            f"EVIDENCE LOST: the shorter GPT analysis names {round(1 / signals, 2)}x the "
            f"signals for {round(1 / chars, 2)}x the length"
        )
    else:
        finding = f"length and evidence scale together ({chars}x chars, {signals}x signals)"
    return {
        "scope": domain or "ALL AGENTS POOLED",
        "longer_claude": claude_label,
        "shorter_gpt": gpt_label,
        "n_paired": len(both),
        "chars_ratio_median": chars,
        "evidence_ratio_median": median("evidence_ratio"),
        "signals_cited_ratio_median": signals,
        "grounded_ratio_median": grounded,
        "pairs_same_band": sum(1 for d in deltas if d["same_band"]),
        "pairs_both_justified": sum(1 for d in deltas if d["band_justified_both"]),
        "finding": finding,
        "pairs": deltas,
    }


def known_failure_reproduction(evaluated: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Do the two calibration failures every Claude tier shared reproduce in the GPT set?

    Both cases are the same fault - alerts arising from one underlying behaviour read as
    independent corroboration - and both were shared by ALL Claude tiers, so they are a
    property of the prompt rather than of a model. Whether the other family repeats them
    is therefore genuinely new information, and it is reported per model rather than per
    set: "GPT reproduces it" and "one GPT variant reproduces it" are different findings.
    """
    out: list[dict[str, Any]] = []
    for app, domain, expected, claude_declared in KNOWN_SHARED_FAILURES:
        rows = [
            r
            for r in evaluated
            if r["app"] == app and r["domain"] == domain and not r["error"]
        ]
        per_model = [
            {
                "model_label": r["model_label"],
                "set": r["set"],
                "declared_band": r["declared_band"],
                "reproduces": r["declared_band"] == claude_declared,
                "correct": r["band_agrees"],
            }
            for r in sorted(rows, key=lambda r: (r["set"], r["model_label"]))
        ]
        gpt = [p for p in per_model if p["set"] == "gpt"]
        claude = [p for p in per_model if p["set"] == "claude"]
        out.append(
            {
                "app": app,
                "domain": domain,
                "pinned_band": expected,
                "prior_claude_failure_band": claude_declared,
                "requirement_id": next((r["requirement_id"] for r in rows), None),
                "customer_rule": next((r["customer_rule"] for r in rows), None),
                "n_models": len(per_model),
                "gpt_reproduces": sum(1 for p in gpt if p["reproduces"]),
                "gpt_n": len(gpt),
                "claude_reproduces": sum(1 for p in claude if p["reproduces"]),
                "claude_n": len(claude),
                "gpt_correct": sum(1 for p in gpt if p["correct"]),
                "claude_correct": sum(1 for p in claude if p["correct"]),
                "per_model": per_model,
            }
        )
    return out


# ---------------------------------------------------------------------------
# The verdict, per (agent, set)
# ---------------------------------------------------------------------------

#: The incumbent Claude model per agent, i.e. what a switch would be switching away
#: from. rings and synthetic ran on Opus 5 in the 14x3 pipeline benchmark; the prior tier
#: study recommended Sonnet 5 for both, so both are candidates here and the verdict names
#: which Claude model it is comparing against.
INCUMBENT: Final[dict[str, str]] = {
    "rings": "opus-5",
    "synthetic": "opus-5",
    "bustout": "sonnet-5",
    "identity": "sonnet-5",
}


def per_set_verdict(cells: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per (agent, GPT model): is it as good as the Claude incumbent, and is n enough?

    Mechanical, and ordered by what this customer is actually buying:

      1. **False positives on the anti-false-positive fixtures.** Any excess over the
         incumbent is disqualifying on its own. This is the customer's stated goal, so it
         outranks every other axis including cost.
      2. **False negatives** - a dismissed corroborated application. Also disqualifying,
         because it is the expensive error in the other direction.
      3. **Band agreement** must not be worse than the incumbent's on the same
         applications.
      4. **Grounding** must not collapse: an analysis that never validates against
         ``_context`` is not doing the job the prompt describes, however well it guesses
         the band.

    Only then are latency and cost reported - as the consequence of a decision the four
    axes above already made, never as an input to it. A cell below
    :data:`MIN_APPS_FOR_A_BAND_CLAIM` applications says so regardless of how well it
    scored.
    """
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        by_domain.setdefault(cell["domain"], []).append(cell)

    verdicts: list[dict[str, Any]] = []
    for domain, group in sorted(by_domain.items()):
        incumbent_label = INCUMBENT.get(domain, "")
        incumbent = next((c for c in group if c["model_label"] == incumbent_label), None)
        if incumbent is None:
            verdicts.append(
                {
                    "domain": domain,
                    "incumbent": incumbent_label,
                    "verdict": "NOT ASSESSED - the incumbent Claude model was not swept here",
                    "candidates": [],
                }
            )
            continue
        base_fp = incumbent["anti_false_positive"]["false_positives"]
        base_fn = incumbent["false_negatives"]["count"]
        base_band = incumbent["band"]["agreements"]
        base_ground = incumbent["measured"]["grounded_fraction_median"] or 0.0
        candidates: list[dict[str, Any]] = []
        for cell in group:
            if cell["set"] != "gpt":
                continue
            fp = cell["anti_false_positive"]["false_positives"]
            fn = cell["false_negatives"]["count"]
            band = cell["band"]["agreements"]
            ground = cell["measured"]["grounded_fraction_median"] or 0.0
            reasons: list[str] = []
            if fp > base_fp:
                reasons.append(
                    f"{fp} false positive(s) on the anti-FP fixtures against the "
                    f"incumbent's {base_fp} ({', '.join(cell['anti_false_positive']['apps_escalated'])})"
                )
            if fn > base_fn:
                reasons.append(
                    f"{fn} dismissal(s) of a corroborated application against the "
                    f"incumbent's {base_fn} ({', '.join(cell['false_negatives']['apps'])})"
                )
            if band < base_band:
                reasons.append(
                    f"band agreement {band}/{cell['band']['n']} below the incumbent's "
                    f"{base_band}/{incumbent['band']['n']}"
                )
            # Half the incumbent's grounding is the line: below that the analysis is
            # asserting signal values rather than validating them, which is the one
            # instruction every specialist prompt states.
            if ground < base_ground / 2:
                reasons.append(
                    f"grounded fraction {ground} is less than half the incumbent's {base_ground}"
                )
            # An analysis that declares no band cannot support a band claim, so the sample
            # test uses the BAND-READABLE count rather than the answered count. A model
            # that answers six times and declares a band twice has n=2 here, which is the
            # honest reading and is also a quality signal in its own right.
            if cell["n_unreadable_band"]:
                reasons.append(
                    f"{cell['n_unreadable_band']} analysis/analyses declared no locatable "
                    f"risk band ({', '.join(cell['unreadable_band_apps'])}), which breaches "
                    f"the customer's requirement that reasoning, classification and "
                    f"conclusion align"
                )
            if cell["band"]["n"] < MIN_APPS_FOR_A_BAND_CLAIM:
                verdict = (
                    f"n TOO SMALL - {cell['band']['n']} application(s) with a readable band; "
                    f"a band claim needs {MIN_APPS_FOR_A_BAND_CLAIM}"
                )
            elif reasons:
                verdict = "NOT AS GOOD: " + "; ".join(reasons)
            else:
                verdict = "AS GOOD ON THIS SAMPLE"
            candidates.append(
                {
                    "model_label": cell["model_label"],
                    "verdict": verdict,
                    "n": cell["band"]["n"],
                    "n_answered": cell["n_usable"],
                    "n_unreadable_band": cell["n_unreadable_band"],
                    "band": f"{band}/{cell['band']['n']}",
                    "anti_fp_false_positives": f"{fp}/{cell['anti_false_positive']['n_fixtures']}",
                    "false_negatives": f"{fn}/{cell['false_negatives']['n_at_risk']}",
                    "grounded_fraction_median": cell["measured"]["grounded_fraction_median"],
                    "contract_violations": cell["contract"]["total_violations"],
                    "wall_s_median": cell["measured"]["wall_s_median"],
                    "cost_usd_median": cell["measured"]["cost_usd_median"],
                    "out_tok_median": cell["measured"]["out_tok_median"],
                    "disqualifying": bool(reasons),
                }
            )
        verdicts.append(
            {
                "domain": domain,
                "incumbent": incumbent_label,
                "incumbent_scores": {
                    "n": incumbent["band"]["n"],
                    "n_answered": incumbent["n_usable"],
                    "n_unreadable_band": incumbent["n_unreadable_band"],
                    "band": f"{base_band}/{incumbent['band']['n']}",
                    "anti_fp_false_positives": (
                        f"{base_fp}/{incumbent['anti_false_positive']['n_fixtures']}"
                    ),
                    "false_negatives": f"{base_fn}/{incumbent['false_negatives']['n_at_risk']}",
                    "grounded_fraction_median": incumbent["measured"]["grounded_fraction_median"],
                    "contract_violations": incumbent["contract"]["total_violations"],
                    "wall_s_median": incumbent["measured"]["wall_s_median"],
                    "cost_usd_median": incumbent["measured"]["cost_usd_median"],
                    "out_tok_median": incumbent["measured"]["out_tok_median"],
                },
                "candidates": candidates,
                "sample_note": (
                    f"{incumbent['n_usable']} application(s) answered per cell, "
                    f"{incumbent['band']['n']} with a readable band; a band claim needs "
                    f"{MIN_APPS_FOR_A_BAND_CLAIM}. Anti-false-positive fixtures in this "
                    f"sample: {incumbent['anti_false_positive']['n_fixtures']}."
                ),
            }
        )
    return verdicts


def build_report(
    evaluated: Sequence[dict[str, Any]],
    *,
    region: str,
    live_calls: dict[str, Any],
) -> dict[str, Any]:
    """The full result document."""
    cells = aggregate(evaluated)
    specialist_cost = [r["cost_usd"] for r in evaluated if r.get("cost_usd")]
    judge_cost = [
        entry["cost_usd"]
        for r in evaluated
        for entry in r["judges"].values()
        if entry.get("cost_usd")
    ]
    return {
        "schema": SCHEMA,
        "run": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "aws_region": region,
            "mantle_region": mantle.resolve_mantle_region("openai.gpt-5.6-luna"),
            "claude_set": CLAUDE_SET,
            "gpt_set": GPT_SET,
            "judges": JUDGES,
            "domains": list(SWEEP_DOMAINS),
            "applications": list(SWEEP_APPS),
            "anti_false_positive_applications": [
                a for a in SWEEP_APPS if a in ANTI_FP_APPS
            ],
            "shuffle_seed": SHUFFLE_SEED,
            "specialist_max_output_tokens": SPECIALIST_MAX_TOKENS,
            "blinding": (
                "model/provider identifiers stripped from the analysis text before "
                "judging (evals.tier_quality.anonymise); judge order shuffled under the "
                "fixed seed; both judges see the same anonymised text and the same rubric"
            ),
        },
        "live_calls": live_calls,
        "spend_actual_usd": {
            "specialist_calls": round(sum(specialist_cost), 4) if specialist_cost else None,
            "judge_calls": round(sum(judge_cost), 4) if judge_cost else None,
            "total": (
                round(sum(specialist_cost) + sum(judge_cost), 4)
                if specialist_cost or judge_cost
                else None
            ),
            "note": (
                "measured from each call's own reported usage. GPT costs include the 1.25x "
                "cache-WRITE term: omitting it under-reported a measured call by 62%. Rows "
                "reused from an earlier sweep carry that sweep's measured cost; rows with "
                "no recorded usage contribute nothing rather than an estimate."
            ),
        },
        "per_cell": cells,
        "per_set_verdict": per_set_verdict(cells),
        "two_judge_agreement": two_judge_agreement(evaluated),
        "judge_self_preference": judge_self_preference(evaluated),
        "length_gap": length_gap(evaluated),
        "known_failure_reproduction": known_failure_reproduction(evaluated),
        "per_analysis": list(evaluated),
        "caveats": [
            JUDGE_CONFLICT_NOTE,
            "The recommendation rests on the DETERMINISTIC layers - band agreement "
            "against the pinned fixture, escalation on the anti-false-positive fixtures, "
            "_context grounding, and agents.output_contract.check. The judged calibration "
            "and substance scores are subjective and are reported separately, and an axis "
            "where the two judges disagree yields no finding at all.",
            f"{len(SWEEP_APPS)} applications per cell. A band-agreement difference of one "
            f"case is {round(100 / len(SWEEP_APPS))} points, so a one-case gap between two "
            "models is inside this sample's resolution by construction.",
            "Only 2 of the 5 anti-false-positive fixtures (APP-1009, APP-1010) exercise "
            "the four agents swept here; APP-1006/1007/1008 target employment, dealer and "
            "income, which were not swept. The false-positive finding is therefore narrow "
            "and a wider anti-FP sweep is the obvious next measurement.",
            "Percentiles come from evals.bench.summarize_values: p50 needs 5 samples, p95 "
            "needs 20. Cells below those counts report the refusal, not a number.",
            "GPT specialists receive the verbatim prompt concatenated with the user turn "
            "(the mantle Responses shape agents.synthesize already uses), while Claude "
            "specialists receive it as a cached system prompt. That is a real difference in "
            "how the two families are invoked and it cannot be removed while GPT-5.x is "
            "off Converse.",
            "Latency and cost here are single serial calls, not the fan-out. The "
            "end-to-end consequence of a swap belongs to the pipeline benchmark.",
        ],
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _fmt(value: Any, dash: str = "—") -> str:
    return dash if value is None else str(value)


def _judge_short(judge: str) -> str:
    """Short judge label for a table header, with the family it is biased toward.

    Splitting on "." would render ``openai.gpt-5.5`` as "5", so the whole tail after the
    first dot is kept and the family is appended - the reader needs to know which way a
    column is expected to lean in order to read the margin at all.
    """
    tail = judge.split(".", 1)[-1] if "." in judge else judge
    return f"{tail} ({JUDGES.get(judge, '?')})"


def render_markdown(report: dict[str, Any]) -> str:
    """Human-readable summary. Every table cell traces to a measured field."""
    lines: list[str] = []
    run = report["run"]
    lines.append("# Is the GPT set as good at the fraud analysis?")
    lines.append("")
    lines.append(
        f"Blind, two judges: `{'`, `'.join(run['judges'])}`. "
        f"{len(run['domains'])} agents x {len(run['applications'])} applications x "
        f"{len(run['claude_set']) + len(run['gpt_set'])} models. "
        f"Region `{run['aws_region']}` (mantle `{run['mantle_region']}`). "
        f"Generated {run['timestamp_utc']}."
    )
    lines.append("")
    spend = report["spend_actual_usd"]
    lines.append(
        f"Measured spend: specialists ${_fmt(spend['specialist_calls'])}, judges "
        f"${_fmt(spend['judge_calls'])}, total ${_fmt(spend['total'])}."
    )
    lines.append("")

    lines.append("## The verdict, per (agent, GPT model)")
    lines.append("")
    for verdict in report["per_set_verdict"]:
        lines.append(f"### {verdict['domain']} (incumbent `{verdict['incumbent']}`)")
        lines.append("")
        incumbent = verdict.get("incumbent_scores")
        if incumbent:
            lines.append(
                f"- incumbent `{verdict['incumbent']}`: band {incumbent['band']}, anti-FP "
                f"false positives {incumbent['anti_fp_false_positives']}, false negatives "
                f"{incumbent['false_negatives']}, grounded "
                f"{_fmt(incumbent['grounded_fraction_median'])}, contract violations "
                f"{incumbent['contract_violations']}, {_fmt(incumbent['wall_s_median'])}s, "
                f"${_fmt(incumbent['cost_usd_median'])}/call, "
                f"{_fmt(incumbent['out_tok_median'])} out tok"
            )
        for candidate in verdict["candidates"]:
            lines.append(
                f"- **`{candidate['model_label']}`: {candidate['verdict']}** — band "
                f"{candidate['band']}, anti-FP false positives "
                f"{candidate['anti_fp_false_positives']}, false negatives "
                f"{candidate['false_negatives']}, grounded "
                f"{_fmt(candidate['grounded_fraction_median'])}, contract violations "
                f"{candidate['contract_violations']}, {_fmt(candidate['wall_s_median'])}s, "
                f"${_fmt(candidate['cost_usd_median'])}/call, "
                f"{_fmt(candidate['out_tok_median'])} out tok"
            )
        if verdict.get("sample_note"):
            lines.append(f"- Sample: {verdict['sample_note']}")
        lines.append("")

    lines.append("## Per (agent, model): deterministic scores")
    lines.append("")
    lines.append(
        "| agent | set | model | answered | no band | band | escalations | dismissals | "
        "anti-FP false pos | contract viol | grounded frac p50 | signals cited p50 | "
        "wall p50 s | $/call p50 | out tok p50 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for cell in report["per_cell"]:
        band = cell["band"]
        measured = cell["measured"]
        lines.append(
            f"| {cell['domain']} | {cell['set']} | {cell['model_label']} | {cell['n_usable']} "
            f"| {cell['n_unreadable_band']} "
            f"| {band['agreements']}/{band['n']} | {band['escalations']} | {band['dismissals']} "
            f"| {cell['anti_false_positive']['false_positives']}/"
            f"{cell['anti_false_positive']['n_fixtures']} "
            f"| {cell['contract']['total_violations']} "
            f"| {_fmt(measured['grounded_fraction_median'])} "
            f"| {_fmt(measured['signals_cited_median'])} "
            f"| {_fmt(measured['wall_s_median'])} | {_fmt(measured['cost_usd_median'])} "
            f"| {_fmt(measured['out_tok_median'])} |"
        )
    lines.append("")

    lines.append("## Per (agent, model): what each judge thought")
    lines.append("")
    judges = list(run["judges"])
    header = "| agent | set | model | " + " | ".join(
        f"{_judge_short(j)} cal/sub" for j in judges
    ) + " |"
    lines.append(header)
    lines.append("|---|---|---|" + "---|" * len(judges))
    for cell in report["per_cell"]:
        scores = []
        for judge in judges:
            entry = cell["judges"].get(judge, {})
            scores.append(
                f"{_fmt(entry.get('calibration_mean'))}/{_fmt(entry.get('substance_mean'))} "
                f"(n={entry.get('n_judged', 0)})"
            )
        lines.append(
            f"| {cell['domain']} | {cell['set']} | {cell['model_label']} | "
            + " | ".join(scores)
            + " |"
        )
    lines.append("")

    lines.append("## Do the two oppositely-biased judges agree?")
    lines.append("")
    agreement = report["two_judge_agreement"]
    lines.append(agreement["rule"])
    lines.append("")
    lines.append("| axis | scope | " + " | ".join(_judge_short(j) for j in judges) + " | verdict |")
    lines.append("|---|---|" + "---|" * len(judges) + "---|")
    for axis in agreement["axes"]:
        margins = [
            _fmt(axis["gpt_minus_claude_by_judge"].get(j)) for j in judges
        ]
        lines.append(
            f"| {axis['axis']} | {axis['scope']} | " + " | ".join(margins) + f" | {axis['verdict']} |"
        )
    lines.append("")
    lines.append("Margins are GPT minus Claude: positive favours GPT.")
    lines.append("")

    lines.append("## Each judge's measured preference for its own family")
    lines.append("")
    bias = report["judge_self_preference"]
    lines.append(bias["note"])
    lines.append("")
    for judge, entry in bias["per_judge"].items():
        if not entry.get("assessable"):
            lines.append(f"- `{judge}` ({entry['family']} family): not assessable on this sample")
            continue
        own = entry["own_family_margin"]
        lines.append(
            f"- `{judge}` ({entry['family']} family): scored its OWN family "
            f"{own['calibration']:+} on calibration and {own['substance']:+} on substance"
        )
    cross = bias["deterministic_cross_check"]
    lines.append(
        f"- Deterministic cross-check, GPT minus Claude, no judge involved: band agreement "
        f"{_fmt(cross['band_agreement_gpt_minus_claude'])}, contract violations "
        f"{_fmt(cross['contract_violations_gpt_minus_claude'])}, grounded fraction "
        f"{_fmt(cross['grounded_fraction_gpt_minus_claude'])}"
    )
    lines.append("")

    lines.append("## The length gap: padding or lost evidence?")
    lines.append("")
    gap = report["length_gap"]
    lines.append(gap["reading"])
    lines.append("")
    lines.append(
        "| scope | Claude | GPT | paired | chars ratio | evidence ratio | signals-cited "
        "ratio | grounded ratio | same band | finding |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for comparison in gap["comparisons"]:
        lines.append(
            f"| {comparison['scope']} | {comparison['longer_claude']} "
            f"| {comparison['shorter_gpt']} | {comparison['n_paired']} "
            f"| {_fmt(comparison['chars_ratio_median'])}x "
            f"| {_fmt(comparison['evidence_ratio_median'])}x "
            f"| {_fmt(comparison['signals_cited_ratio_median'])}x "
            f"| {_fmt(comparison['grounded_ratio_median'])}x "
            f"| {comparison['pairs_same_band']}/{comparison['n_paired']} "
            f"| {comparison['finding']} |"
        )
    lines.append("")

    lines.append("## Do the two shared Claude calibration failures reproduce in GPT?")
    lines.append("")
    for case in report["known_failure_reproduction"]:
        lines.append(
            f"### {case['app']} / {case['domain']} — pinned **{case['pinned_band']}**, every "
            f"prior Claude tier said **{case['prior_claude_failure_band']}** "
            f"({case['requirement_id']})"
        )
        lines.append("")
        lines.append(
            f"- GPT reproduced it {case['gpt_reproduces']}/{case['gpt_n']}; Claude "
            f"{case['claude_reproduces']}/{case['claude_n']}. Correct band: GPT "
            f"{case['gpt_correct']}/{case['gpt_n']}, Claude "
            f"{case['claude_correct']}/{case['claude_n']}."
        )
        for entry in case["per_model"]:
            lines.append(
                f"  - `{entry['model_label']}` ({entry['set']}): {_fmt(entry['declared_band'])}"
                + (" — correct" if entry["correct"] else "")
            )
        lines.append("")

    lines.append("## Caveats")
    lines.append("")
    for caveat in report["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--reuse", action="append", default=[], help="JSON of analyses already paid for"
    )
    parser.add_argument("--sweep", default="", help="comma-separated domains to sweep live")
    parser.add_argument("--apps", default=",".join(SWEEP_APPS))
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--judge", action="store_true", help="run both blind judges")
    parser.add_argument("--judges", default=",".join(JUDGES))
    parser.add_argument("--live", action="store_true", help="permit billed calls")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and estimate only")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="rebuild the report from the committed analyses and verdicts; spends nothing",
    )
    parser.add_argument("--out", default="evals/results/set_comparison.json")
    parser.add_argument("--markdown", default="evals/results/set_comparison.md")
    parser.add_argument("--analyses-out", default="evals/results/set_comparison_analyses.json")
    parser.add_argument("--verdicts", default="evals/results/set_comparison_verdicts.json")
    return parser


def _plan(
    domains: Sequence[str],
    apps: Sequence[str],
    labels: Sequence[str],
    have: set[str],
) -> list[tuple[str, str, str]]:
    """Cells still to buy: the full grid minus anything already on disk."""
    planned = []
    for app in apps:
        for domain in domains:
            for label in labels:
                model_id = MODELS.get(label, label)
                if f"{domain}|{model_id}|{app}" in have:
                    continue
                planned.append((domain, model_id, app))
    return planned


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    domains = [d for d in args.sweep.split(",") if d]
    apps = [a for a in args.apps.split(",") if a]
    labels = [m for m in args.models.split(",") if m]
    judges = [j for j in args.judges.split(",") if j]

    analyses_path = REPO_ROOT / args.analyses_out
    verdict_path = REPO_ROOT / args.verdicts

    reuse_paths = list(args.reuse)
    if args.rescore and analyses_path.is_file() and str(analyses_path) not in reuse_paths:
        reuse_paths.append(str(analyses_path))

    reused: list[AnalysisRow] = []
    seen: set[str] = set()
    for path in reuse_paths:
        for row in load_analyses(path):
            if row.key in seen:
                continue
            seen.add(row.key)
            reused.append(row)

    plan = [] if args.rescore else _plan(domains, apps, labels, seen)

    cached: dict[str, dict[str, Any]] = {}
    if verdict_path.is_file():
        stored = json.loads(verdict_path.read_text(encoding="utf-8"))
        cached = {
            key: value
            for key, value in (stored.get("verdicts", {}) if isinstance(stored, dict) else {}).items()
            if "error" not in value
        }
    judge_calls: dict[str, int] = {}
    if args.judge and not args.rescore:
        keys = {r.key for r in reused if r.text} | {f"{d}|{m}|{a}" for d, m, a in plan}
        for judge in judges:
            judge_calls[judge] = len({k for k in keys if f"{judge}|{k}" not in cached})
    estimate = spend_estimate(plan, judge_calls)

    print(f"reused analyses on disk: {len(reused)}")
    print(f"planned specialist calls: {len(plan)}")
    print(f"cached verdicts reused:   {len(cached)}")
    print(f"planned judge calls:      {sum(judge_calls.values())} across {judges}")
    print(json.dumps({k: v for k, v in estimate.items() if k != "per_call"}, indent=1))
    if args.dry_run:
        return 0

    if plan or judge_calls:
        gate = _live_gate_error(args.live)
        if gate:
            print(f"refusing to spend: {gate}", file=sys.stderr)
            return 2

    rows = list(reused)
    swept: list[AnalysisRow] = []
    if plan:
        swept = asyncio.run(run_sweep(plan, progress=print))
        rows.extend(swept)
        # Persist paid-for text BEFORE judging: a judge failure must not discard
        # specialist calls that have already been billed.
        _write_json(
            analyses_path,
            {
                "schema": ANALYSES_SCHEMA,
                "description": (
                    "verbatim specialist analyses from the set comparison (Claude and GPT "
                    "on identical payloads), kept so a re-judge costs judge calls only"
                ),
                "analyses": [r.to_reuse_json() for r in rows],
            },
        )
        print(f"wrote {analyses_path} ({len(rows)} analyses)")

    verdicts: dict[str, dict[str, Any]] = dict(cached)
    if args.judge and not args.rescore:
        verdicts = asyncio.run(
            judge_all(
                [r for r in rows if r.text], judges=judges, progress=print, existing=cached
            )
        )
        _write_json(
            verdict_path,
            {
                "schema": VERDICTS_SCHEMA,
                "description": (
                    "blind verdicts keyed judge|domain|model|application. Two judges, one "
                    "from each family, so agreement can be distinguished from bias"
                ),
                "judges": judges,
                "verdicts": verdicts,
            },
        )
        print(f"wrote {verdict_path} ({len(verdicts)} verdicts)")

    evaluated = [evaluate_row(row, verdicts) for row in rows]
    from agents.models import resolve_region

    report = build_report(
        evaluated,
        region=resolve_region(),
        live_calls={
            "specialist_calls_made": len(swept),
            "specialist_calls_failed": sum(1 for r in swept if r.error),
            "judge_calls_made": len(verdicts) - len(cached),
            "judge_verdicts_reused_from_cache": len(cached),
            "judge_calls_failed": sum(1 for v in verdicts.values() if "error" in v),
            "reused_analyses": len(reused),
            "reuse_sources": sorted({r.source for r in reused}),
        },
    )
    _write_json(REPO_ROOT / args.out, report)
    markdown_path = REPO_ROOT / args.markdown
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"\nwrote {REPO_ROOT / args.out}\nwrote {markdown_path}")
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
