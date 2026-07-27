"""
Per-agent cost estimation.

Amazon Bedrock bills per token at the same published rates regardless of where
the model is hosted, so the cost advantage of this architecture comes from
routing each agent to a right-sized model, not from cheaper tokens. This module
turns token counts into dollars per agent so the demo (and the CloudWatch
dashboard) can show spend broken down by agent.

Rates are $ per 1M tokens (input, output). Update here if published rates change.
"""

from __future__ import annotations

from agents.models import LIGHT_MODEL, MID_MODEL, HEAVY_MODEL, model_for, SYNTHESIZER_MODEL

# $ per 1,000,000 tokens: (input_rate, output_rate)
MODEL_RATES = {
    LIGHT_MODEL: (1.0, 5.0),    # Haiku tier
    MID_MODEL: (3.0, 15.0),     # Sonnet tier
    HEAVY_MODEL: (5.0, 25.0),   # Opus tier
}
_DEFAULT_RATE = (3.0, 15.0)

# Illustrative token footprint per call when actual usage is unavailable
# (offline mock). (input_tokens, output_tokens).
NOMINAL_TOKENS = {
    "specialist": (1800, 450),   # system prompt + application payload -> short verdict
    "synthesizer": (5000, 700),  # eight analyses in -> strict-JSON adjudication
}


def cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Dollar cost of one call given the model and token counts."""
    in_rate, out_rate = MODEL_RATES.get(model_id, _DEFAULT_RATE)
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def nominal_tokens(role: str) -> tuple[int, int]:
    """Illustrative (input, output) token counts for a role: 'specialist' or 'synthesizer'."""
    return NOMINAL_TOKENS.get(role, NOMINAL_TOKENS["specialist"])


def specialist_cost(domain: str, input_tokens: int, output_tokens: int) -> float:
    """Cost of one specialist call on its configured model."""
    return cost_usd(model_for(domain), input_tokens, output_tokens)


def synthesizer_cost(input_tokens: int, output_tokens: int) -> float:
    """Cost of the orchestrator's synthesis call."""
    return cost_usd(SYNTHESIZER_MODEL, input_tokens, output_tokens)


def project_monthly(per_app_cost: float, apps_per_day: int) -> dict:
    """Project daily and monthly cost from a per-application cost."""
    daily = per_app_cost * apps_per_day
    return {
        "per_app_usd": round(per_app_cost, 4),
        "apps_per_day": apps_per_day,
        "daily_usd": round(daily, 2),
        "monthly_usd": round(daily * 30, 2),
    }
