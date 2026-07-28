"""
A second, independent judge: GPT-5.6 Sol on the bedrock-mantle endpoint.

WHY A SECOND JUDGE

``evals/tier_quality.py`` judges with Claude Opus 5, which is also one of the candidate
specialist models. That conflict was measured rather than assumed away: on its own 16 rows
the judge scored substance **+0.78** and calibration **+0.31** higher than on the other 32,
while the deterministic band-agreement margin moved only -0.026. So any conclusion resting
on a Claude model out-scoring a non-Claude one by less than ~0.31 calibration is not
supported by that judge alone.

The set comparison now asks a harder question -- is the **GPT-5.6 set** as good at the
fraud analysis as the Claude set? -- and a Claude judge is the wrong instrument for it in
exactly the direction that matters. A GPT judge has the mirror bias. So both are run and
the comparison of the two is the finding:

* where Opus 5 and Sol **agree**, the result holds despite two opposite biases;
* where they **disagree**, the honest answer is "not established", and the disagreement
  itself is reported rather than averaged into a single number.

Sol is deliberately the GPT judge rather than Luna or Terra: it is the only GPT-5.6 variant
NOT a candidate for any specialist assignment (Luna is the leading candidate and the
incumbent synthesizer, Terra is the middle candidate), so it is the one GPT model that is
not scoring a sibling it might be assigned alongside. It also measured the highest reasoning
effort of the three, which is what a judge should be.

WHAT IS HELD IDENTICAL

The judge instructions, the anonymisation, the verdict schema and the pinned expectation all
come from ``evals.tier_quality`` unchanged. Only the model and the transport differ. If the
prompt drifted between the two judges the comparison would be meaningless, so this module
imports those rather than restating them, and a test asserts it uses the same instruction
text.

TRANSPORT

GPT-5.x is not on Bedrock Converse, so this cannot reuse ``tier_quality.judge_analysis``
(which builds a Strands Agent). It goes through ``agents.mantle.complete`` and parses the
verdict JSON itself, validating against the same Pydantic model -- the same shape
``agents/synthesize.py`` uses for the master adjudication on the same endpoint.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Final

from agents import mantle
from evals.tier_quality import (
    JUDGE_MAX_TOKENS,
    AnalysisRow,
    _JUDGE_INSTRUCTIONS,
    _judge_verdict_model,
    anonymise,
)
from fixtures.loader import expected_for
from prompts.loader import SPECIALIST_PROMPTS

#: The GPT judge. Sol because it is the only GPT-5.6 variant that is not a candidate for a
#: specialist assignment, so it is not scoring a model it could be deployed beside.
SOL_JUDGE_MODEL: Final[str] = "openai.gpt-5.6-sol"

#: Sol is served in us-east-1 and us-east-2 only -- there is no us-west-2 Sol.
SOL_JUDGE_REGION: Final[str] = "us-east-1"

#: Judging is the one place to spend reasoning effort: the whole value of a second judge is
#: that it reasons independently rather than pattern-matching the prose.
SOL_JUDGE_EFFORT: Final[str] = "high"

#: Appended to the shared instructions. The Converse path gets its JSON shape enforced by a
#: forced tool call; the Responses API has no such tool here, so the schema is stated and
#: then validated on the way back. Nothing about the STANDARD is restated -- only the
#: output format -- so the two judges are held to identical criteria.
_JSON_INSTRUCTION: Final[str] = """

=== OUTPUT FORMAT ===
Return ONLY a JSON object, no markdown fence and no commentary, with exactly these keys:

{{
  "band_read_from_analysis": "<the band the analysis itself declares, verbatim>",
  "band_matches_expectation": <true|false>,
  "calibration_score": <integer 1-5, how well the band follows the customer's calibration>,
  "false_positive_verdict": "<FalsePositive|Correct|FalseNegative>",
  "escalation_drift": "<none|mild|material>",
  "substance_score": <integer 1-5, distinct grounded evidence, NOT length>,
  "padding_observed": <true|false>,
  "cites_context_fields": <true|false, does it validate signals against the paired _context>,
  "contract_notes": "<output-contract observations, or an empty string>",
  "justification": "<why, pointing at specific text; UNDER 1000 CHARACTERS>"
}}

Use exactly one of these for "false_positive_verdict": "FalsePositive" (it escalated
something the customer's rules call benign), "Correct" (the band and the reasoning both
stand), or "FalseNegative" (it cleared something the rules say should have been escalated).
Do not invent a fourth value. Keep "justification" under 1000 characters.
"""


def build_prompt(row: AnalysisRow) -> str:
    """The shared judge instructions plus a JSON-output instruction.

    The standard itself -- her verbatim prompt, the pinned band, the three overriding rules,
    the anti-false-positive list -- is ``tier_quality._JUDGE_INSTRUCTIONS`` unchanged, so
    both judges are answering the same question about the same anonymised text.
    """
    expectation = expected_for(row.app)["domains"][row.domain]
    body = _JUDGE_INSTRUCTIONS.format(
        domain=row.domain,
        prompt=SPECIALIST_PROMPTS[row.domain],
        app=row.app,
        expected_band=expectation["band"],
        requirement_id=expectation["requirement_id"],
        customer_rule=expectation["customer_rule"],
        analysis=anonymise(row.text),
    )
    return body + _JSON_INSTRUCTION.format()


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the verdict object out of the response.

    Tolerates a markdown fence because the Responses API has no forced-tool guarantee, but
    does not tolerate anything that is not a JSON object -- a judge that editorialises
    instead of answering is a failed judgement, not a partial one.
    """
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        if "\n" in body:
            body = body.split("\n", 1)[1]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in judge output: {body[:180]!r}")
    return json.loads(body[start : end + 1])


async def judge_analysis_sol(
    row: AnalysisRow,
    *,
    model_id: str = SOL_JUDGE_MODEL,
    region: str = SOL_JUDGE_REGION,
    effort: str = SOL_JUDGE_EFFORT,
) -> dict[str, Any]:
    """One blind judgement by the GPT judge, in the same shape as the Claude judge's.

    Returns ``{"verdict": {...}, "judge_model", "judge_wall_s", "judge_usage",
    "judge_cost_usd"}`` on success, or ``{"error": ..., "wall_s": ...}`` on failure -- the
    same contract ``tier_quality.judge_analysis`` returns, so an aggregator can consume
    either without branching.
    """
    verdict_model = _judge_verdict_model()
    prompt = build_prompt(row)
    started = time.perf_counter()
    try:
        response = await asyncio.to_thread(
            mantle.complete,
            model_id,
            prompt,
            max_output_tokens=JUDGE_MAX_TOKENS,
            effort=effort,
            region=region,
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "wall_s": round(time.perf_counter() - started, 3)}

    try:
        payload = _extract_json(response.get("text", ""))
        verdict = verdict_model.model_validate(payload)
    except Exception as exc:
        return {
            "error": f"judge output failed the verdict schema: {type(exc).__name__}: {exc}",
            "wall_s": round(time.perf_counter() - started, 3),
            "raw_text": (response.get("text") or "")[:400],
        }

    return {
        "verdict": verdict.model_dump(),
        "judge_model": model_id,
        "judge_region": response.get("region"),
        "judge_effort": response.get("effort"),
        "judge_wall_s": round(time.perf_counter() - started, 3),
        "judge_usage": response.get("usage"),
        "judge_cost_usd": response.get("cost_usd"),
    }


# ---------------------------------------------------------------------------
# Agreement between the two judges
# ---------------------------------------------------------------------------

#: Fields where the two judges can be compared directly. The scores are ordinal 1-5 and the
#: verdicts categorical, so agreement is exact-match on the categoricals and a signed delta
#: on the scores -- no averaging of two differently-biased opinions into one number.
COMPARABLE_CATEGORICAL: Final[tuple[str, ...]] = (
    "band_matches_expectation",
    "false_positive_verdict",
    "escalation_drift",
    "padding_observed",
    "cites_context_fields",
)
COMPARABLE_ORDINAL: Final[tuple[str, ...]] = ("calibration_score", "substance_score")


#: The three verdicts that carry a decision. Judges reach for synonyms ("PassWithNote",
#: "Pass", "Acceptable"), and treating a wording difference as a disagreement would
#: manufacture conflict where the two judges actually agree. Anything that is not clearly a
#: false positive or a false negative is a pass.
_FP_SYNONYMS: Final[dict[str, str]] = {
    "falsepositive": "FalsePositive",
    "false_positive": "FalsePositive",
    "overescalated": "FalsePositive",
    "falsenegative": "FalseNegative",
    "false_negative": "FalseNegative",
    "missed": "FalseNegative",
}


def normalise_fp_verdict(value: Any) -> str:
    """Map a judge's false-positive wording onto the three decision-bearing values."""
    key = str(value or "").strip().lower().replace(" ", "").replace("-", "")
    return _FP_SYNONYMS.get(key, "Correct")


def compare_verdicts(claude: dict[str, Any], sol: dict[str, Any]) -> dict[str, Any]:
    """Agreement between one Claude verdict and one Sol verdict on the same analysis.

    ``agrees`` is reserved for the categorical fields that carry a decision. A score delta
    is reported but never counted as disagreement on its own: two judges scoring 4 and 5 are
    not in conflict about anything the customer cares about.
    """
    def value(verdict: dict[str, Any], field: str) -> Any:
        if field == "false_positive_verdict":
            return normalise_fp_verdict(verdict.get(field))
        return verdict.get(field)

    agreement = {
        f: value(claude, f) == value(sol, f) for f in COMPARABLE_CATEGORICAL
    }
    deltas = {
        f: (
            None
            if claude.get(f) is None or sol.get(f) is None
            else int(sol[f]) - int(claude[f])
        )
        for f in COMPARABLE_ORDINAL
    }
    return {
        "categorical_agreement": agreement,
        "categorical_agree_count": sum(1 for v in agreement.values() if v),
        "categorical_field_count": len(agreement),
        "ordinal_delta_sol_minus_claude": deltas,
        # The decision-bearing one: do both judges agree the analysis hit the right band,
        # and do both agree on the false-positive direction?
        "decision_agrees": (
            agreement.get("band_matches_expectation") is True
            and agreement.get("false_positive_verdict") is True
        ),
    }


def summarize_agreement(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate agreement across many analyses.

    Reports the rate AND the n, and names the rows where the judges disagreed, because a
    disagreement is the interesting datum -- it marks a conclusion the evidence does not
    support.
    """
    if not pairs:
        return {"n": 0, "note": "no paired verdicts"}
    decided = [p for p in pairs if p.get("comparison")]
    agree = [p for p in decided if p["comparison"]["decision_agrees"]]
    disagree = [p for p in decided if not p["comparison"]["decision_agrees"]]
    calib_deltas = [
        p["comparison"]["ordinal_delta_sol_minus_claude"]["calibration_score"]
        for p in decided
        if p["comparison"]["ordinal_delta_sol_minus_claude"].get("calibration_score") is not None
    ]
    subst_deltas = [
        p["comparison"]["ordinal_delta_sol_minus_claude"]["substance_score"]
        for p in decided
        if p["comparison"]["ordinal_delta_sol_minus_claude"].get("substance_score") is not None
    ]
    return {
        "n": len(decided),
        "decision_agreement_count": len(agree),
        "decision_agreement_rate": (len(agree) / len(decided)) if decided else None,
        "disagreements": [
            {
                "app": p.get("app"),
                "domain": p.get("domain"),
                "model": p.get("model"),
                "claude": {
                    k: p["claude_verdict"].get(k)
                    for k in ("band_read_from_analysis", "band_matches_expectation", "false_positive_verdict")
                },
                "sol": {
                    k: p["sol_verdict"].get(k)
                    for k in ("band_read_from_analysis", "band_matches_expectation", "false_positive_verdict")
                },
            }
            for p in disagree
        ],
        "mean_calibration_delta_sol_minus_claude": (
            sum(calib_deltas) / len(calib_deltas) if calib_deltas else None
        ),
        "mean_substance_delta_sol_minus_claude": (
            sum(subst_deltas) / len(subst_deltas) if subst_deltas else None
        ),
        "interpretation": (
            "A positive mean delta means the GPT judge scores these analyses higher than the "
            "Claude judge does. Read it together with which SET the analyses came from: the "
            "two judges have opposite family biases, so a conclusion is only safe where both "
            "agree, or where the margin exceeds the biases measured in "
            "evals/results/tier_quality.json (Claude judge: +0.31 calibration, +0.78 "
            "substance for its own family)."
        ),
    }


__all__ = [
    "COMPARABLE_CATEGORICAL",
    "COMPARABLE_ORDINAL",
    "SOL_JUDGE_EFFORT",
    "SOL_JUDGE_MODEL",
    "SOL_JUDGE_REGION",
    "build_prompt",
    "compare_verdicts",
    "judge_analysis_sol",
    "summarize_agreement",
]
