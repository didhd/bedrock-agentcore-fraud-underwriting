"""
Per-agent model configuration: the headline Amazon Bedrock differentiator.

Snowflake Intelligence locks every agent to a single LLM (the source system runs
Claude Sonnet 4.6 across all agents today). Amazon Bedrock AgentCore lets each
agent use a right-sized model: a lightweight model for simple, near-deterministic
checks and a heavyweight model only where the reasoning genuinely needs it. That
is where both the latency win and the cost win come from, since the per-token
rate for a given Claude model is identical to Snowflake's.

Three tiers, all overridable by environment variable:

  LIGHT  (Haiku)  - fast, cheap; simple or often-trivial specialists
  MID    (Sonnet) - the source system's current across-the-board model
  HEAVY  (Opus)   - reserved for the most reasoning-intensive specialists

The nominal latencies are illustrative planning figures used by the demo's
timing view; real numbers come from live Bedrock runs.
"""

from __future__ import annotations
import os

# Model IDs (Bedrock, us. cross-region inference prefix). Override per tier.
LIGHT_MODEL = os.environ.get("BEDROCK_MODEL_LIGHT", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
MID_MODEL = os.environ.get("BEDROCK_MODEL_MID", "us.anthropic.claude-sonnet-4-6")
HEAVY_MODEL = os.environ.get("BEDROCK_MODEL_HEAVY", "us.anthropic.claude-opus-4-8")

# Short tier label per model id, for display.
TIER_OF = {LIGHT_MODEL: "light/Haiku", MID_MODEL: "mid/Sonnet", HEAVY_MODEL: "heavy/Opus"}

# Nominal per-call latency by tier (seconds). Illustrative planning figures for
# the timing view; the demo scales these down so offline runs are fast.
NOMINAL_LATENCY_S = {LIGHT_MODEL: 0.9, MID_MODEL: 2.5, HEAVY_MODEL: 6.0}

# Per-specialist model assignment. Rationale:
#   straw   - frequently trivial (no co-borrower) -> LIGHT
#   dealer  - mostly a portfolio-vs-fraud lookup  -> LIGHT
#   identity/income/employment/bustout - standard validation -> MID
#   synthetic/rings - multi-signal network / manufactured-credit reasoning -> HEAVY
SPECIALIST_MODELS = {
    "identity": MID_MODEL,
    "dealer": LIGHT_MODEL,
    "straw": LIGHT_MODEL,
    "employment": MID_MODEL,
    "income": MID_MODEL,
    "synthetic": HEAVY_MODEL,
    "bustout": MID_MODEL,
    "rings": HEAVY_MODEL,
}

# The orchestrator (synthesizer) weighs the totality of evidence -> MID by default
# (bump to HEAVY_MODEL via env for maximum synthesis quality).
SYNTHESIZER_MODEL = os.environ.get("BEDROCK_MODEL_SYNTHESIZER", MID_MODEL)


def model_for(domain: str) -> str:
    """Return the configured model id for a specialist domain."""
    return SPECIALIST_MODELS.get(domain, MID_MODEL)


def tier_label(model_id: str) -> str:
    """Human-readable tier label for a model id."""
    return TIER_OF.get(model_id, "custom")


def nominal_latency(model_id: str) -> float:
    """Illustrative per-call latency (seconds) for a model id."""
    return NOMINAL_LATENCY_S.get(model_id, 2.5)


def model_assignment_table() -> str:
    """Render the per-agent model configuration for display."""
    rows = ["Per-agent model configuration (the Bedrock differentiator):",
            f"  {'agent':<12}{'tier':<14}{'model id':<45}{'~latency'}"]
    for domain, mid in SPECIALIST_MODELS.items():
        rows.append(f"  {domain:<12}{tier_label(mid):<14}{mid:<45}{nominal_latency(mid):.1f}s")
    rows.append(f"  {'orchestrator':<12}{tier_label(SYNTHESIZER_MODEL):<14}"
                f"{SYNTHESIZER_MODEL:<45}{nominal_latency(SYNTHESIZER_MODEL):.1f}s  (synthesizer)")
    return "\n".join(rows)
