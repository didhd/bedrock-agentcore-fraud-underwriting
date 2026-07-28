"""
Per-agent model selection for the fraud pipeline.

Snowflake Intelligence pins every Cortex Agent to one LLM class. Amazon Bedrock
lets each of the nine agents run on a right-sized model, so this module owns two
decisions and nothing else: which model id each agent gets, and how a
`BedrockModel` is constructed so an eight-way fan-out does not throttle itself.

Three things here are deliberate and load-bearing:

1. **The `global.*` inference profile is the default.** `us.*` and `global.*`
   address the same weights but `us.*` bills at exactly ``REGIONAL_MULTIPLIER``
   (1.10x) on every rate category. The prefix is env-overridable because a
   customer with a data-residency requirement needs `us.*`; the multiplier lives
   next to it so `agents.pricing` can charge the right rate either way instead of
   silently under-reporting by 9.09%.

2. **Tier assignment is justified by MEASUREMENT, or it is labelled unproven.**
   This is a correction. The first version of this table was authored by reading
   the customer's prompts -- "fraud rings needs multi-signal network reasoning, so
   give it the heavyweight model" -- and on both agents that reading placed on
   HEAVY it was wrong. Re-derived 2026-07-28 from ``evals/results/tier_quality.json``
   (48 blind-judged analyses scored against each agent's own verbatim CALIBRATION
   text and the fixture's pinned band) and
   ``evals/results/pipeline_14x3_us-east-1.json`` / ``tail_analysis.json``
   (critical-path ownership across 42 live pipeline runs):

   * rings moved HEAVY -> MID. n=5 applications, paired so only the same payload is
     compared. Opus 5 and Sonnet 5 reached the SAME band 4/5 with the SAME single
     judge-confirmed false positive. Opus's only edge is +0.20 judge calibration,
     which is smaller than the judge's own measured +0.31 self-preference for its
     family, so it is not distinguishable from bias. Opus costs $0.0696/call against
     $0.0241 and runs 32.81s p50 against 22.05s, and its 1.76x characters carry only
     1.71x the distinct evidence for a substance delta of +0 -- padding, not accuracy.
   * synthetic moved HEAVY -> MID, but on QUALITY, not on latency. Sonnet 5 beat Opus
     on band agreement (5/5 vs 4/5), calibration (4.80 vs 4.20) and cost ($0.0194 vs
     $0.0293) at n=5; the margin runs AGAINST the judge's own family so
     self-preference cannot explain it, and Opus produced the sample's only FALSE
     NEGATIVE (APP-1014, declared LOW against a pinned POSSIBLE). Sonnet is *slower*
     here -- 13.54s vs 8.51s p50, slower on 4 of 5 paired applications -- and the
     move is made anyway, because a missed synthetic identity costs more than 5s.
   * bustout and identity were swept and HELD at MID, because n=3 per cell cannot
     support a band claim: one application moves the rate 33 points. Haiku lost on no
     axis in either and is ~3x cheaper, and that is a reason to extend the sweep, not
     to move a fraud dimension.
   * dealer and straw keep LIGHT: the one part of the original prompt reading that
     measurement corroborates (4.4s / 6.6s p50, cheapest two agents, critical path in
     0 of 42 runs).
   * employment and income were NEVER swept. They sit at MID with no quality evidence
     either way and their rationales say exactly that.

   Every ``TIER_RATIONALE`` entry cites the measurement, its n, and where the evidence
   runs out. Where the judge (Opus 5) scored its own family, no conclusion rests on a
   margin below its measured +0.31 calibration / +0.78 substance self-preference.

   Tiering is *not* claimed to save money by its shape. Haiku->Sonnet is a 2.00/10.00
   per-MTok step down and Sonnet->Opus is the identical step up, so at equal token
   counts one LIGHT agent funds exactly one HEAVY agent. With no HEAVY agent left the
   shape reduces to "two cheap agents, six standard", and what remains is simply not
   paying Opus rates for output that measured no better.
   ``agents.pricing.compare_tiered_vs_uniform`` decomposes any reported saving into
   ``structural_saving_usd`` and ``model_saving_usd`` from real usage. The one
   projection this module states -- ``projected_cost_per_adjudication`` -- is computed
   from recorded per-agent token counts and labels itself a PROJECTION, because the
   full pipeline has not been re-run under this assignment.

3. **There is no latency table in this module.** A previous revision carried
   ``NOMINAL_LATENCY_S = {haiku: 0.9, sonnet: 2.5, opus: 6.0}`` and derived a
   customer-facing "26s sequential -> 8.5s parallel" headline from it. Those
   figures were invented and measurement contradicts their ordering (Sonnet 4.6
   is slower than Opus 4.8 on a real fraud prompt). Latency a customer sees comes
   from one place - the benchmark harness, which records
   ``result.metrics.accumulated_metrics["latencyMs"]`` and wall clock from live
   invocations. Nothing in this module may be used to predict latency.

   The one measured latency table in the codebase,
   ``agents.mantle.MEASURED_SYNTHESIS_LATENCY``, is a different kind of object: it
   is a routing INPUT (which region answers the synthesizer fastest), every cell
   carries its own n / date / prompt size, and it publishes nothing about an
   application's processing time. The synthesizer region below is chosen from it.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Final

# ---------------------------------------------------------------------------
# Inference profile prefix
# ---------------------------------------------------------------------------

#: `us.*` cross-region inference costs exactly this multiple of `global.*` on
#: every rate category (input, output, cache read, cache write). Verified against
#: the published Bedrock rate card; `agents.pricing` applies it.
REGIONAL_MULTIPLIER: Final[float] = 1.10

GLOBAL_PREFIX: Final[str] = "global."
US_PREFIX: Final[str] = "us."
SUPPORTED_PREFIXES: Final[tuple[str, ...]] = (GLOBAL_PREFIX, US_PREFIX)


def _resolve_prefix() -> str:
    """Read BEDROCK_MODEL_PREFIX, defaulting to the cheaper `global.` profile.

    Raises loudly on an unknown value: a silently-accepted typo would route every
    agent to a model id that does not exist and cost math that cannot be trusted.
    """
    raw = os.environ.get("BEDROCK_MODEL_PREFIX", GLOBAL_PREFIX)
    prefix = raw if raw.endswith(".") else f"{raw}."
    if prefix not in SUPPORTED_PREFIXES:
        raise ValueError(
            f"BEDROCK_MODEL_PREFIX={raw!r} is not supported. "
            f"Use one of {', '.join(repr(p) for p in SUPPORTED_PREFIXES)}."
        )
    return prefix


MODEL_PREFIX: Final[str] = _resolve_prefix()

# ---------------------------------------------------------------------------
# Model ids
#
# Base ids carry no inference-profile prefix. Every one is live-invokable in
# us-east-1 and us-west-2. Current generation is 5.x; the 4.x ids are kept
# because Sonnet 4.6 is the source system's model and therefore the honest
# cost baseline, and because Opus 4.8 remains a valid HEAVY alternative.
# ---------------------------------------------------------------------------

BASE_HAIKU_4_5: Final[str] = "anthropic.claude-haiku-4-5-20251001-v1:0"
BASE_SONNET_5: Final[str] = "anthropic.claude-sonnet-5"
BASE_SONNET_4_6: Final[str] = "anthropic.claude-sonnet-4-6"
BASE_OPUS_5: Final[str] = "anthropic.claude-opus-5"
BASE_OPUS_4_8: Final[str] = "anthropic.claude-opus-4-8"
BASE_OPUS_4_7: Final[str] = "anthropic.claude-opus-4-7"

BASE_MODEL_IDS: Final[tuple[str, ...]] = (
    BASE_HAIKU_4_5,
    BASE_SONNET_5,
    BASE_SONNET_4_6,
    BASE_OPUS_5,
    BASE_OPUS_4_8,
    BASE_OPUS_4_7,
)


def with_prefix(base_model_id: str, prefix: str | None = None) -> str:
    """Attach an inference-profile prefix to a base model id."""
    return f"{prefix or MODEL_PREFIX}{base_model_id}"


def prefix_of(model_id: str) -> str:
    """Return the inference-profile prefix carried by ``model_id`` ('' if none).

    Cost math keys off this rather than off ``MODEL_PREFIX`` so a model id built
    with an explicit prefix, or read back from a live response, is still priced
    correctly.
    """
    for candidate in SUPPORTED_PREFIXES:
        if model_id.startswith(candidate):
            return candidate
    return ""


def base_model_id(model_id: str) -> str:
    """Strip any inference-profile prefix from ``model_id``."""
    return model_id[len(prefix_of(model_id)) :]


# Tier defaults. LIGHT/MID/HEAVY are env-overridable with a full model id
# (prefix included) for operators who need to pin something else.
LIGHT_MODEL: Final[str] = os.environ.get("BEDROCK_MODEL_LIGHT", with_prefix(BASE_HAIKU_4_5))
MID_MODEL: Final[str] = os.environ.get("BEDROCK_MODEL_MID", with_prefix(BASE_SONNET_5))
HEAVY_MODEL: Final[str] = os.environ.get("BEDROCK_MODEL_HEAVY", with_prefix(BASE_OPUS_5))

#: The source system runs Claude Sonnet 4.6 across all nine agents. Any
#: "tiering saves money" claim must be measured against this, not against our
#: own MID tier.
BASELINE_MODEL: Final[str] = os.environ.get("BEDROCK_MODEL_BASELINE", with_prefix(BASE_SONNET_4_6))

TIER_OF: Final[dict[str, str]] = {
    LIGHT_MODEL: "light",
    MID_MODEL: "mid",
    HEAVY_MODEL: "heavy",
}

# ---------------------------------------------------------------------------
# Per-agent assignment
# ---------------------------------------------------------------------------

DOMAINS: Final[tuple[str, ...]] = (
    "identity",
    "dealer",
    "straw",
    "employment",
    "income",
    "synthetic",
    "bustout",
    "rings",
)

#: Per-agent model assignment, RE-DERIVED FROM MEASUREMENT on 2026-07-28.
#:
#: The first version of this table was authored by reading the customer's prompts:
#: "fraud rings needs multi-signal network reasoning, so give it the heavyweight model."
#: Measurement contradicted that on both agents it placed on HEAVY, and the correction is
#: the point of the table. Every entry's evidence is in ``TIER_RATIONALE`` below with its
#: n; nothing here is a reading of a prompt sentence.
#:
#: Evidence: ``evals/results/tier_quality.json`` (48 blind-judged analyses, judge =
#: Opus 5, scored against each agent's own verbatim CALIBRATION text and the fixture's
#: pinned band) and ``evals/results/pipeline_14x3_us-east-1.json`` +
#: ``tail_analysis.json`` (42 live pipeline runs, 336 specialist calls).
#:
#:   rings, HEAVY -> MID (the only change that moves the pipeline median). n=5.
#:   Same band 4/5, same single false positive; Opus is $0.0696 vs $0.0241 and 32.81s vs
#:   22.05s p50, and its 1.76x characters carry 1.71x the evidence for substance +0.
#:   rings owned the critical path in 25 of 42 runs and the fan-out floor is 96.3% of
#:   fan-out, so nothing else in this table is worth as much wall clock.
#:
#:   synthetic, HEAVY -> MID on QUALITY, accepting a LATENCY LOSS. n=5. Sonnet 5 bands
#:   5/5 vs Opus's 4/5 and calibrates 4.80 vs 4.20 at $0.0194 vs $0.0293 -- but is
#:   SLOWER, 13.54s vs 8.51s p50 and slower on 4 of 5 paired applications. The move is
#:   made because Opus produced this sweep's only false NEGATIVE (APP-1014 LOW against a
#:   pinned POSSIBLE). This is the row that proves the table is not cost-driven: a
#:   cost-driven table would have taken Haiku here (5/5, $0.0059, 9.56s).
#:
#:   bustout, identity: HELD at MID at n=3, which cannot support a band claim.
#:
#:   dealer, straw: unchanged at LIGHT -- corroborated, not merely inherited.
#:
#:   employment, income: unchanged at MID and explicitly UNPROVEN. Never swept.
#:
#: NOTE ON WHAT THIS DOES NOT FIX: the tail. 61.0% of end-to-end variance is
#: within-application (identical input, different draw), and the two 60s+ runs were
#: single calls generating at 16.1 and 4.7 output tok/s against a 66 tok/s median, inside
#: Bedrock's own reported latency with zero throttles. No assignment here removes that;
#: see ``evals/results/tail_analysis.md``.
#:
#: NOTE ON WHAT MEASUREMENT DID NOT FIX EITHER, and what the customer will ask about
#: first: on APP-1014 rings all three models declared POSSIBLE against a pinned LOW, and
#: on APP-1004 bustout all three over-escalated to HIGH against a pinned POSSIBLE, with
#: the judge naming the same fault each time -- alerts arising from ONE behaviour treated
#: as independent corroboration. That is an anti-false-positive failure against the
#: customer's stated calibration bar and it is a prompt/guardrail problem, not a
#: model-selection one. Buying a bigger model does not fix it.
SPECIALIST_MODELS: Final[dict[str, str]] = {
    "identity": MID_MODEL,
    "dealer": LIGHT_MODEL,
    "straw": LIGHT_MODEL,
    "employment": MID_MODEL,
    "income": MID_MODEL,
    "synthetic": MID_MODEL,
    "bustout": MID_MODEL,
    "rings": MID_MODEL,
}

#: GPT-5.6 Luna on the bedrock-mantle endpoint. See agents/mantle.py.
#:
#: This is the one model choice in the repo made on a MEASUREMENT rather than on a
#: reading of the customer's prompt. The synthesizer is the pipeline's bottleneck: it
#: consumes all eight specialist narratives (~28K input tokens) and emits the 21-key
#: object. Measured on APP-1004 with the same eight analyses:
#:
#:     Claude Sonnet 5   55.2s   $0.11731   21 keys, HIGH RISK / DECLINE
#:     GPT-5.6 Luna       6.1s   $0.01488   21 keys, HIGH RISK / DECLINE
#:
#: ~9x faster for an identical verdict, and the difference between a 101s end-to-end
#: and a 37.5s one -- i.e. the difference between missing and meeting the customer's
#: sub-minute target. The eight specialists deliberately stay on Claude: they ARE the
#: fraud analysis, and no accuracy comparison justifies moving them.
#:
#: WHICH VARIANT, AND WHICH REGION, ARE BOTH MEASURED CHOICES.
#:
#: Luna is the cheapest of the three GPT-5.6 variants AND the fastest on this prompt,
#: which is not the ordering anyone expects. Measured on the real 31,343-char synthesis
#: prompt, n=3 per cell, effort=low, all 24 calls valid and unanimous on HIGH RISK /
#: DECLINE (``agents.mantle.MEASURED_SYNTHESIS_LATENCY``):
#:
#:     luna  us-east-1   4.61s p50     $1.10/$6.60 per 1M
#:     terra us-east-2   6.29s p50     $2.75/$16.50 per 1M
#:     sol   us-east-1  11.19s p50     $5.50/$33.00 per 1M
#:
#: So paying 5x more per token buys 2.4x MORE latency here. There is no
#: speed-for-money trade to make, and the cheap variant is not the compromise choice.
#:
#: The REGION is picked by ``agents.mantle.region_choice`` from that same table and
#: deliberately NOT from ``AWS_REGION`` -- ``resolve_region`` below governs the Converse
#: specialists and has no bearing on a mantle call, which is in-region only with no
#: cross-region inference profile. With ``AWS_REGION=us-west-2`` the previous revision
#: sent every synthesis to us-west-2 (5.02s p50) instead of us-east-1 (4.61s p50), an
#: 8.9% latency tax on the pipeline's bottleneck for no benefit. Override with
#: BEDROCK_MANTLE_REGION.
#:
#: What is NOT claimed: that Luna reasons as well as Sonnet 5 on this task. One
#: application agreed, and the 24 matrix calls agreed with each other -- agreement
#: across regions is a determinism observation, not an accuracy one. Before this ships
#: to the customer, run the calibration judge in evals/ across the fixture set on both.
#:
#: Also NOT claimed: that this variant ranking holds for any other prompt. It is a
#: property of a 31K-char prompt with an ~800-1000 token reply; a short-prompt probe
#: ranks the regions differently. See agents/mantle.py and docs/model-selection.md.
#:
#: Set BEDROCK_MODEL_SYNTHESIZER to a Claude id to go back to the Converse path; the
#: routing in agents/synthesize.py follows the id, not a flag.
DEFAULT_SYNTHESIZER_MODEL: Final[str] = "openai.gpt-5.6-luna"
SYNTHESIZER_MODEL: Final[str] = os.environ.get(
    "BEDROCK_MODEL_SYNTHESIZER", DEFAULT_SYNTHESIZER_MODEL
)

ROLES: Final[tuple[str, ...]] = DOMAINS + ("synthesizer",)

#: Why each agent sits where it sits, CITING THE MEASUREMENT rather than a prompt
#: sentence. Re-derived 2026-07-28.
#:
#: Read the three prefixes literally, because they encode how much the entry is worth:
#:
#:   "<TIER> - justified by MEASUREMENT"  a quality comparison was run on this agent and
#:       it decided the assignment. Latency, cost, quality verdict and n are all quoted.
#:   "<TIER> - HELD"                      a comparison was run and DELIBERATELY not acted
#:       on, because n is too small to move a fraud dimension. The cheaper candidate and
#:       what it would save are recorded so the next sweep starts from evidence.
#:   "<TIER> - UNPROVEN"                  no quality comparison exists for this agent at
#:       all. The entry says so instead of borrowing another agent's result.
#:
#: Sources, both on disk and both re-derivable:
#:   evals/results/tier_quality.json  - 48 blind-judged analyses (judge Opus 5, model
#:       identifiers stripped, order shuffled under seed 20260728), scored against each
#:       agent's own verbatim CALIBRATION text and the fixture's pinned band.
#:   evals/results/pipeline_14x3_us-east-1.json - 42 live pipeline runs / 336 specialist
#:       calls, source of every p50 and $/call quoted at n=42.
#:
#: THE JUDGE GRADES ITS OWN FAMILY. Opus 5 is both judge and candidate, and on its own 16
#: rows it scored calibration +0.31 and substance +0.78 versus the other 32 while the
#: band-agreement margin it cannot influence moved -0.026. No entry below rests on an
#: Opus-versus-cheaper margin smaller than that.
TIER_RATIONALE: Final[dict[str, str]] = {
    "straw": (
        "LIGHT - justified by MEASUREMENT. n=42 calls, evals/results/pipeline_14x3_us-east-1.json: "
        "the fastest and cheapest agent in the pipeline at 4.40s p50 / 8.76s p95 and $0.00352 per "
        "call on Haiku 4.5, 224 output tokens mean, and it was the slowest agent in 0 of 42 runs - "
        "so it can never set the fan-out floor. NOT quality-swept: no judge comparison against a "
        "larger model exists for straw, and the case for LIGHT here is latency/cost plus the "
        "prompt's own scope ('If no co-borrower exists, straw risk is inherently minimal - keep "
        "analysis to 2-3 sentences'), which measurement corroborates at 672 chars p50. Moving it UP "
        "would need a quality argument nobody has measured."
    ),
    "dealer": (
        "LIGHT - justified by MEASUREMENT. n=42 calls, evals/results/pipeline_14x3_us-east-1.json: "
        "6.56s p50 / 14.56s p95 and $0.00541 per call on Haiku 4.5, 379 output tokens mean, slowest "
        "agent in 0 of 42 runs. Second cheapest and second fastest of the eight. NOT quality-swept "
        "either - like straw, dealer never went through the judge, so LIGHT rests on latency, cost "
        "and the prompt's banded-lookup scope ('Do NOT escalate fraud risk classification based "
        "solely on dealer default rates or consortium scores'), not on a measured quality parity."
    ),
    "rings": (
        "MID - justified by MEASUREMENT, moved down from HEAVY (Opus 5) on 2026-07-28, and it is "
        "the single largest median-latency lever in the pipeline. n=5 applications per cell, "
        "evals/results/tier_quality.json, paired so only same-payload analyses are compared. "
        "QUALITY: Opus 5 and Sonnet 5 reached the SAME band 4/5 against the pinned expectation with "
        "the SAME one judge-confirmed false positive. Opus's only remaining edge is +0.20 judge "
        "calibration (4.20 vs 4.00), which is SMALLER than the judge's own measured +0.31 "
        "self-preference for its family and is therefore not distinguishable from bias. Its 1.76x "
        "characters carry 1.71x the distinct evidence for a substance delta of +0 - length, not "
        "accuracy. COST: $0.0696 vs $0.0241 per call p50 (2.9x). LATENCY: 32.81s vs 22.05s p50, "
        "faster on 3 of 5 paired applications. Why it matters more here than anywhere else: rings "
        "was the slowest agent in 25 of 42 pipeline runs and the fan-out floor is 96.3% of fan-out "
        "(evals/results/tail_analysis.json), so fan-out cannot finish before it does. Ceiling on "
        "the fix, also measured: slowest-minus-second-slowest is 6.34s p50, so even an instant "
        "rings leaves the next agent as the floor. NOT FIXED by this move: on APP-1014 all three "
        "models declared POSSIBLE against a pinned LOW RISK (RNG-020), all three judged "
        "FalsePositive - a prompt/guardrail failure no tier buys out."
    ),
    "synthetic": (
        "MID - justified by MEASUREMENT on QUALITY, moved down from HEAVY (Opus 5) on 2026-07-28, "
        "and this row deliberately ACCEPTS A LATENCY LOSS. n=5 applications, "
        "evals/results/tier_quality.json. QUALITY: Sonnet 5 bands 5/5 against Opus's 4/5 and "
        "calibrates 4.80 against 4.20, with 0 judge-confirmed false positives on both. Opus "
        "produced the sweep's only FALSE NEGATIVE - on APP-1014 it declared LOW RISK against a "
        "pinned POSSIBLE RISK (SYN-008) and the judge scored it 'dismisses' - a false NEGATIVE, "
        "which is the more dangerous error direction for a synthetic-identity dimension. This is "
        "the trade-off stated plainly: ~5s of extra median latency bought a fraud dimension that "
        "the faster, more expensive model missed, and on a fraud product that is the right side to "
        "err on. The margin runs AGAINST the "
        "judge's own family, so its +0.31 self-preference cannot explain it - this is the one cell "
        "where the cheaper model wins with no bias caveat available. COST: $0.0194 vs $0.0293 p50. "
        "LATENCY: Sonnet is SLOWER - 13.54s vs 8.51s p50, slower on 4 of 5 paired applications - "
        "and the move is made anyway, because a missed synthetic identity costs more than 5s. That "
        "is also why Haiku was NOT taken here despite banding 5/5 at $0.0059 and 9.56s: it carried "
        "8 output-contract violations against Sonnet's 5, including citing numeric alert ids on 3 "
        "of 5 calls, which synthetic_agent_prompt.txt forbids."
    ),
    "identity": (
        "MID - HELD at Sonnet 5 after a measured trial too small to act on. n=3 applications, "
        "evals/results/tier_quality.json, and a band claim needs 5: one application moves the rate "
        "33 points. On that thin sample Haiku 4.5 lost on no band (3/3 vs Sonnet's 2/2 readable) at "
        "$0.0051 vs $0.0171 and 8.04s vs 13.65s, but scored 1.00 LOWER on judge calibration (4.00 "
        "vs 5.00) and committed 7 output-contract violations against Sonnet's 2 - including citing "
        "numeric alert ids on 2 of 3 calls, which identity_agent_prompt.txt forbids ('Reference "
        "specific findings from alerts, not alert numbers'), and putting a risk label in a section "
        "header on all 3. Cheaper and faster, measurably sloppier on her own rules. At n=42 in the "
        "pipeline benchmark identity is 12.56s p50 / 33.40s p95 at $0.01727 and was the slowest "
        "agent in 4 of 42 runs, so it is not a critical-path priority. Extend the sweep to 5+ "
        "applications before moving it."
    ),
    "bustout": (
        "MID - HELD at Sonnet 5, and the evidence is deliberately NOT acted on. n=3 applications, "
        "evals/results/tier_quality.json; a band claim needs 5. On that sample Haiku 4.5 did not "
        "lose - band 2/3 against Sonnet's 1/3, identical calibration mean 2.67, one judge-confirmed "
        "false positive against Sonnet's two - at $0.0087 vs $0.0254 and 13.65s vs 25.31s p50. That "
        "is a reason to extend the sweep, not to move a fraud dimension on 3 applications. Worth "
        "resolving, because at n=42 bustout is 14.46s p50 / 35.71s p95 at $0.01699 and was the "
        "slowest agent in 7 of 42 runs - second only to rings - and it is one of the two prompts "
        "with NO character cap, writing 1097 output tokens p50 against 288-394 for the capped "
        "agents. NOT FIXED by any tier: on APP-1004 all three models over-escalated to HIGH RISK "
        "against a pinned POSSIBLE RISK (BST-020)."
    ),
    "income": (
        "MID - UNPROVEN. income was never quality-swept: no judge comparison against Haiku or Opus "
        "exists for this agent, so there is no evidence for or against a move and none is invented "
        "here. What IS measured, n=42 calls, evals/results/pipeline_14x3_us-east-1.json: 8.09s p50 "
        "but 31.44s p95 at $0.01537 per call on Sonnet 5, slowest agent in 5 of 42 runs, and it "
        "owned one of the two 60s+ tail runs (APP-1001 rep2, 66.0s of a 66.5s fan-out, generating "
        "at 4.7 output tok/s against a 66 tok/s median - server-side, not verbosity). MID is the "
        "conservative hold; the next sweep should include it precisely because its p95/p50 spread "
        "is the second widest of the eight."
    ),
    "employment": (
        "MID - UNPROVEN. employment was never quality-swept; no judge comparison exists, so the "
        "assignment carries no quality evidence and does not pretend to. What IS measured, n=42 "
        "calls, evals/results/pipeline_14x3_us-east-1.json: 7.48s p50 / 17.64s p95 at $0.01022 per "
        "call on Sonnet 5, 527 output tokens mean, and it was the slowest agent in 0 of 42 runs. "
        "It is the cheapest and fastest of the six MID agents and a plausible LIGHT candidate on "
        "cost, which is exactly why it needs a judged sweep rather than a guess: a LIGHT move here "
        "would save ~$0.005/app and cannot be justified without a band comparison."
    ),
    "synthesizer": (
        "CUSTOM (GPT-5.6 Luna on bedrock-mantle) - justified by MEASUREMENT, and it is NOT a Claude "
        "tier, so LIGHT/MID/HEAVY do not apply. LATENCY, n=42 live runs, "
        "evals/results/pipeline_14x3_us-east-1.json: 4.20s p50 / 5.59s p95 at "
        "$0.01064 per call, 716 output tokens mean, 0 failures and 0 repair attempts across 42 "
        "adjudications - 14.4% of end-to-end against fan-out's 85.6%. The substitution was decided "
        "on a same-inputs comparison on APP-1004 (n=1 each): Claude Sonnet 5 55.2s / $0.11731 "
        "against Luna 6.1s / $0.01488 for an IDENTICAL 21-key HIGH RISK / DECLINE verdict. Variant "
        "and region are also measured, n=3 per cell on the real 31,343-char prompt: luna us-east-1 "
        "4.61s p50 beats terra 6.29s and sol 11.19s while costing 5x less - see "
        "agents.mantle.MEASURED_SYNTHESIS_LATENCY. WHAT IS UNPROVEN: that Luna's fraud REASONING "
        "matches Sonnet 5's. The 42 runs validated 21 keys every time and the verdict-stability "
        "check found 12 of 14 applications stable, but no blind judge has scored Luna against a "
        "Claude synthesizer across the fixture set. That eval is the gap; BEDROCK_MODEL_SYNTHESIZER "
        "switches back to any Claude id and agents/synthesize.py routes on the id, not a flag."
    ),
}


def model_for(role: str) -> str:
    """Return the configured model id for a domain or for 'synthesizer'."""
    if role == "synthesizer":
        return SYNTHESIZER_MODEL
    return SPECIALIST_MODELS.get(role, MID_MODEL)


def tier_label(model_id: str) -> str:
    """Tier name for a model id ('light' / 'mid' / 'heavy' / 'custom')."""
    return TIER_OF.get(model_id, "custom")


def rationale_for(role: str) -> str:
    """Return the documented tier rationale for a role."""
    return TIER_RATIONALE.get(role, "no rationale recorded")


#: The three evidence grades a ``TIER_RATIONALE`` entry may carry, most to least
#: load-bearing. ``evidence_grade`` reads the entry rather than re-deciding, so the
#: rendered table and the rationale text can never disagree about what was measured.
EVIDENCE_GRADES: Final[tuple[str, ...]] = ("MEASUREMENT", "HELD", "UNPROVEN")

#: Which agents went through the BLIND QUALITY JUDGE, and at what n. This is a narrower
#: and more expensive claim than ``evidence_grade``: an agent can be graded MEASUREMENT on
#: latency and cost (dealer and straw are, at n=42 calls) while never having had its
#: analysis QUALITY compared against another model. Conflating the two is how a cost win
#: gets presented as a quality result, so the distinction is recorded rather than implied.
#: Source: ``evals/results/tier_quality.json``.
QUALITY_SWEPT_AGENTS: Final[dict[str, int]] = {
    "rings": 5,
    "synthetic": 5,
    "bustout": 3,
    "identity": 3,
}

#: The minimum applications per cell that supports a band-agreement claim, from the eval's
#: own percentile policy. bustout and identity were swept below it and are therefore HELD.
MIN_APPLICATIONS_FOR_A_BAND_CLAIM: Final[int] = 5


def evidence_grade(role: str) -> str:
    """How strong the evidence behind ``role``'s assignment is.

    ``MEASUREMENT`` - a measurement on this agent decided the assignment. ``HELD`` - a
    comparison was run and deliberately NOT acted on because n was too small to move a
    fraud dimension. ``UNPROVEN`` - no comparison discriminating models exists for it.

    This grades the ASSIGNMENT, not the analysis quality: check
    :data:`QUALITY_SWEPT_AGENTS` for whether a blind judge ever scored the agent's output.
    dealer and straw are graded ``MEASUREMENT`` on n=42 latency and cost and appear in
    neither.

    Returns ``UNRECORDED`` for a rationale that states no grade, which is a defect rather
    than a fourth category: an assignment with no evidence statement is the thing this
    module exists to prevent.
    """
    rationale = rationale_for(role)
    for grade in EVIDENCE_GRADES:
        if grade in rationale:
            return grade
    return "UNRECORDED"


def model_assignment_table() -> str:
    """Render the per-agent model configuration for display.

    Deliberately carries no latency or cost column: neither can be known from
    configuration alone. The ``evidence`` column reports the GRADE of the rationale,
    not a yes/no - "we measured it", "we measured it and held", and "we never
    measured it" are three different things and a boolean hides the third.
    """
    rows = [
        "Per-agent model configuration:",
        f"  {'agent':<13}{'tier':<7}{'model id':<52}{'max_tok':<9}evidence",
    ]
    for role in ROLES:
        mid = model_for(role)
        rows.append(
            f"  {role:<13}{tier_label(mid):<7}{mid:<52}"
            f"{max_tokens_for(role):<9}{evidence_grade(role)}"
        )
    rows.append(f"  baseline (source system, all agents): {BASELINE_MODEL}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Cost PROJECTION from recorded per-agent tokens
#
# WHY THIS IS A PROJECTION AND SAYS SO IN EVERY RETURN VALUE. The 42-run benchmark
# measured $0.14396 per adjudication p50 / $0.15616 mean UNDER THE OLD ASSIGNMENT, with
# rings and synthetic on Opus 5. The assignment above moves both to Sonnet 5 and the full
# pipeline has NOT been re-run since, so there is no measured end-to-end figure for the
# current configuration and this module must not invent one.
#
# The one thing that cannot be done here is holding token counts constant across a model
# swap: a different model writes a different amount, so pricing Opus's token counts at
# Sonnet's rates is fiction. On rings, measured: Opus wrote 2096.8 output tokens mean and
# Sonnet 1862.6 on the SAME five applications. So every row below is priced at the token
# counts that THAT model actually produced, and each row carries its own n and source.
# ---------------------------------------------------------------------------

#: Recorded mean per-call token usage for each role, at the model currently assigned to
#: it. Every row is measured; none is modelled. The two sources differ in sample size and
#: in which applications they cover, and that asymmetry is the projection's main
#: limitation, so it is recorded per row rather than averaged away:
#:
#:   n=42, all 14 fixtures x 3 repetitions, evals/results/pipeline_14x3_us-east-1.json -
#:     the six agents whose model did NOT change, plus the synthesizer.
#:   n=5, APP-1004/1009/1012/1013/1014, evals/results/tier_quality_analyses.json - rings
#:     and synthetic ON SONNET 5, i.e. the model now assigned. These five applications are
#:     the harder half of the fixture set: the same two agents' Opus rows cost 31.1% and
#:     26.6% MORE on these five than their 14-fixture mean did, so the projection for
#:     these two rows is if anything pessimistic.
#:
#: Cache columns are included because Bedrock's ``inputTokens`` excludes cached tokens;
#: dropping ``cacheReadInputTokens`` would under-report total input and mis-price the
#: 0.1x cache-read rate. Fractional means are kept verbatim from the reports rather than
#: rounded, so the arithmetic can be checked against them.
RECORDED_USAGE_PER_CALL: Final[dict[str, dict[str, Any]]] = {
    "identity": {
        "model_id": MID_MODEL,
        "usage": {"inputTokens": 3110.0, "outputTokens": 1105.2142857142858,
                  "cacheReadInputTokens": 0.0, "cacheWriteInputTokens": 0.0},
        "n": 42, "source": "evals/results/pipeline_14x3_us-east-1.json",
        "basis": "14 fixtures x 3 repetitions",
    },
    "dealer": {
        "model_id": LIGHT_MODEL,
        "usage": {"inputTokens": 3517.6428571428573, "outputTokens": 379.26190476190476,
                  "cacheReadInputTokens": 0.0, "cacheWriteInputTokens": 0.0},
        "n": 42, "source": "evals/results/pipeline_14x3_us-east-1.json",
        "basis": "14 fixtures x 3 repetitions",
    },
    "straw": {
        "model_id": LIGHT_MODEL,
        "usage": {"inputTokens": 2399.9285714285716, "outputTokens": 224.38095238095238,
                  "cacheReadInputTokens": 0.0, "cacheWriteInputTokens": 0.0},
        "n": 42, "source": "evals/results/pipeline_14x3_us-east-1.json",
        "basis": "14 fixtures x 3 repetitions",
    },
    "employment": {
        "model_id": MID_MODEL,
        "usage": {"inputTokens": 2363.1428571428573, "outputTokens": 527.3095238095239,
                  "cacheReadInputTokens": 1091.0, "cacheWriteInputTokens": 0.0},
        "n": 42, "source": "evals/results/pipeline_14x3_us-east-1.json",
        "basis": "14 fixtures x 3 repetitions",
    },
    "income": {
        "model_id": MID_MODEL,
        "usage": {"inputTokens": 3183.5714285714284, "outputTokens": 900.4285714285714,
                  "cacheReadInputTokens": 0.0, "cacheWriteInputTokens": 0.0},
        "n": 42, "source": "evals/results/pipeline_14x3_us-east-1.json",
        "basis": "14 fixtures x 3 repetitions",
    },
    "bustout": {
        "model_id": MID_MODEL,
        "usage": {"inputTokens": 1785.6428571428571, "outputTokens": 1316.4047619047619,
                  "cacheReadInputTokens": 1297.0, "cacheWriteInputTokens": 0.0},
        "n": 42, "source": "evals/results/pipeline_14x3_us-east-1.json",
        "basis": "14 fixtures x 3 repetitions",
    },
    # The two agents whose model CHANGED. Priced at Sonnet 5's own measured tokens, not
    # at the Opus tokens the 42-run benchmark recorded.
    "synthetic": {
        "model_id": MID_MODEL,
        "usage": {"inputTokens": 3793.4, "outputTokens": 1517.8,
                  "cacheReadInputTokens": 0.0, "cacheWriteInputTokens": 0.0},
        "n": 5, "source": "evals/results/tier_quality_analyses.json",
        "basis": "APP-1004/1009/1012/1013/1014 on the newly assigned Sonnet 5",
    },
    "rings": {
        "model_id": MID_MODEL,
        "usage": {"inputTokens": 1620.8, "outputTokens": 1862.6,
                  "cacheReadInputTokens": 705.0, "cacheWriteInputTokens": 235.0},
        "n": 5, "source": "evals/results/tier_quality_analyses.json",
        "basis": "APP-1004/1009/1012/1013/1014 on the newly assigned Sonnet 5",
    },
    "synthesizer": {
        "model_id": DEFAULT_SYNTHESIZER_MODEL,
        "usage": {"inputTokens": 2.0, "outputTokens": 715.8571428571429,
                  "cacheReadInputTokens": 0.0, "cacheWriteInputTokens": 4300.238095238095},
        "n": 42, "source": "evals/results/pipeline_14x3_us-east-1.json",
        "basis": "14 fixtures x 3 repetitions",
    },
}

#: The measured end-to-end cost per adjudication under the PREVIOUS assignment (rings and
#: synthetic on Opus 5), from the same 42-run benchmark. Kept so the projection can state
#: what it is a projection *against* instead of quoting a saving with no baseline. p50 is
#: the median of 41 priced run totals; the mean is over the same 41.
#:
#: NOTE THE DATE THIS WAS MEASURED ON. The run is 2026-07-28, i.e. inside Sonnet 5's
#: introductory window, so the six MID agents in it are priced at 2.00/10.00. Comparing a
#: post-2026-09-01 projection against this fixed number is NOT like-for-like -- it would
#: charge the new assignment the lapsed rate while crediting the old one the promo. That is
#: what ``RECORDED_USAGE_PER_CALL_PRIOR_ASSIGNMENT`` exists to prevent.
MEASURED_COST_PER_ADJUDICATION_PRIOR_ASSIGNMENT: Final[dict[str, Any]] = {
    "p50_usd": 0.14395847500000003,
    "p95_usd": 0.22710520000000003,
    "mean_usd": 0.15615689268292682,
    "n_priced_runs": 41,
    "n_runs": 42,
    "measured_as_of": "2026-07-28",
    "assignment": "rings=opus-5, synthetic=opus-5, six others as now",
    "source": "evals/results/pipeline_14x3_us-east-1.json",
}

#: The PRIOR assignment's own measured tokens, so both assignments can be repriced at the
#: same date and the comparison stays like-for-like when Sonnet 5's promo lapses. Only the
#: two rows that changed model are recorded; the other seven are identical to
#: :data:`RECORDED_USAGE_PER_CALL` and are reused rather than duplicated.
#:
#: These are Opus 5's OWN token counts from the 42-run benchmark (n=41 each: one run lost
#: both agents to an InternalServerException). Opus wrote 1568.8 output tokens mean on
#: rings against Sonnet's 1862.6, and 581.5 on synthetic against Sonnet's 1517.8 -- the two
#: models genuinely write different amounts, which is exactly why neither side of this
#: comparison may borrow the other's tokens.
RECORDED_USAGE_PER_CALL_PRIOR_ASSIGNMENT: Final[dict[str, dict[str, Any]]] = {
    "rings": {
        "model_id": HEAVY_MODEL,
        "usage": {"inputTokens": 1568.170731707317, "outputTokens": 1568.7804878048781,
                  "cacheReadInputTokens": 1175.0, "cacheWriteInputTokens": 0.0},
        "n": 41, "source": "evals/results/pipeline_14x3_us-east-1.json",
        "basis": "14 fixtures x 3 repetitions on the then-assigned Opus 5",
    },
    "synthetic": {
        "model_id": HEAVY_MODEL,
        "usage": {"inputTokens": 2730.341463414634, "outputTokens": 581.4634146341464,
                  "cacheReadInputTokens": 1001.0, "cacheWriteInputTokens": 0.0},
        "n": 41, "source": "evals/results/pipeline_14x3_us-east-1.json",
        "basis": "14 fixtures x 3 repetitions on the then-assigned Opus 5",
    },
}


def projected_cost_per_adjudication(*, as_of: Any | None = None) -> dict[str, Any]:
    """Project $/adjudication for the CURRENT assignment from recorded per-agent tokens.

    THIS IS A PROJECTION, NOT A MEASUREMENT, and every return value says so. It sums nine
    per-call costs, each priced from the token counts that model actually produced
    (:data:`RECORDED_USAGE_PER_CALL`), which is the only way a model swap can be costed
    honestly - holding tokens constant across a swap is fiction, and a measured
    end-to-end does not exist for this configuration because the pipeline has not been
    re-run since the assignment changed.

    Two things the sum is NOT:

    * It is not a p50 or a mean of run totals. A sum of per-agent means is the expected
      cost of one adjudication, which is comparable to the benchmark's MEAN
      ($0.15616), not to its p50 ($0.14396). Both are reported for comparison and
      labelled.
    * It carries no distribution. The benchmark's measured spread was $0.1177-$0.2536
      per run; nothing here predicts a p95.

    ``confirmed_by`` names the exact run that would turn this into a measurement.
    """
    from agents.pricing import cost_usd  # noqa: PLC0415  (pricing imports this module)

    per_role: dict[str, dict[str, Any]] = {}
    total = 0.0
    for role, record in RECORDED_USAGE_PER_CALL.items():
        model_id = record["model_id"]
        cost = cost_usd(model_id, record["usage"], as_of=as_of)
        total += cost
        per_role[role] = {
            "model_id": model_id,
            "tier": tier_label(model_id),
            "cost_usd": cost,
            "n": record["n"],
            "source": record["source"],
            "basis": record["basis"],
        }

    # The prior assignment repriced AT THE SAME DATE, so the delta isolates the model
    # change instead of mixing in Sonnet 5's promo expiry. Its rings/synthetic rows carry
    # Opus's own measured tokens; the other seven roles are unchanged and reused.
    prior_total = 0.0
    for role, record in RECORDED_USAGE_PER_CALL.items():
        prior_record = RECORDED_USAGE_PER_CALL_PRIOR_ASSIGNMENT.get(role, record)
        prior_total += cost_usd(prior_record["model_id"], prior_record["usage"], as_of=as_of)

    prior = MEASURED_COST_PER_ADJUDICATION_PRIOR_ASSIGNMENT
    specialists = total - per_role["synthesizer"]["cost_usd"]
    return {
        "kind": "PROJECTION",
        "projected_usd": total,
        "projected_specialists_usd": specialists,
        "projected_synthesizer_usd": per_role["synthesizer"]["cost_usd"],
        "per_role": per_role,
        "smallest_n": min(r["n"] for r in per_role.values()),
        "measured_prior_assignment": dict(prior),
        # Both sides priced at ``as_of``: this is the honest saving from the model change.
        "prior_assignment_projected_usd": prior_total,
        "delta_vs_prior_repriced_usd": total - prior_total,
        # Against the number actually measured on 2026-07-28. Only meaningful at a date
        # inside the introductory window, because that is when the benchmark ran.
        "delta_vs_prior_measured_mean_usd": total - prior["mean_usd"],
        "comparable_to": (
            "the benchmark's MEAN cost per adjudication ($0.15616, n=41 priced runs), "
            "because this is a sum of per-agent means - not to its p50 ($0.14396)"
        ),
        "not_a_measurement": (
            "Projected from recorded per-agent token counts, each priced at the model "
            "that produced them. The full pipeline has NOT been re-run under this "
            "assignment, so there is no measured end-to-end cost for it and no "
            "percentile of any kind."
        ),
        "confirmed_by": "AGENTCORE_BENCH_ALLOW_LIVE=1 python -m evals.bench --pipeline --repetitions 3",
    }


# ---------------------------------------------------------------------------
# Prompt caching: minimum cacheable prefix
# ---------------------------------------------------------------------------

#: Minimum cacheable prefix in tokens, measured per model by bisection. Below
#: this a `cachePoint` is accepted with no error, no warning, and
#: cacheWriteInputTokens == 0 - the caller pays full input price forever. The
#: table is NOT monotonic across generations, which is exactly the trap: Haiku
#: 4.5 needs 8x the prefix Opus 5 does.
MIN_CACHEABLE_PROMPT_TOKENS: Final[dict[str, int]] = {
    BASE_OPUS_5: 512,
    BASE_SONNET_5: 1024,
    BASE_SONNET_4_6: 1024,
    BASE_OPUS_4_8: 1024,
    BASE_HAIKU_4_5: 4096,
}

#: Coarse chars-per-token divisor used only by `estimate_prompt_tokens`.
#: This is an estimate for a pre-flight warning, never an input to cost.
_CHARS_PER_TOKEN_ESTIMATE: Final[int] = 4


def supports_prompt_caching(model_id: str) -> bool:
    """Whether Bedrock prompt caching engages for this model id at all.

    Mirrors the SDK's own gate (`'claude' in model_id or 'anthropic' in
    model_id`), so a non-Claude id silently drops cache points.
    """
    lowered = model_id.lower()
    return "claude" in lowered or "anthropic" in lowered


def min_cacheable_tokens(model_id: str) -> int | None:
    """Minimum cacheable prefix for ``model_id``, or None if unmeasured."""
    return MIN_CACHEABLE_PROMPT_TOKENS.get(base_model_id(model_id))


def estimate_prompt_tokens(text: str) -> int:
    """Rough token count for a prompt string (~4 chars/token).

    An estimate, and labelled as one everywhere it surfaces. Use a real
    ``inputTokens`` from a response, or Bedrock's own count, for anything a
    customer sees.
    """
    return len(text) // _CHARS_PER_TOKEN_ESTIMATE


def will_cache(model_id: str, prompt_tokens: int) -> bool:
    """Whether a prefix of ``prompt_tokens`` actually caches on ``model_id``.

    Takes a token count, not a string, so the answer is not contaminated by the
    char/4 estimate. Returns False for an unmeasured model - refusing to promise
    a cache we have not verified.
    """
    if not supports_prompt_caching(model_id):
        return False
    minimum = min_cacheable_tokens(model_id)
    if minimum is None:
        return False
    return prompt_tokens >= minimum


def cache_prefix_report(model_id: str, prompt: str, prompt_tokens: int | None = None) -> dict[str, Any]:
    """Pre-flight report on whether ``prompt`` will cache on ``model_id``.

    Pass ``prompt_tokens`` when a real count is available; otherwise the report
    is driven by ``estimate_prompt_tokens`` and marks itself ``estimated``.
    """
    estimated = prompt_tokens is None
    tokens = estimate_prompt_tokens(prompt) if estimated else int(prompt_tokens)
    minimum = min_cacheable_tokens(model_id)
    caches = will_cache(model_id, tokens)
    if not supports_prompt_caching(model_id):
        reason = "model id is not a Claude/Anthropic id; cache points are dropped"
    elif minimum is None:
        reason = "minimum cacheable prefix not measured for this model id"
    elif caches:
        reason = f"prefix {tokens} >= minimum {minimum}"
    else:
        reason = (
            f"prefix {tokens} < minimum {minimum}: cachePoint is accepted silently, "
            "cacheWriteInputTokens will be 0 and every call pays full input price"
        )
    return {
        "model_id": model_id,
        "prompt_tokens": tokens,
        "prompt_tokens_estimated": estimated,
        "min_cacheable_tokens": minimum,
        "will_cache": caches,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# BedrockModel construction
# ---------------------------------------------------------------------------

#: An eight-way fan-out needs more than botocore's 10-connection default, which
#: is what `BedrockModel` uses when no client config is supplied.
MAX_POOL_CONNECTIONS: Final[int] = 32

#: Supplying `boto_client_config` makes the SDK skip its own
#: `read_timeout=120`, silently falling back to botocore's 60s. Set explicitly.
READ_TIMEOUT_S: Final[int] = 120
CONNECT_TIMEOUT_S: Final[int] = 10

#: The SDK default is (max_attempts=6, initial_delay=4, max_delay=240), i.e.
#: backoff [4, 8, 16, 32, 64, 128]s. One throttle costs 4s against a sub-minute
#: SLA. These values keep total added latency under ~15s.
RETRY_MAX_ATTEMPTS: Final[int] = 4
RETRY_INITIAL_DELAY_S: Final[int] = 1
RETRY_MAX_DELAY_S: Final[int] = 8

# ---------------------------------------------------------------------------
# Output caps
#
# READ THIS BEFORE TIGHTENING ANYTHING HERE. ``max_tokens`` WAS MEASURED AS A BREVITY
# INSTRUMENT AND IT FAILED ON ALL THREE CRITERIA. 30 live calls in us-east-1 across
# bustout and rings at caps 1024/1536/2048/3072/4096 with a same-session 4096 control
# (evals/results/tail_analysis.json -> live_cap_sweep):
#
#   (a) IT DOES NOT LOWER LATENCY. Of the 10 cells that returned an analysis at both
#       their cap and the control, 5 were faster and 5 slower; mean delta -0.23s
#       (min -10.39s, max +17.84s). APP-1004 rings is the clean counter-example: the cap
#       DID bind the token count (2876 -> 1980 tokens at 2048) and the call still took
#       28.50s against the control's 38.90s at a different draw. Meanwhile APP-1012
#       bustout took 33.70s at 3072 and 15.87s at 4096.
#   (b) IT TRUNCATES, NON-MONOTONICALLY. 14 of 30 cells raised MaxTokensReachedException.
#       1024 truncated 6 of 6 cells. Both agents truncated AND completed at 1536 and at
#       2048 depending on the application and the draw, so the boundary is a PROBABILITY,
#       not a threshold. 3072 was the lowest cap where every cell completed - one draw
#       each, which is not enough to call it safe.
#   (c) IT MOVES FRAUD BANDS. 13 of 30 cells agreed with the same-session control; 3 of
#       the disagreements were genuine band moves rather than truncations.
#
# And a truncated call is the WORST outcome available, not a cheap one: strands raises
# rather than returning the partial text, so the call spends its full wall clock (263.2s
# across the 14 truncated cells, up to 29.0s each), is billed for every token it
# generated (bounded ~$0.4378), and returns NOTHING - fan-out degrades to seven of eight
# fraud dimensions. A cap low enough to shorten these agents is a cap low enough to
# delete them.
#
# A CHARACTER CAP AND A TOKEN CAP ARE NOT THE SAME CONSTRAINT, which is why deriving one
# from the other is a trap. Six of the customer's eight prompts say "max 4000 characters"
# and two (bustout, rings) say nothing about length at all. But measured chars-per-output-
# token medians run from 4.51 (dealer) down to 1.55 (identity), and identity's worst calls
# hit 0.33 - 3,582 billed output tokens behind 1,191 characters of analysis. A cap
# computed as 4000/4 would truncate identity at a third of its contract. The customer's
# character rule belongs in her prompt text and in the output-contract tests, not in a
# token cap.
# ---------------------------------------------------------------------------

#: Per-AGENT output cap, because the cap is a property of the agent's own output contract
#: and its own measured verbosity - NOT of whichever model it currently runs on.
#:
#: WHY PER-AGENT AND NOT PER-TIER, concretely: until 2026-07-28 rings drew its cap from
#: ``MAX_TOKENS_BY_TIER[tier_label(model)]``, so moving rings from HEAVY to MID silently
#: moved which cap it inherited. Both happen to be 4096 today, so nothing changed - but
#: rings is one of the two agents with no length cap in its prompt and the only two agents
#: measured to truncate, and a future move to LIGHT would have dropped it to 3072, the
#: lowest cap that completed every cell on ONE draw each. Coupling the truncation risk of
#: an uncapped-prompt agent to an unrelated tier decision is a latent defect; this map
#: removes it. ``agents.fanout`` still resolves by tier, so these values are chosen to
#: reproduce the shipped effective caps exactly - see
#: ``test_per_agent_caps_preserve_the_shipped_effective_values``.
#:
#: EVERY VALUE IS ITS AGENT'S OWN MEASURED CEILING PLUS HEADROOM. "max observed" is the
#: largest output token count for that agent across every live call on disk AT THE MODEL IT
#: IS NOW ASSIGNED: n=42 from evals/results/pipeline_14x3_us-east-1.json, the Sonnet 5 cells
#: of the tier sweep, and the cap sweep's 8 calls each for bustout/rings. Opus 5 rows are
#: excluded because no agent runs on Opus any more -- including them would size bustout's cap
#: against 3385 tokens from a model this assignment does not use:
#:
#:     agent        max observed   cap    headroom   prompt says
#:     straw            989        3072     3.1x     max 4000 chars
#:     dealer          1464        3072     2.1x     max 4000 chars
#:     employment      1647        4096     2.5x     max 4000 chars
#:     synthetic       2737        4096     1.5x     max 4000 chars
#:     income          2783        4096     1.5x     max 4000 chars
#:     rings           2876        4096     1.4x     NOTHING
#:     bustout         3257        4096     1.3x     NOTHING
#:     identity        3582        4096     1.1x     max 4000 chars
#:
#: identity's 1.1x is the thinnest margin in the table and it is deliberately NOT
#: tightened: its 3,582-token call carried only 1,191 characters, so it is the agent whose
#: token usage is least predictable from its visible output.
#:
#: NOT LOWERED ANYWHERE. Every value here is at or above the shipped cap, because the
#: sweep above found no cap below 4096 that is safe for bustout and rings and found no
#: latency benefit at any cap for anyone. If a future measurement shows a cap that
#: shortens a specific agent without truncating it and without moving its band, lower THAT
#: agent's entry and record the run that proved it.
MAX_TOKENS_BY_AGENT: Final[dict[str, int]] = {
    "identity": 4096,
    "dealer": 3072,
    "straw": 3072,
    "employment": 4096,
    "income": 4096,
    "synthetic": 4096,
    "bustout": 4096,
    "rings": 4096,
}

#: The lowest ``max_tokens`` at which every measured cell completed, per agent, and the
#: caps at which each was OBSERVED to raise ``MaxTokensReachedException``. Recorded so
#: nobody tightens ``MAX_TOKENS_BY_AGENT`` blindly: these two agents truncated at 1024,
#: 1536 AND 2048 while also COMPLETING at 1536 and 2048 on other applications, so the
#: numbers below bound a probability, not a threshold. Only bustout and rings were swept
#: (they are the two with no length cap in their prompt and the two most verbose agents);
#: the other six carry ``None`` because nobody measured them, not because they are safe.
MEASURED_TRUNCATION_BOUNDARY: Final[dict[str, dict[str, Any]]] = {
    "bustout": {
        "caps_tested": (1024, 1536, 2048, 3072, 4096),
        "caps_observed_truncating": (1024, 1536, 2048),
        "lowest_cap_every_cell_completed": 3072,
        "n_applications": 3,
        "source": "evals/results/tail_analysis.json -> live_cap_sweep",
    },
    "rings": {
        "caps_tested": (1024, 1536, 2048, 3072, 4096),
        "caps_observed_truncating": (1024, 1536, 2048),
        "lowest_cap_every_cell_completed": 3072,
        "n_applications": 3,
        "source": "evals/results/tail_analysis.json -> live_cap_sweep",
    },
}

#: Per-tier output cap, retained as the FALLBACK for callers that hold a model id but no
#: agent name (``build_bedrock_model`` when no explicit ``max_tokens`` is passed, and any
#: non-specialist call such as the eval judge). ``MAX_TOKENS_BY_AGENT`` governs the eight
#: specialists and is the table to change; this one exists so a model id alone still
#: resolves to something safe.
#:
#: Kept at the shipped values on purpose: the live cap sweep found no latency benefit at
#: any lower cap and 14 truncations out of 30 cells, so there is nothing here to win.
MAX_TOKENS_BY_TIER: Final[dict[str, int]] = {
    "light": 3072,
    "mid": 4096,
    "heavy": 4096,
    "custom": 4096,
}

#: The synthesizer emits a 21-key JSON object whose nine summaries are each capped by the
#: customer at "under 1200 characters", plus recommended_stipulations and
#: top_risk_factors. Nine summaries x 1200 chars is ~2,850 tokens; 4096 covers the worst
#: legitimate case with headroom for the JSON scaffolding.
#:
#: MEASURED, n=42 live adjudications (evals/results/pipeline_14x3_us-east-1.json): the
#: largest synthesis wrote 1,005 output tokens and the median 666, so 4096 is 4.1x the
#: observed ceiling and has never bound. 0 of 42 calls truncated and 0 needed a repair
#: attempt. It is left wide because truncation here destroys the JSON object rather than
#: shortening it - a truncated synthesis fails the 21-key contract outright, which is a
#: failed adjudication rather than a cheaper one.
#:
#: MEASURED, earlier pass: at 16384 a live synthesis took 84s of a 113s end-to-end run and
#: emitted 9,244 output tokens - far past the contract it was asked to honour. So the
#: ceiling does matter; it just does not belong anywhere near the observed maximum.
SYNTHESIZER_MAX_TOKENS: Final[int] = 4096


def max_tokens_for(role: str) -> int:
    """Output cap for a domain or for 'synthesizer'.

    Resolves from :data:`MAX_TOKENS_BY_AGENT` for the eight specialists, so the cap
    follows the agent's own output contract rather than whichever model it currently
    runs on. Falls back to the assigned model's tier for an unknown role, which is the
    same value ``build_bedrock_model`` would have picked.
    """
    if role == "synthesizer":
        return SYNTHESIZER_MAX_TOKENS
    if role in MAX_TOKENS_BY_AGENT:
        return MAX_TOKENS_BY_AGENT[role]
    return MAX_TOKENS_BY_TIER[tier_label(model_for(role))]

_shared_client_lock = threading.Lock()
_shared_clients: dict[str, Any] = {}


def resolve_region() -> str:
    """Region for the bedrock-runtime client (AWS_REGION, else us-east-1)."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def shared_bedrock_client(region: str | None = None) -> Any:
    """Return the process-wide bedrock-runtime client for ``region``.

    Two ``BedrockModel`` instances built from the same ``boto3.Session`` still
    get *separate* botocore clients, and therefore separate connection pools -
    so an 8-way fan-out over 8 models would hold 8 pools of 10. botocore clients
    are thread-safe, so one client with a raised pool is both correct and the
    only way the pool ceiling is actually shared.
    """
    import boto3
    from botocore.config import Config as BotocoreConfig

    resolved = region or resolve_region()
    with _shared_client_lock:
        client = _shared_clients.get(resolved)
        if client is None:
            client = boto3.Session().client(
                service_name="bedrock-runtime",
                region_name=resolved,
                config=BotocoreConfig(
                    max_pool_connections=MAX_POOL_CONNECTIONS,
                    read_timeout=READ_TIMEOUT_S,
                    connect_timeout=CONNECT_TIMEOUT_S,
                    retries={"mode": "adaptive"},
                    user_agent_extra="pp-fraud-underwriter",
                ),
            )
            _shared_clients[resolved] = client
        return client


def reset_shared_clients() -> None:
    """Drop cached bedrock-runtime clients (tests, region switches)."""
    with _shared_client_lock:
        _shared_clients.clear()


def _validate_model_config(model_config: dict[str, Any]) -> None:
    """Fail loudly on a key `BedrockConfig` does not declare.

    ``BedrockModel.update_config`` only emits a ``UserWarning`` for unknown keys,
    so a typo like ``max_token`` is accepted, ignored, and never surfaces. This
    turns that into an exception at construction time.
    """
    from typing_extensions import get_type_hints

    from strands.models import BedrockModel

    valid = set(get_type_hints(BedrockModel.BedrockConfig).keys())
    unknown = sorted(set(model_config) - valid)
    if unknown:
        raise ValueError(
            f"BedrockConfig does not declare {unknown}; valid keys are {sorted(valid)}. "
            "strands only warns about this, so it is raised here instead."
        )


def build_bedrock_model(
    model_id: str,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    region: str | None = None,
    effort: str | None = None,
    streaming: bool = True,
    client: Any | None = None,
) -> Any:
    """Build a `BedrockModel` sharing one bedrock-runtime client.

    ``effort`` (Opus 5 / Sonnet 5) is passed through
    ``additional_request_fields={'output_config': {'effort': ...}}`` and is
    opt-in - the default sends nothing, so no behaviour is assumed.

    Note the argument order constraint in the SDK: passing both ``region_name``
    and ``boto_session`` raises, so this builds the model with neither and
    replaces ``model.client`` with the shared one afterwards.
    """
    from strands.models import BedrockModel

    resolved_max_tokens = max_tokens if max_tokens is not None else MAX_TOKENS_BY_TIER[tier_label(model_id)]

    model_config: dict[str, Any] = {
        "model_id": model_id,
        "max_tokens": resolved_max_tokens,
        "streaming": streaming,
    }
    if temperature is not None:
        model_config["temperature"] = temperature
    if effort is not None:
        model_config["additional_request_fields"] = {"output_config": {"effort": effort}}

    _validate_model_config(model_config)

    model = BedrockModel(region_name=region or resolve_region(), **model_config)
    # Replace the per-instance client so all N models share one pool.
    model.client = client if client is not None else shared_bedrock_client(region)
    return model


def build_retry_strategy() -> Any:
    """Throttle-retry strategy tuned for a sub-minute SLA."""
    from strands.agent import ModelRetryStrategy

    return ModelRetryStrategy(
        max_attempts=RETRY_MAX_ATTEMPTS,
        initial_delay=RETRY_INITIAL_DELAY_S,
        max_delay=RETRY_MAX_DELAY_S,
    )


def cached_system_prompt(prompt: str) -> list[dict[str, Any]]:
    """Wrap a system prompt in the SystemContentBlock list that enables caching.

    Reaches Bedrock verbatim as ``[{'text': ...}, {'cachePoint': {...}}]``.
    Check ``will_cache`` first: below the model's minimum prefix this is a no-op
    that costs full price.

    MEASURED REALITY FOR THIS WORKLOAD -- read before quoting a caching benefit.

    The cache point is placed here, after the verbatim customer prompt, and that is
    the only correct place for it, but it rarely pays:

      * The customer's specialist prompts are 547-956 estimated tokens. The minimum
        cacheable prefix is 512 (Opus 5), 1024 (Sonnet 5 / 4.6, Opus 4.8) and 4096
        (Haiku 4.5). So only the two Opus-tier agents clear the bar on the prompt
        alone; the other six accept the cache point silently, report
        ``cacheWriteInputTokens=0``, and pay full input price.
      * Extending the cached prefix with the signal layer's per-domain reference
        block (signal names, the customer's own bands, entitled alert ids) lifts five
        of eight past 1024 but still leaves all eight far below Haiku's 4096.
      * More fundamentally, the part of the request that is actually large -- the
        projected application payload, 3.4-8.3 KB per domain -- is DIFFERENT for every
        application. A cache entry is keyed on an exact prefix, so it can only be
        reused when the same application is analysed again. In a forward-flow
        underwriting stream that essentially never happens.

    Padding the prefix to cross a threshold would raise input token count in order to
    make a cache-hit metric look good, which costs more than it saves. So caching is
    left enabled (it is free when it misses) and is NOT counted as a cost lever. The
    levers that survive measurement are the ``global.*`` prefix (-9.09%), batch
    inference (-50%, on the models that support it), and removing per-application
    Cortex Analyst calls. ``cache_prefix_report`` reports the truth per agent, and
    ``evals/bench.py`` records real ``cacheReadInputTokens`` rather than assuming.
    """
    return [{"text": prompt}, {"cachePoint": {"type": "default"}}]
