"""
the orchestrator — orchestrator and synthesizer for the fraud-analysis platform.

Two distinct roles, matching the source system:

1. UNDERWRITING PIPELINE (the performance-critical path):
   the eight specialists run IN PARALLEL, each on its own configured model, and
   the orchestrator acts as the SYNTHESIZER, combining the eight analyses into a single
   strict-JSON adjudication. Parallel fan-out plus per-agent model right-sizing
   is what turns the source system's 48-hour batch into sub-minute-per-app.
   See adjudicate() / underwriting_pipeline() / batch_throughput().

2. INTERACTIVE / TARGETED (outside underwriting):
   the orchestrator is the ORCHESTRATOR that routes a question to one or two specialists
   as tools. See analyze() / build_orchestrator().
"""

from __future__ import annotations
import concurrent.futures
import json
import os
import time

try:
    from strands import Agent
except ImportError:  # MOCK_MODE / offline / CI without the SDK installed
    Agent = None

from agents.prompts import MASTER_PROMPT, SYNTHESIZER_PROMPT
from agents.models import (
    SYNTHESIZER_MODEL, SPECIALIST_MODELS, model_for, tier_label, nominal_latency,
)
from agents.specialists import SPECIALIST_TOOLS, _run_specialist, run_specialist_metered
from agents.pricing import specialist_cost, synthesizer_cost, nominal_tokens
from tools.application_data import fetch_application_record
from tools.cortex_analyst_mcp import retrieve_signals

try:
    from observability.metrics import emit_adjudication
except Exception:  # observability optional (needs boto3/AWS); never breaks the demo
    def emit_adjudication(*args, **kwargs):  # type: ignore
        return False

MOCK_MODE = os.environ.get("MOCK_MODE", "0") == "1"

# The eight fraud dimensions, in the canonical order the orchestrator reports them.
DOMAINS = ["identity", "dealer", "straw", "employment",
           "income", "synthetic", "bustout", "rings"]


# ===========================================================================
# 1) UNDERWRITING PIPELINE — parallel fan-out + the orchestrator synthesis + timing
# ===========================================================================
def adjudicate(application_id: str) -> dict:
    """
    Run the underwriting pipeline for one application.

    The eight specialists run concurrently (one thread each, each on its
    configured model). The orchestrator then synthesizes their analyses into the
    strict-JSON adjudication. Returns a result dict with the adjudication,
    timing, and per-agent cost.
    """
    app_id = application_id.strip().upper()
    record = fetch_application_record(app_id)
    if record is None:
        return {"application_id": app_id,
                "adjudication_json": json.dumps({"application_id": app_id, "error": "not found"}),
                "verdicts": {}, "timing": {}, "per_agent": {}, "cost": {}}

    # ---- parallel fan-out (metered) --------------------------------------
    verdicts: dict[str, str] = {}
    per_agent: dict[str, dict] = {}
    wall_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(DOMAINS)) as pool:
        futures = {pool.submit(_metered_specialist, d, app_id): d for d in DOMAINS}
        for fut in concurrent.futures.as_completed(futures):
            d = futures[fut]
            per_agent[d] = fut.result()
            verdicts[d] = per_agent[d]["_text"]
    fanout_wall = time.perf_counter() - wall_start

    # ---- orchestrator synthesis ------------------------------------------
    synth_start = time.perf_counter()
    adjudication_json = (
        _synthesize_mock(app_id, verdicts) if MOCK_MODE
        else _synthesize_live(app_id, verdicts)
    )
    synth_wall = time.perf_counter() - synth_start
    synth_in, synth_out = nominal_tokens("synthesizer")
    synth_cost = synthesizer_cost(synth_in, synth_out)

    # ---- timing (measured + nominal real-world extrapolation) -----------
    nominal_fanout = max(per_agent[d]["nominal_s"] for d in DOMAINS)      # parallel = slowest agent
    nominal_serial = sum(per_agent[d]["nominal_s"] for d in DOMAINS)      # sequential = sum
    nominal_synth = nominal_latency(SYNTHESIZER_MODEL)
    timing = {
        "measured_fanout_wall_s": round(fanout_wall, 3),
        "measured_synth_wall_s": round(synth_wall, 3),
        "measured_total_s": round(fanout_wall + synth_wall, 3),
        "nominal_parallel_per_app_s": round(nominal_fanout + nominal_synth, 1),
        "nominal_sequential_per_app_s": round(nominal_serial + nominal_synth, 1),
        "synthesizer_model": SYNTHESIZER_MODEL,
    }

    # ---- cost ------------------------------------------------------------
    agents_cost = sum(per_agent[d]["cost_usd"] for d in DOMAINS)
    cost = {
        "per_agent_usd": {d: per_agent[d]["cost_usd"] for d in DOMAINS},
        "synthesizer_usd": round(synth_cost, 6),
        "total_usd": round(agents_cost + synth_cost, 6),
    }
    per_agent["synthesizer"] = {
        "model": SYNTHESIZER_MODEL, "tier": tier_label(SYNTHESIZER_MODEL),
        "input_tokens": synth_in, "output_tokens": synth_out,
        "cost_usd": round(synth_cost, 6), "nominal_s": nominal_synth,
    }

    # strip internal text before returning per_agent detail
    for d in DOMAINS:
        per_agent[d].pop("_text", None)

    # ---- emit per-agent telemetry (best-effort; no-op offline) -----------
    try:
        adj = json.loads(adjudication_json)
        emit_adjudication(app_id, per_agent, cost, timing,
                          adj.get("overall_risk_decision", ""))
    except Exception:
        pass

    return {"application_id": app_id, "verdicts": verdicts, "per_agent": per_agent,
            "adjudication_json": adjudication_json, "timing": timing, "cost": cost}


def _metered_specialist(domain: str, app_id: str) -> dict:
    """Run one specialist, returning its verdict, timing, tokens, and cost."""
    t0 = time.perf_counter()
    text, in_tok, out_tok = run_specialist_metered(domain, app_id)
    elapsed = time.perf_counter() - t0
    model_id = model_for(domain)
    return {
        "_text": text,
        "risk": _parse_risk(text),
        "findings": text.split("FINDINGS:", 1)[-1].strip(),
        "model": model_id,
        "tier": tier_label(model_id),
        "elapsed_s": round(elapsed, 3),
        "nominal_s": nominal_latency(model_id),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(specialist_cost(domain, in_tok, out_tok), 6),
    }


def adjudicate_stream(application_id: str):
    """
    Generator form of the underwriting pipeline for a live UI.

    Yields event dicts as agents complete, so a front end can light up each
    specialist the moment it finishes (showing the parallel fan-out visually):
      {"event":"start", ...}
      {"event":"agent_done", "domain":..., "risk":..., "cost_usd":..., ...}   (x8)
      {"event":"synthesis", ...}
      {"event":"done", "adjudication":{...}, "timing":{...}, "cost":{...}}
    """
    app_id = application_id.strip().upper()
    record = fetch_application_record(app_id)
    if record is None:
        yield {"event": "error", "message": f"Application {app_id} not found."}
        return

    yield {
        "event": "start",
        "application_id": app_id,
        "agents": [{"domain": d, "tier": tier_label(model_for(d)),
                    "model": model_for(d), "nominal_s": nominal_latency(model_for(d))}
                   for d in DOMAINS],
        "core_fields": {k: record.get(k) for k in
                        ("borrower_name", "loan_amount", "vehicle", "fraud_score",
                         "stated_annual_income", "ltv_pct", "dealer_name")},
    }

    per_agent: dict[str, dict] = {}
    verdicts: dict[str, str] = {}
    wall_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(DOMAINS)) as pool:
        futures = {pool.submit(_metered_specialist, d, app_id): d for d in DOMAINS}
        for fut in concurrent.futures.as_completed(futures):
            d = futures[fut]
            info = fut.result()
            per_agent[d] = info
            verdicts[d] = info["_text"]
            yield {"event": "agent_done", "domain": d, "risk": info["risk"],
                   "findings": info["findings"], "tier": info["tier"],
                   "model": info["model"], "elapsed_s": info["elapsed_s"],
                   "cost_usd": info["cost_usd"]}
    fanout_wall = time.perf_counter() - wall_start

    yield {"event": "synthesis", "message": "Orchestrator synthesizing eight analyses..."}
    synth_start = time.perf_counter()
    adjudication_json = (
        _synthesize_mock(app_id, verdicts) if MOCK_MODE
        else _synthesize_live(app_id, verdicts)
    )
    synth_wall = time.perf_counter() - synth_start

    nominal_fanout = max(per_agent[d]["nominal_s"] for d in DOMAINS)
    nominal_serial = sum(per_agent[d]["nominal_s"] for d in DOMAINS)
    nominal_synth = nominal_latency(SYNTHESIZER_MODEL)
    synth_in, synth_out = nominal_tokens("synthesizer")
    synth_cost = synthesizer_cost(synth_in, synth_out)
    agents_cost = sum(per_agent[d]["cost_usd"] for d in DOMAINS)

    timing = {
        "measured_total_s": round(fanout_wall + synth_wall, 3),
        "nominal_parallel_per_app_s": round(nominal_fanout + nominal_synth, 1),
        "nominal_sequential_per_app_s": round(nominal_serial + nominal_synth, 1),
    }
    cost = {
        "per_agent_usd": {d: per_agent[d]["cost_usd"] for d in DOMAINS},
        "synthesizer_usd": round(synth_cost, 6),
        "total_usd": round(agents_cost + synth_cost, 6),
    }
    try:
        adj = json.loads(adjudication_json)
    except Exception:
        adj = {"raw": adjudication_json}

    # best-effort telemetry
    pa_for_metrics = dict(per_agent)
    pa_for_metrics["synthesizer"] = {"model": SYNTHESIZER_MODEL, "tier": tier_label(SYNTHESIZER_MODEL),
                                     "input_tokens": synth_in, "output_tokens": synth_out,
                                     "cost_usd": round(synth_cost, 6), "nominal_s": nominal_synth}
    for d in list(pa_for_metrics):
        pa_for_metrics[d].pop("_text", None)
    try:
        emit_adjudication(app_id, pa_for_metrics, cost, timing, adj.get("overall_risk_decision", ""))
    except Exception:
        pass

    yield {"event": "done", "adjudication": adj, "timing": timing, "cost": cost}


def _timed_specialist(domain: str, app_id: str) -> tuple[str, float]:
    t0 = time.perf_counter()
    text = _run_specialist(domain, app_id)
    return text, time.perf_counter() - t0


def underwriting_pipeline(application_id: str) -> str:
    """Render the parallel underwriting pipeline with a timing view + JSON."""
    res = adjudicate(application_id)
    if not res["verdicts"]:
        return res["adjudication_json"]

    t = res["timing"]
    lines = ["=" * 70,
             f"UNDERWRITING PIPELINE — {res['application_id']}",
             "8 specialists run IN PARALLEL, each on its configured model;",
             "the orchestrator synthesizes the results (synthesizer role here).",
             "=" * 70,
             f"{'agent':<12}{'tier':<14}{'~latency':<10}{'~cost':<11}{'risk'}"]
    for d in DOMAINS:
        pa = res["per_agent"][d]
        risk = _parse_risk(res["verdicts"][d])
        lines.append(f"{d:<12}{pa['tier']:<14}{pa['nominal_s']:<10.1f}"
                     f"${pa.get('cost_usd', 0):<10.5f}{risk}")
    c = res.get("cost", {})
    lines += [
        "",
        "Latency (illustrative nominal figures, per application):",
        f"  sequential (Snowflake-style, one model, one at a time): "
        f"{t['nominal_sequential_per_app_s']:.1f}s",
        f"  parallel on AgentCore (slowest agent + synthesis):       "
        f"{t['nominal_parallel_per_app_s']:.1f}s   -> under 1 minute",
        f"  measured wall-clock this run (scaled offline):           "
        f"{t['measured_total_s']:.3f}s",
        "",
        f"Cost (illustrative, per application): agents ${sum(c.get('per_agent_usd', {}).values()):.5f} "
        f"+ synthesis ${c.get('synthesizer_usd', 0):.5f} = ${c.get('total_usd', 0):.5f}/app",
        "",
        "ADJUDICATION (orchestrator synthesis):",
        res["adjudication_json"],
    ]
    return "\n".join(lines)


def batch_throughput(application_ids: list[str], concurrency: int = 4) -> str:
    """
    Process several applications concurrently and report throughput.

    Demonstrates the second requirement (batch volume): many application
    pipelines run at once. Reports measured wall-clock plus an extrapolation of
    daily capacity from the nominal per-app parallel latency.
    """
    t0 = time.perf_counter()
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for res in pool.map(adjudicate, application_ids):
            results.append(res)
    wall = time.perf_counter() - t0

    n = len(results)
    nominal_per_app = results[0]["timing"]["nominal_parallel_per_app_s"] if results else 0
    # With `concurrency` app-pipelines in flight, nominal daily capacity:
    daily_capacity = int((86400 / nominal_per_app) * concurrency) if nominal_per_app else 0

    lines = ["=" * 70,
             f"BATCH THROUGHPUT — {n} applications, concurrency={concurrency}",
             "=" * 70]
    for res in results:
        adj = json.loads(res["adjudication_json"])
        lines.append(f"  {res['application_id']}: {adj.get('overall_risk_decision','?'):<11} "
                     f"-> {adj.get('recommendation_decision','?')}")
    lines += [
        "",
        f"measured wall-clock (scaled offline): {wall:.3f}s for {n} apps",
        f"nominal per-app parallel latency:     {nominal_per_app:.1f}s",
        f"nominal daily capacity @ concurrency {concurrency}: ~{daily_capacity:,} apps/day",
        "(scale concurrency and Bedrock TPM quotas to reach hundreds of thousands/day)",
    ]
    return "\n".join(lines)


# ---- synthesizers ---------------------------------------------------------
def _synthesize_live(app_id: str, verdicts: dict[str, str]) -> str:
    """the orchestrator (LLM) synthesizes the eight analyses into strict JSON."""
    analyses = "\n\n".join(f"{d.upper()} ANALYSIS:\n{verdicts[d]}" for d in DOMAINS)
    synthesizer = Agent(model=SYNTHESIZER_MODEL, system_prompt=SYNTHESIZER_PROMPT)
    result = synthesizer(
        f"application_id: {app_id}\n\n{analyses}\n\n"
        "Synthesize these eight analyses into the required strict JSON."
    )
    return str(result)


def _synthesize_mock(app_id: str, verdicts: dict[str, str]) -> str:
    """Deterministic offline synthesis using the same totality-of-evidence rules."""
    out: dict = {"application_id": app_id}
    high = possible = 0
    factors: list[str] = []
    for d in DOMAINS:
        risk = _parse_risk(verdicts[d])
        findings = verdicts[d].split("FINDINGS:", 1)[-1].strip()
        out[f"{d}_summary"] = findings
        out[f"{d}_flag"] = 1 if risk == "HIGH RISK" else 0
        if risk == "HIGH RISK":
            high += 1
            factors.append(f"{d}: {findings}")
        elif risk == "POSSIBLE RISK":
            possible += 1

    if high >= 2 or (high >= 1 and possible >= 2):
        overall, rec = "HIGH RISK", "DECLINE"
    elif high >= 1 or possible >= 1:
        overall, rec = "MEDIUM RISK", "REVIEW AND APPLY STIPULATIONS"
    else:
        overall, rec = "LOW RISK", "APPROVE"

    out["overall_risk_decision"] = overall
    out["recommendation_decision"] = rec
    out["recommended_stipulations"] = (
        "" if rec != "REVIEW AND APPLY STIPULATIONS"
        else "Verify income documentation and re-confirm identity/employment before funding."
    )
    top = [f"{i+1}. {f}" for i, f in enumerate(factors[:4])]
    out["top_risk_factors"] = "" if overall == "LOW RISK" else "; ".join(top)
    out["master_summary"] = (
        f"{overall} for {app_id}. {high} high-risk and {possible} possible-risk "
        f"specialist findings across eight fraud domains, synthesized by the orchestrator. "
        f"Recommendation: {rec}."
    )

    ordered = ["application_id", "master_summary", "overall_risk_decision",
               "recommendation_decision", "recommended_stipulations", "top_risk_factors"]
    for d in DOMAINS:
        ordered += [f"{d}_summary", f"{d}_flag"]
    return json.dumps({k: out[k] for k in ordered}, indent=2)


# ===========================================================================
# 2) INTERACTIVE / TARGETED — the orchestrator as router (outside underwriting)
# ===========================================================================
def build_orchestrator() -> Agent:
    """Construct the orchestrator (router) with the specialist tools attached."""
    return Agent(model=SYNTHESIZER_MODEL, system_prompt=MASTER_PROMPT, tools=SPECIALIST_TOOLS)


def analyze(query: str) -> str:
    """
    Single fraud-analysis request.

    Full-review / adjudication requests go through the parallel underwriting
    pipeline and return the adjudication JSON. Targeted questions route to a
    specialist. In live mode a non-review query is handled by the orchestrator (router).
    """
    app_id = _extract_app_id(query)
    if app_id and _is_full_review(query):
        return adjudicate(app_id)["adjudication_json"]

    if MOCK_MODE:
        domain = _route_domain(query)
        if app_id and domain:
            return f"[{domain} agent] {app_id}\n{_run_specialist(domain, app_id)}"
        return ("MOCK_MODE: provide an application id (e.g. APP-1004) and either a "
                "full-review request or a domain keyword (identity, income, rings, ...).")

    return str(build_orchestrator()(query))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
_DOMAIN_KEYWORDS = {
    "synthetic": ["synthetic", "fake identity"],
    "straw": ["straw", "proxy", "nominee"],
    "bustout": ["bust", "default", "delinquent"],
    "dealer": ["dealer", "dealership"],
    "income": ["income", "salary", "earnings"],
    "employment": ["employment", "employer", "job"],
    "rings": ["ring", "network", "organized"],
    "identity": ["identity", "ssn", "pii"],  # least specific — checked last
}


def _extract_app_id(query: str) -> str | None:
    import re
    m = re.search(r"APP-\d{3,}", query, re.IGNORECASE)
    return m.group(0).upper() if m else None


def _is_full_review(query: str) -> bool:
    q = query.lower()
    return any(k in q for k in ("full review", "adjudicat", "approve", "decline",
                                "underwrit", "run all", "complete review", "all agents"))


def _route_domain(query: str) -> str | None:
    q = query.lower()
    order = ["synthetic", "straw", "bustout", "dealer", "income", "employment", "rings", "identity"]
    for domain in order:
        if any(kw in q for kw in _DOMAIN_KEYWORDS[domain]):
            return domain
    return None


def _parse_risk(verdict: str) -> str:
    for line in verdict.splitlines():
        if line.strip().upper().startswith("RISK:"):
            return line.split(":", 1)[1].strip().upper()
    return "LOW RISK"


# ===========================================================================
# Two-phase demo: signal layer (Cortex Analyst via MCP) -> analysis layer
# ===========================================================================
def signal_layer_demo(application_id: str) -> str:
    """Render Phase 1 (signal retrieval via MCP) then Phase 2 (parallel adjudication)."""
    lines: list[str] = []
    app_id = application_id.strip().upper()

    envelope = retrieve_signals(app_id)
    lines.append("=" * 70)
    lines.append("PHASE 1 - SIGNAL LAYER  (Snowflake Cortex Analyst semantic view via MCP)")
    lines.append("=" * 70)
    lines.append(f"source          : {envelope.get('source')}")
    lines.append(f"semantic_view   : {envelope.get('semantic_view')}")
    lines.append(f"question        : {envelope.get('question')}")
    lines.append("")
    lines.append("guardrails enforced by the semantic layer (preserved in migration):")
    for g in envelope.get("guardrails_applied", []):
        lines.append(f"  - {g}")
    lines.append("")
    lines.append("generated SQL (produced by Cortex Analyst, not by the agents):")
    for sql_line in (envelope.get("generated_sql", "") or "").splitlines():
        lines.append(f"  {sql_line}")
    lines.append("")
    sig = envelope.get("signals", {})
    lines.append(f"retrieved signals: {len(sig)} fields (handed to the analysis layer)")
    if "error" in envelope:
        lines.append(f"  ! {envelope['error']}")
        return "\n".join(lines)

    lines.append("")
    lines.append("=" * 70)
    lines.append("PHASE 2 - ANALYSIS LAYER  (8 specialists in parallel; the orchestrator synthesizes)")
    lines.append("=" * 70)
    lines.append(underwriting_pipeline(app_id))
    return "\n".join(lines)
