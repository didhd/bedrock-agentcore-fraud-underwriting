"""
The eight fraud specialists, exposed to the master orchestrator as tools.

This implements the "agents as tools" pattern: each specialist is a full LLM
agent with its own system prompt, but from the orchestrator's perspective it is a single
callable tool. This mirrors the source Snowflake Cortex setup where the
master agent routes to named sub-agents, one per fraud dimension (identity,
dealer, straw, employment, income, synthetic, bust-out, rings).

Set MOCK_MODE=1 (env) to run without invoking Bedrock — each specialist returns
a deterministic heuristic verdict from the signals so the orchestration flow can
be demonstrated offline / in CI.
"""

from __future__ import annotations
import os

# strands is only required for LIVE mode (invoking Bedrock). In MOCK_MODE the
# demo runs with zero heavy dependencies, so we guard the import and fall back
# to a no-op @tool decorator that preserves the function unchanged.
try:
    from strands import Agent, tool
except ImportError:  # MOCK_MODE / offline / CI without the SDK installed
    Agent = None

    def tool(func):  # type: ignore
        return func

from agents.prompts import SPECIALIST_PROMPTS
from agents.models import model_for, nominal_latency
from agents.pricing import nominal_tokens
from tools.application_data import fetch_application_record, render_application_for_agent

MOCK_MODE = os.environ.get("MOCK_MODE", "0") == "1"


def _sim_latency_scale() -> float:
    """Read the offline latency-simulation scale at call time (0 = disabled)."""
    try:
        return float(os.environ.get("SIM_LATENCY_SCALE", "0"))
    except ValueError:
        return 0.0


def _usage_from_result(result) -> tuple[int, int]:
    """Best-effort extraction of (input_tokens, output_tokens) from a Strands result."""
    try:
        usage = result.metrics.accumulated_usage  # type: ignore[attr-defined]
        return int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))
    except Exception:
        return nominal_tokens("specialist")


def run_specialist_metered(domain: str, application_id: str) -> tuple[str, int, int]:
    """
    Run a specialist and return (analysis_text, input_tokens, output_tokens).

    Offline (MOCK_MODE) returns the deterministic verdict plus illustrative
    nominal token counts so cost/telemetry can be shown without Bedrock.
    """
    record = fetch_application_record(application_id)
    if record is None:
        return (f"RISK: LOW RISK\nFINDINGS: Application {application_id} not found.", 0, 0)

    model_id = model_for(domain)
    if MOCK_MODE:
        scale = _sim_latency_scale()
        if scale > 0:
            import time
            time.sleep(nominal_latency(model_id) * scale)
        in_tok, out_tok = nominal_tokens("specialist")
        return _mock_specialist(domain, record), in_tok, out_tok

    specialist = Agent(model=model_id, system_prompt=SPECIALIST_PROMPTS[domain])
    payload = render_application_for_agent(record, domain=domain)
    result = specialist(f"Analyze this application for {domain} fraud risk.\n\n{payload}")
    in_tok, out_tok = _usage_from_result(result)
    return str(result), in_tok, out_tok


def _run_specialist(domain: str, application_id: str) -> str:
    """Fetch the application, run the domain specialist on its configured model."""
    record = fetch_application_record(application_id)
    if record is None:
        return f"RISK: LOW RISK\nFINDINGS: Application {application_id} not found in the dataset."

    model_id = model_for(domain)

    if MOCK_MODE:
        scale = _sim_latency_scale()
        if scale > 0:
            import time
            time.sleep(nominal_latency(model_id) * scale)
        return _mock_specialist(domain, record)

    prompt = SPECIALIST_PROMPTS[domain]
    specialist = Agent(model=model_id, system_prompt=prompt)
    payload = render_application_for_agent(record, domain=domain)
    result = specialist(f"Analyze this application for {domain} fraud risk.\n\n{payload}")
    return str(result)


# ---------------------------------------------------------------------------
# Tool wrappers — one per specialist. Names match the master prompt's routing.
# ---------------------------------------------------------------------------
@tool
def call_identity_agent(application_id: str) -> str:
    """Analyze an application for IDENTITY fraud (SSN sharing, PII mismatch, identity volatility). Args: application_id e.g. 'APP-1004'."""
    return _run_specialist("identity", application_id)


@tool
def call_dealer_agent(application_id: str) -> str:
    """Analyze an application for DEALER fraud (dealer-orchestrated coordination), separating it from portfolio/credit risk. Args: application_id."""
    return _run_specialist("dealer", application_id)


@tool
def call_straw_agent(application_id: str) -> str:
    """Analyze an application for STRAW BORROWER fraud (proxy/nominee co-borrowers, role-switching). Args: application_id."""
    return _run_specialist("straw", application_id)


@tool
def call_employment_agent(application_id: str) -> str:
    """Analyze an application for EMPLOYMENT fraud (fabricated/shell employers), separating it from data gaps. Args: application_id."""
    return _run_specialist("employment", application_id)


@tool
def call_income_agent(application_id: str) -> str:
    """Analyze an application for INCOME fraud (overstatement, inconsistent reporting). Args: application_id."""
    return _run_specialist("income", application_id)


@tool
def call_synthetic_agent(application_id: str) -> str:
    """Analyze an application for SYNTHETIC IDENTITY fraud (fabricated identity + manufactured credit). Args: application_id."""
    return _run_specialist("synthetic", application_id)


@tool
def call_bustout_agent(application_id: str) -> str:
    """Analyze an application for BUST-OUT fraud (rapid velocity, unsustainable exposure). Args: application_id."""
    return _run_specialist("bustout", application_id)


@tool
def call_rings_agent(application_id: str) -> str:
    """Analyze an application for FRAUD RING activity (shared identifiers, network overlap, exact-unit reuse). Args: application_id."""
    return _run_specialist("rings", application_id)


@tool
def get_application_summary(application_id: str) -> str:
    """Return the raw application record (core fields, signals, context) for inspection. Args: application_id."""
    record = fetch_application_record(application_id)
    if record is None:
        return f"Application {application_id} not found."
    return render_application_for_agent(record)


SPECIALIST_TOOLS = [
    call_identity_agent,
    call_dealer_agent,
    call_straw_agent,
    call_employment_agent,
    call_income_agent,
    call_synthetic_agent,
    call_bustout_agent,
    call_rings_agent,
    get_application_summary,
]


# ---------------------------------------------------------------------------
# Offline heuristic used when MOCK_MODE=1 (no Bedrock calls).
# Deterministic, signal-driven, and intentionally conservative to match the
# specialists' calibration (LOW is the most common outcome).
# ---------------------------------------------------------------------------
def _mock_specialist(domain: str, record: dict) -> str:
    s = record.get("signals", {})
    risk = "LOW RISK"
    findings = "Signals are isolated or explainable by context; no material fraud concern."

    if domain == "identity":
        if s.get("ssn_distinct_identity_combinations", 1) >= 2 and s.get("has_significant_identity_inconsistency", 0):
            risk, findings = "HIGH RISK", "SSN linked to multiple identity combinations with corroborating inconsistencies."
        elif s.get("phone_reuse_distinct_ssn_count", 0) >= 4:
            risk, findings = "POSSIBLE RISK", "Elevated phone reuse across distinct SSNs beyond household norms."
    elif domain == "synthetic":
        if s.get("very_new_file_high_score", 0) and s.get("older_borrower_new_credit_high_score", 0) and s.get("rapid_app_velocity", 0):
            risk, findings = "HIGH RISK", "Very new thin file with high score on an older borrower plus velocity — synthetic construction pattern."
    elif domain == "rings":
        if s.get("address_reuse_distinct_ssn_count", 0) >= 5 and s.get("address_ssn_count_same_unit", 0) >= 5:
            risk, findings = "HIGH RISK", "Repeated exact-unit reuse and multi-identifier overlap across unrelated SSNs."
    elif domain == "employment":
        if s.get("employer_spike_flag", 0) and s.get("employer_new_ssns_90d", 0) >= 8 and s.get("employer_distinct_other_ssns_count", 999) <= 15:
            risk, findings = "POSSIBLE RISK", "Small employer with a spike of new SSNs in 90 days — possible shell employer."
    elif domain == "income":
        pct = s.get("ssn_income_overstatement_percentage", 0)
        if pct > 25:
            risk, findings = "POSSIBLE RISK", f"Stated income overstated by ~{pct}%, above the 25% materiality threshold."
    elif domain == "bustout":
        if s.get("ssn_apps_last_7d", 0) >= 5 and s.get("ssn_loan_80_percent_or_more_annual_income_high_ratio", 0):
            risk, findings = "POSSIBLE RISK", "High 7-day application velocity combined with elevated loan-to-income exposure."
    elif domain == "straw":
        if not record.get("has_coborrower"):
            findings = "No co-borrower present; primary straw mechanism absent. Straw risk minimal."
    elif domain == "dealer":
        if s.get("dealer_consortium_score", 0) >= 900 and s.get("phone_reuse_count_this_dealer", 0) >= 3:
            risk, findings = "POSSIBLE RISK", "Elevated dealer consortium score with phone reuse suggesting coordination (validate against volume)."

    return f"RISK: {risk}\nFINDINGS: {findings}"
