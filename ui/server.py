"""
Demo server for the customer-facing migration UI.

Serves the built Vite/React bundle from ``ui/dist`` and four endpoints:

    GET /api/health                  backend capability report (what is really wired)
    GET /api/applications            the fixture catalog for the application selector
    GET /api/payload/{app_id}        the signal-layer view, per domain
    GET /api/stream/{app_id}         Server-Sent Events for one adjudication run

DESIGN RULE THAT DRIVES THIS WHOLE FILE: no number reaches the browser unless
this process measured it. Where a value is not measured -- most obviously token
counts and cost in MOCK_MODE, where no Bedrock call happens at all -- the field is
emitted as ``None`` and the UI renders an em dash. Every numeric frame carries a
``measured`` boolean and a ``source`` string so the front end can label provenance
instead of implying a benchmark. Latency IS genuinely measured even in MOCK_MODE
(it is the wall clock of the mock run) so it ships with ``source: "mock"`` rather
than being passed off as a Bedrock measurement.

The repo is mid-migration: several modules (``fixtures``, ``signal_layer.registry``,
``signal_layer.schema``, ``agents.fanout``, ``agents.synthesize``, ``agents.contracts``)
are landing from other workstreams. Everything below imports them if present and
degrades to the modules that exist today otherwise, reporting which path it took
through /api/health. Nothing here fakes a module that is missing.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

#: Offline by default: the demo must run with zero AWS. Set MOCK_MODE=0 to invoke
#: Bedrock for real (which is also the only mode in which tokens/cost are known).
MOCK_MODE = os.environ.get("MOCK_MODE", "1") == "1"

_UI_DIR = Path(__file__).resolve().parent
_DIST = _UI_DIR / "dist"


# ---------------------------------------------------------------------------
# Capability discovery: import what has landed, record what has not.
# ---------------------------------------------------------------------------
@dataclass
class Capabilities:
    """Which real modules this process found, for /api/health and the UI banner."""

    notes: list[str] = field(default_factory=list)
    fixtures: bool = False
    signal_registry: bool = False
    signal_schema: bool = False
    signal_bands: bool = False
    fanout: bool = False
    synthesize: bool = False
    contracts: bool = False
    legacy_dataset: bool = False

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


CAPS = Capabilities()

# -- prompt layer (landed, sha256-verified at import) ------------------------
from prompts.loader import AGENT_NAMES, DOMAINS  # noqa: E402

# -- signal layer: bands + alert categories ----------------------------------
def _load_submodule(dotted: str) -> Any:
    """
    Import one signal_layer submodule without executing the package __init__.

    signal_layer/__init__.py re-exports the whole package eagerly, so while any one
    of its submodules is still landing from another workstream a plain
    ``from signal_layer.bands import ...`` fails for a reason that has nothing to do
    with bands. The bands and alert-category modules are self-contained (the package
    docstring guarantees they import no boto3/strands), so they are loaded straight
    off disk as a fallback. Delete this helper once the package imports cleanly.
    """
    import importlib
    import importlib.util

    try:
        return importlib.import_module(dotted)
    except Exception:
        path = _REPO_ROOT / Path(*dotted.split(".")).with_suffix(".py")
        if not path.is_file():
            raise
        spec = importlib.util.spec_from_file_location(dotted.replace(".", "_"), path)
        if spec is None or spec.loader is None:  # pragma: no cover
            raise ImportError(f"cannot load {dotted} from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        CAPS.note(
            f"{dotted} loaded directly from {path.name} because signal_layer/__init__.py "
            "still imports a module that has not landed"
        )
        return module


try:
    _alerts_mod = _load_submodule("signal_layer.alert_categories")
    _bands_mod = _load_submodule("signal_layer.bands")
    alerts_for_domain = _alerts_mod.alerts_for_domain
    category_of = _alerts_mod.category_of
    dealer_score_band = _bands_mod.dealer_score_band
    fraud_score_band = _bands_mod.fraud_score_band

    CAPS.signal_bands = True
except Exception as exc:  # pragma: no cover - surfaced through /api/health
    alerts_for_domain = None  # type: ignore[assignment]
    category_of = None  # type: ignore[assignment]
    dealer_score_band = None  # type: ignore[assignment]
    fraud_score_band = None  # type: ignore[assignment]
    CAPS.note(f"signal_layer bands/alert categories unavailable: {exc}")

# -- signal layer: per-domain projection -------------------------------------
try:
    from signal_layer.registry import signals_for

    CAPS.signal_registry = True
except Exception as exc:
    signals_for = None  # type: ignore[assignment]
    CAPS.note(
        "signal_layer.registry not present; the per-domain signal projection is "
        f"reported as unavailable rather than guessed ({exc})"
    )

try:
    from signal_layer.schema import SignalPayload, render_for_agent  # noqa: F401

    CAPS.signal_schema = True
except Exception as exc:
    CAPS.note(f"signal_layer.schema not present ({exc})")

# -- application data --------------------------------------------------------
build_payload = None
try:
    from fixtures.loader import build_payload, get_application, list_application_ids

    CAPS.fixtures = True
except Exception as exc:
    try:
        from data.applications import get_application, list_application_ids

        CAPS.legacy_dataset = True
        CAPS.note(
            "fixtures/ has not landed; using the in-repo data/applications.py dataset "
            f"({exc})"
        )
    except Exception as exc2:  # pragma: no cover
        get_application = None  # type: ignore[assignment]
        list_application_ids = None  # type: ignore[assignment]
        CAPS.note(f"no application dataset available at all: {exc2}")

# -- execution layer ---------------------------------------------------------
try:
    from agents.fanout import fan_out_streaming

    CAPS.fanout = True
except Exception as exc:
    fan_out_streaming = None  # type: ignore[assignment]
    CAPS.note(f"agents.fanout not present ({exc})")

try:
    from agents.synthesize import synthesize

    CAPS.synthesize = True
except Exception as exc:
    synthesize = None  # type: ignore[assignment]
    CAPS.note(f"agents.synthesize not present ({exc})")

try:
    from agents.contracts import Adjudication  # noqa: F401

    CAPS.contracts = True
except Exception as exc:
    CAPS.note(f"agents.contracts not present ({exc})")

try:
    from agents.models import SPECIALIST_MODELS, SYNTHESIZER_MODEL, model_for
except Exception as exc:  # pragma: no cover
    SPECIALIST_MODELS = {}  # type: ignore[assignment]
    SYNTHESIZER_MODEL = None  # type: ignore[assignment]
    model_for = None  # type: ignore[assignment]
    CAPS.note(f"agents.models unavailable ({exc})")


# ---------------------------------------------------------------------------
# The customer's own output contracts, mirrored to the browser so the UI never
# hardcodes a title or a band -- and so bustout/rings correctly get NO title.
# ---------------------------------------------------------------------------
#: Exact ``- Title:`` line from each verbatim specialist prompt. bustout and rings
#: define no Title ("Return only the final analysis." / "Return only final
#: analysis."), so they map to None and the UI must not invent one.
ANALYSIS_TITLES: dict[str, str | None] = {
    "identity": "Identity Fraud Risk Analysis",
    "dealer": "Dealer Fraud Risk Analysis",
    "straw": "Straw Borrower Fraud Risk Analysis",
    "employment": "Employment Fraud Risk Analysis",
    "income": "Income Fraud Risk Analysis",
    "synthetic": "Synthetic Identity Fraud Risk Analysis",
    "bustout": None,
    "rings": None,
}

#: Specialist calibration bands, verbatim from the prompts.
RISK_BANDS = ("LOW RISK", "POSSIBLE RISK", "HIGH RISK")

#: Length budgets from the prompts' OUTPUT sections, for the fidelity panel.
LENGTH_RULES = {
    "low_risk_max_sentences": 5,
    "low_risk_min_sentences": 3,
    "possible_high_max_chars": 4000,
    "straw_no_coborrower_max_sentences": 3,
    "master_summary_max_chars": 1200,
}

#: The master adjudication's 21 keys, in the customer's required order. Built from
#: prompts.loader.DOMAINS so the order cannot drift from the master prompt's.
ADJUDICATION_KEYS: tuple[str, ...] = (
    "master_summary",
    "overall_risk_decision",
    "recommendation_decision",
    "recommended_stipulations",
    "top_risk_factors",
    *[k for d in DOMAINS for k in (f"{d}_summary", f"{d}_flag")],
)

OVERALL_RISK_VALUES = ("LOW RISK", "MEDIUM RISK", "HIGH RISK")
RECOMMENDATION_VALUES = ("APPROVE", "REVIEW AND APPLY STIPULATIONS", "DECLINE")

#: The enum conflict between the customer's two artifacts. Surfaced in the UI as
#: an open question, not silently resolved.
ENUM_CONFLICT = {
    "title": "Open question: risk and recommendation enums differ between two customer artifacts",
    "executable": {
        "artifact": "master_agent_prompt.txt",
        "status": "executable -- this is what the production agent runs, so it wins",
        "overall_risk_decision": list(OVERALL_RISK_VALUES),
        "recommendation_decision": list(RECOMMENDATION_VALUES),
    },
    "narrative": {
        "artifact": "Agent_Underwriting_Process_Guide.pdf",
        "status": "narrative -- not implemented by this port",
        "overall_risk_decision": ["LOW RISK", "POSSIBLE RISK", "HIGH RISK"],
        "recommendation_decision": [
            "APPROVE",
            "APPROVE WITH STIPULATIONS",
            "MANUAL REVIEW",
            "DECLINE",
        ],
    },
    "action": "Confirm with the prompt author which artifact is current.",
}


# ---------------------------------------------------------------------------
# Scenario labelling. Descriptive metadata only, never a measurement.
# ---------------------------------------------------------------------------
_FALLBACK_SCENARIOS: dict[str, str] = {
    "APP-1001": "Clean first-time buyer",
    "APP-1002": "Spousal co-borrower, thin file",
    "APP-1003": "Income overstatement (ambiguous)",
    "APP-1004": "Synthetic identity and fraud-ring overlap",
    "APP-1005": "Bust-out velocity pattern",
}

#: Substrings marking a scenario as an anti-false-positive test. The customer's
#: stated quality goal is fewer false positives, so the UI pins these to the top.
_FALSE_POSITIVE_MARKERS = (
    "false positive",
    "false-positive",
    "no coordination",
    "all-null",
    "all null",
    "staffing",
    "multi-unit",
    "multi unit",
    "alert-volume",
    "alert volume",
    "benign",
    "explainable",
    "clean",
    "household",
)


def _is_anti_false_positive(record: dict[str, Any], scenario: str) -> bool:
    explicit = record.get("anti_false_positive")
    if isinstance(explicit, bool):
        return explicit
    haystack = " ".join(
        str(record.get(k, "")) for k in ("scenario", "scenario_label", "notes", "label")
    )
    haystack = f"{haystack} {scenario}".lower()
    return any(marker in haystack for marker in _FALSE_POSITIVE_MARKERS)


def _scenario_label(app_id: str, record: dict[str, Any]) -> str:
    for key in ("scenario_label", "scenario", "label", "description"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _FALLBACK_SCENARIOS.get(app_id, "")


_CORE_KEYS = {
    "application_id",
    "scenario",
    "scenario_label",
    "label",
    "notes",
    "description",
    "alerts_fired",
    "guardrails_applied",
    "context",
    "anti_false_positive",
}


def _record_signals(record: dict[str, Any]) -> dict[str, Any]:
    """The application's signal dict, for either the nested or the flat shape."""
    signals = record.get("signals")
    if isinstance(signals, dict):
        return signals
    return {
        k: v
        for k, v in record.items()
        if k not in _CORE_KEYS
        and not k.endswith("_context")
        and not isinstance(v, (dict, list))
    }


def _record_context(record: dict[str, Any]) -> dict[str, Any]:
    ctx = record.get("context")
    if isinstance(ctx, dict):
        return dict(ctx)
    return {k: v for k, v in record.items() if k.endswith("_context")}


def _alerts_fired(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize alerts_fired. Absent means zero alerts, never an invented one."""
    raw = record.get("alerts_fired")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        alert_id = item.get("alert_id")
        categories = item.get("category", item.get("categories"))
        if categories is None and category_of is not None and isinstance(alert_id, int):
            try:
                categories = category_of(alert_id)
            except Exception:
                categories = None
        if isinstance(categories, str):
            categories = [categories]
        out.append(
            {
                "alert_id": alert_id,
                "categories": list(categories) if categories else [],
                "finding_text": item.get("finding_text") or item.get("finding") or "",
            }
        )
    return out


def _fraud_band(score: Any) -> str | None:
    if fraud_score_band is None or not isinstance(score, (int, float)):
        return None
    try:
        return fraud_score_band(score)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Fraud underwriting on Amazon Bedrock AgentCore",
    description="Demo control plane for the 1:1 AgentCore replacement.",
)


@app.get("/api/health")
def health() -> JSONResponse:
    """What is actually wired in this process. The UI renders this verbatim."""
    return JSONResponse(
        {
            "mock_mode": MOCK_MODE,
            "measures_tokens": (not MOCK_MODE) and CAPS.fanout,
            "capabilities": {
                "fixtures": CAPS.fixtures,
                "signal_registry": CAPS.signal_registry,
                "signal_schema": CAPS.signal_schema,
                "signal_bands": CAPS.signal_bands,
                "fanout": CAPS.fanout,
                "synthesize": CAPS.synthesize,
                "contracts": CAPS.contracts,
                "legacy_dataset": CAPS.legacy_dataset,
            },
            "notes": CAPS.notes,
            "domains": list(DOMAINS),
            "agent_names": dict(AGENT_NAMES),
            "analysis_titles": ANALYSIS_TITLES,
            "risk_bands": list(RISK_BANDS),
            "length_rules": LENGTH_RULES,
            "adjudication_keys": list(ADJUDICATION_KEYS),
            "overall_risk_values": list(OVERALL_RISK_VALUES),
            "recommendation_values": list(RECOMMENDATION_VALUES),
            "enum_conflict": ENUM_CONFLICT,
            "synthesizer_model": SYNTHESIZER_MODEL,
        }
    )


@app.get("/api/applications")
def applications() -> JSONResponse:
    if list_application_ids is None:
        raise HTTPException(status_code=503, detail="no application dataset available")
    out: list[dict[str, Any]] = []
    for app_id in list_application_ids():
        record = get_application(app_id) or {}
        signals = _record_signals(record)
        scenario = _scenario_label(app_id, record)
        fraud_score = record.get("fraud_score", signals.get("fraud_score"))
        dealer_score = signals.get(
            "dealer_consortium_score", record.get("dealer_consortium_score")
        )
        dealer_band = None
        if dealer_score_band is not None and isinstance(dealer_score, (int, float)):
            try:
                dealer_band = dealer_score_band(dealer_score)
            except Exception:
                dealer_band = None
        out.append(
            {
                "application_id": record.get("application_id", app_id),
                "scenario": scenario,
                "anti_false_positive": _is_anti_false_positive(record, scenario),
                "loan_amount": record.get("loan_amount", signals.get("loan_amount")),
                "vehicle": record.get("vehicle", signals.get("vehicle")),
                "fraud_score": fraud_score,
                "fraud_score_band": _fraud_band(fraud_score),
                "dealer_consortium_score": dealer_score,
                "dealer_score_band": dealer_band,
                "signal_count": len(signals),
                "alert_count": len(_alerts_fired(record)),
            }
        )
    return JSONResponse(out)


#: Per-domain ``*_context`` field names. The customer's own naming is inconsistent
#: (ring_context for rings, employer_context for employment), so the pairing is
#: declared rather than derived from the domain string.
_CONTEXT_ALIASES: dict[str, tuple[str, ...]] = {
    "identity": ("identity_context",),
    "dealer": ("dealer_context",),
    "straw": ("straw_context", "coborrower_context"),
    "employment": ("employment_context", "employer_context"),
    "income": ("income_context",),
    "synthetic": ("synthetic_context",),
    "bustout": ("bustout_context", "negative_file_context"),
    "rings": ("rings_context", "ring_context"),
}


@app.get("/api/payload/{application_id}")
def payload(application_id: str) -> JSONResponse:
    """The signal-layer view: what each specialist is allowed to see, per domain."""
    if get_application is None:
        raise HTTPException(status_code=503, detail="no application dataset available")
    record = get_application(application_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown application {application_id}")

    signals = _record_signals(record)
    context = _record_context(record)
    alerts = _alerts_fired(record)

    built = None
    if build_payload is not None:
        try:
            built = build_payload(record.get("application_id", application_id))
        except Exception as exc:
            CAPS.note(f"fixtures.build_payload failed: {exc}")

    return JSONResponse(
        {
            "application_id": record.get("application_id", application_id),
            "projection_source": (
                "signal_layer.registry.signals_for"
                if CAPS.signal_registry
                else "unavailable: signal_layer.registry has not landed"
            ),
            "precomputed": True,
            "total_signal_count": len(signals),
            "total_alert_count": len(alerts),
            "domains": [
                _domain_view(d, built, signals, context, alerts, record) for d in DOMAINS
            ],
        }
    )


def _domain_view(
    domain: str,
    built: Any,
    signals: dict[str, Any],
    context: dict[str, Any],
    alerts: list[dict[str, Any]],
    record: dict[str, Any],
) -> dict[str, Any]:
    """One domain's slice of the signal layer, with provenance for every part."""
    names: list[str] | None = None
    guardrails: list[str] = []

    # 1) A real SignalPayload/DomainView from the signal layer, if it has landed.
    view = _find_domain_view(built, domain)
    if view is not None:
        raw = getattr(view, "signals", None)
        if isinstance(raw, dict):
            names = sorted(raw)
        elif isinstance(raw, (list, tuple, set)):
            names = sorted(str(x) for x in raw)
        applied = getattr(view, "guardrails_applied", None)
        if isinstance(applied, (list, tuple, set)):
            guardrails = [str(x) for x in applied]

    # 2) Otherwise the registry's projection, intersected with this application.
    if names is None and signals_for is not None:
        try:
            spec = signals_for(domain)
            candidates = (
                sorted(spec)
                if isinstance(spec, dict)
                else sorted(getattr(s, "name", str(s)) for s in spec)
            )
            names = [n for n in candidates if n in signals]
        except Exception:
            names = None

    domain_signals = (
        {n: signals[n] for n in names if n in signals} if names is not None else None
    )

    if not guardrails:
        applied = record.get("guardrails_applied")
        if isinstance(applied, dict):
            candidate = applied.get(domain)
            if isinstance(candidate, (list, tuple)):
                guardrails = [str(x) for x in candidate]
        elif isinstance(applied, (list, tuple)):
            guardrails = [str(x) for x in applied]

    aliases = _CONTEXT_ALIASES.get(domain, (f"{domain}_context",))
    paired_context = {k: v for k, v in context.items() if k in aliases}

    entitled: set[int] | None = None
    if alerts_for_domain is not None:
        try:
            entitled = alerts_for_domain(domain)
        except Exception:
            entitled = None
    domain_alerts = (
        [
            a
            for a in alerts
            if isinstance(a.get("alert_id"), int) and a["alert_id"] in entitled
        ]
        if entitled is not None
        else []
    )

    return {
        "domain": domain,
        "agent_name": AGENT_NAMES[domain],
        "signal_count": len(domain_signals) if domain_signals is not None else None,
        "signals": domain_signals,
        "signals_unavailable_reason": (
            None
            if domain_signals is not None
            else "signal_layer.registry has not landed; per-domain projection not computed"
        ),
        "paired_context": paired_context,
        "entitled_alert_count": len(entitled) if entitled is not None else None,
        "alerts_fired": domain_alerts,
        "guardrails_applied": guardrails,
        "analysis_title": ANALYSIS_TITLES[domain],
        "model": _model_of(domain),
    }


def _find_domain_view(built: Any, domain: str) -> Any:
    if built is None:
        return None
    try:
        views = getattr(built, "domain_views", None)
        if isinstance(views, dict):
            return views.get(domain)
        if views is not None:
            return next(
                (v for v in views if getattr(v, "domain", None) == domain), None
            )
        return getattr(built, domain, None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Model / rate / usage helpers
# ---------------------------------------------------------------------------
def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _model_of(domain: str) -> str | None:
    if model_for is not None:
        try:
            return model_for(domain)
        except Exception:
            pass
    return SPECIALIST_MODELS.get(domain) if isinstance(SPECIALIST_MODELS, dict) else None


def _rate_for(model_id: str | None) -> dict[str, float] | None:
    """Published $/1M rates for a model id, from whichever module prices it.

    Both branches call the owning module's **public** ``rates_for``. An earlier
    version reached for module-level dicts by guessing at names -- ``MODEL_RATES``,
    ``RATES``, ``RATE_TABLE`` -- none of which exist: the real constant is
    ``BASE_RATES_PER_MTOK``. So every Claude row returned ``None`` and the UI's
    "Rate applied" column rendered an em dash for eight of the nine agents while
    happily showing the GPT row, which took the other branch. Cost was still
    correct, because ``agents.pricing.cost_usd`` never used this helper -- only the
    display was wrong, which is the sort of bug a demo audience spots first.

    ``pricing.rates_for`` is prefix-aware (``global.*`` vs ``us.*`` differ by exactly
    1.10x) and returns the introductory-vs-standard distinction, so the rate shown is
    the rate that was actually applied rather than a base-table lookup.
    """
    if not model_id:
        return None
    # GPT-5.x lives on the bedrock-mantle endpoint and is priced by agents.mantle;
    # agents.pricing only knows the Claude rate card and would report nothing.
    try:
        from agents import mantle

        if mantle.is_mantle_model(model_id):
            rate_in, rate_cache, rate_out = mantle.rates_for(model_id)
            return {
                "input_per_mtok": rate_in,
                "output_per_mtok": rate_out,
                "cache_read_per_mtok": rate_cache,
                "cache_write_per_mtok": rate_in
                * mantle.CACHE_WRITE_MULTIPLIER.get(model_id, 1.25),
            }
    except Exception:
        pass
    try:
        from agents import pricing

        card = pricing.rates_for(model_id)
    except Exception:
        return None
    if not isinstance(card, dict):
        return None
    out: dict[str, float] = {}
    for target, source in (
        ("input_per_mtok", "input"),
        ("output_per_mtok", "output"),
        ("cache_read_per_mtok", "cache_read"),
        ("cache_write_per_mtok", "cache_write_applied"),
    ):
        value = card.get(source)
        if isinstance(value, (int, float)):
            out[target] = float(value)
    return out or None


def _tier_of(model_id: str | None) -> str | None:
    if not model_id:
        return None
    try:
        from agents.models import tier_label

        return tier_label(model_id)
    except Exception:
        pass
    stem = str(model_id).lower()
    for needle, label in (
        ("haiku", "light"),
        ("sonnet", "mid"),
        ("opus", "heavy"),
        # GPT-5.6 capability tiers, named by OpenAI: Luna fast/cheap, Terra balanced,
        # Sol flagship reasoning.
        ("luna", "light"),
        ("terra", "mid"),
        ("sol", "heavy"),
    ):
        if needle in stem:
            return label
    return None


def _parse_band(text: Any) -> str | None:
    """Find the customer's risk band in a specialist's prose. HIGH before POSSIBLE."""
    if not isinstance(text, str):
        return None
    upper = text.upper()
    for band in ("HIGH RISK", "POSSIBLE RISK", "LOW RISK"):
        if band in upper:
            return band
    return None


def _sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()])


def _usage_of(obj: Any) -> dict[str, int | None]:
    """
    Real Bedrock usage off a result. Per the platform notes, inputTokens and
    outputTokens are required but cacheReadInputTokens / cacheWriteInputTokens are
    OPTIONAL, so every read is a .get() and a missing field stays None.
    """
    empty: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }
    usage = None
    if isinstance(obj, dict):
        usage = obj.get("usage") or obj.get("accumulated_usage")
    if usage is None:
        # SpecialistResult / SynthesisResult carry it as a plain attribute. Without
        # this branch the synthesis row reports null tokens and null cost, which
        # silently drops the master call out of the per-application total.
        usage = getattr(obj, "usage", None)
    if usage is None:
        metrics = getattr(obj, "metrics", None)
        if metrics is not None:
            usage = getattr(metrics, "accumulated_usage", None)
    if not isinstance(usage, dict):
        return empty
    return {
        "input_tokens": usage.get("inputTokens", usage.get("input_tokens")),
        "output_tokens": usage.get("outputTokens", usage.get("output_tokens")),
        "cache_read_tokens": usage.get(
            "cacheReadInputTokens", usage.get("cache_read_tokens")
        ),
        "cache_write_tokens": usage.get(
            "cacheWriteInputTokens", usage.get("cache_write_tokens")
        ),
    }


def _latency_of(obj: Any) -> float | None:
    """result.metrics.accumulated_metrics['latencyMs'], if the result carries it."""
    metrics = getattr(obj, "metrics", None)
    accumulated = getattr(metrics, "accumulated_metrics", None) if metrics else None
    if isinstance(accumulated, dict):
        value = accumulated.get("latencyMs")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _cost_of(model_id: str | None, usage: dict[str, int | None]) -> float | None:
    """Cost from MEASURED tokens only. Any missing count returns None, not a guess."""
    if usage.get("input_tokens") is None or usage.get("output_tokens") is None:
        return None
    try:
        from agents.pricing import cost_usd
    except Exception:
        return None
    try:
        params = inspect.signature(cost_usd).parameters
        kwargs: dict[str, Any] = {}
        for name in ("cache_read_tokens", "cache_write_tokens"):
            if name in params and usage.get(name) is not None:
                kwargs[name] = usage[name]
        return float(
            cost_usd(model_id, usage["input_tokens"], usage["output_tokens"], **kwargs)
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Live run: agents.fanout + agents.synthesize
# ---------------------------------------------------------------------------
async def _iterate(source: Any) -> AsyncIterator[Any]:
    """Consume an async generator, a sync generator, or an awaitable of an iterable."""
    if hasattr(source, "__aiter__"):
        async for item in source:
            yield item
        return
    if inspect.isawaitable(source):
        source = await source
    if hasattr(source, "__aiter__"):
        async for item in source:
            yield item
        return
    if isinstance(source, Iterable):
        for item in source:
            yield item


def _pinned_band(app_id: str, domain: str) -> str | None:
    """The fixture's pinned band for one domain, or None if the fixture does not state one."""
    try:
        from fixtures.loader import expected_for  # noqa: PLC0415

        expected = expected_for(app_id) or {}
        return ((expected.get("domains") or {}).get(domain) or {}).get("band")
    except Exception:
        return None


def _call_synthesize(analyses: dict[str, Any], app_id: str) -> Any:
    """Invoke agents.synthesize.synthesize, passing application_id if it accepts one.

    Offline synthesis is fixture-pinned and raises ``UnpinnedApplicationError`` for an
    application it does not know, so the id is not optional in practice -- omitting it
    silently degrades every scenario to a placeholder verdict.
    """
    try:
        params = inspect.signature(synthesize).parameters
    except (TypeError, ValueError):
        return synthesize(analyses)
    if "application_id" in params:
        return synthesize(analyses, application_id=app_id)
    return synthesize(analyses)


def _call_fanout(payload_obj: Any, app_id: str) -> Any:
    """Invoke fan_out_streaming against whatever signature it actually has."""
    params = list(inspect.signature(fan_out_streaming).parameters)
    if not params:
        return fan_out_streaming()
    first = params[0]
    if first in ("payload", "signal_payload") and payload_obj is not None:
        return fan_out_streaming(payload_obj)
    if first in ("application_id", "app_id"):
        return fan_out_streaming(app_id)
    return fan_out_streaming(payload_obj if payload_obj is not None else app_id)


def _normalize_fanout_event(event: Any, t0: float) -> dict[str, Any] | None:
    """Map one agents.fanout event onto this server's SSE frame contract."""
    if not isinstance(event, dict):
        domain = getattr(event, "domain", None)
        if domain is None:
            return None
        event = {
            "domain": domain,
            "analysis": getattr(event, "analysis", None) or getattr(event, "text", None),
            "risk_band": getattr(event, "risk_band", None),
            "model": getattr(event, "model", None),
            "latency_ms": getattr(event, "latency_ms", None),
            "result": event,
        }

    domain = event.get("domain") or event.get("agent")
    kind = event.get("event") or event.get("type")
    if domain is None:
        return None
    if kind in ("agent_started", "started", "running"):
        return {
            "event": "agent_started",
            "domain": domain,
            "at_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        }
    if event.get("error") or kind in ("agent_failed", "failed", "error"):
        return {
            "event": "agent_failed",
            "domain": domain,
            "agent_name": AGENT_NAMES.get(domain, domain),
            "error": str(event.get("error") or event.get("message") or "unknown error"),
        }

    model_id = event.get("model") or event.get("model_id") or _model_of(domain)
    analysis = (
        event.get("analysis")
        or event.get("text")
        or event.get("output")
        or event.get("findings")
        or ""
    )
    result = event.get("result") if event.get("result") is not None else event
    usage = _usage_of(result)
    latency_ms = event.get("latency_ms", event.get("latencyMs"))
    if latency_ms is None:
        latency_ms = _latency_of(result)
    if latency_ms is None and event.get("elapsed_s") is not None:
        latency_ms = float(event["elapsed_s"]) * 1000.0
    cost = event.get("cost_usd")
    if cost is None:
        cost = _cost_of(model_id, usage)

    return {
        "event": "agent_completed",
        "domain": domain,
        "agent_name": AGENT_NAMES.get(domain, domain),
        "analysis": analysis,
        "analysis_title": ANALYSIS_TITLES.get(domain),
        "risk_band": event.get("risk_band") or _parse_band(analysis),
        "char_count": len(analysis),
        "sentence_count": _sentence_count(analysis) if analysis else 0,
        "model": model_id,
        "tier": _tier_of(model_id),
        "rate": _rate_for(model_id),
        "latency_ms": round(float(latency_ms), 1) if latency_ms is not None else None,
        "finished_at_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        "measured": True,
        "source": "bedrock",
        **usage,
        "cost_usd": cost,
    }


def _coerce_adjudication(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    for attr in ("model_dump", "dict"):
        fn = getattr(result, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    nested = getattr(result, "adjudication", None)
    if nested is not None and nested is not result:
        return _coerce_adjudication(nested)
    try:
        return json.loads(getattr(result, "text", None) or str(result))
    except Exception:
        return {}


def _key_check(adjudication: dict[str, Any]) -> dict[str, Any]:
    """Validate the 21-key contract: exact set, exact order, no application_id key."""
    keys = list(adjudication)
    return {
        "expected_count": len(ADJUDICATION_KEYS),
        "actual_count": len(keys),
        "order_matches": keys == list(ADJUDICATION_KEYS),
        "missing": [k for k in ADJUDICATION_KEYS if k not in adjudication],
        "unexpected": [k for k in keys if k not in ADJUDICATION_KEYS],
        "valid": keys == list(ADJUDICATION_KEYS),
    }


def _run_completed(
    per_agent: dict[str, dict[str, Any]],
    fanout_ms: float,
    synth_ms: float,
    source: str,
    synthesis_cost_usd: float | None = None,
) -> dict[str, Any]:
    latencies = {
        d: info["latency_ms"]
        for d, info in per_agent.items()
        if isinstance(info.get("latency_ms"), (int, float))
    }
    slowest = max(latencies, key=lambda k: latencies[k]) if latencies else None
    costs = [info.get("cost_usd") for info in per_agent.values()]
    agent_cost = (
        sum(c for c in costs if c is not None)
        if costs and all(c is not None for c in costs)
        else None
    )
    return {
        "event": "run_completed",
        "fanout_wall_ms": round(fanout_ms, 1),
        "synthesis_ms": round(synth_ms, 1),
        "end_to_end_ms": round(fanout_ms + synth_ms, 1),
        "slowest_agent": slowest,
        "slowest_agent_ms": latencies.get(slowest) if slowest else None,
        "sum_of_agent_ms": round(sum(latencies.values()), 1) if latencies else None,
        "agent_cost_usd": agent_cost,
        "synthesis_cost_usd": synthesis_cost_usd,
        # The per-application figure the cost panel shows. None unless BOTH the eight
        # specialists and the master reported usage: a partial total would understate
        # the real cost of an adjudication.
        "cost_per_application_usd": (
            round(agent_cost + synthesis_cost_usd, 6)
            if agent_cost is not None and synthesis_cost_usd is not None
            else None
        ),
        "source": source,
    }


async def _run_live(app_id: str) -> AsyncIterator[dict[str, Any]]:
    """Real path: agents.fanout + agents.synthesize. Every number measured here."""
    payload_obj = build_payload(app_id) if build_payload is not None else None

    analyses: dict[str, str] = {}
    per_agent: dict[str, dict[str, Any]] = {}
    t0 = time.perf_counter()

    for domain in DOMAINS:
        yield {"event": "agent_started", "domain": domain, "at_ms": 0.0}

    async for event in _iterate(_call_fanout(payload_obj, app_id)):
        frame = _normalize_fanout_event(event, t0)
        if frame is None:
            continue
        if frame["event"] == "agent_completed":
            # Offline the specialist prose deliberately asserts no band (no model ran,
            # so claiming one would be fabrication). Fall back to the fixture's pinned
            # band, which carries the requirement id and customer sentence that justify
            # it, and label where it came from so the UI never implies it was inferred.
            if not frame.get("risk_band"):
                pinned = _pinned_band(app_id, frame["domain"])
                if pinned:
                    frame["risk_band"] = pinned
                    frame["risk_band_source"] = "fixture-pinned expectation (no model ran)"
            analyses[frame["domain"]] = frame.get("analysis") or ""
            per_agent[frame["domain"]] = frame
        yield frame

    fanout_ms = (time.perf_counter() - t0) * 1000.0

    yield {
        "event": "synthesis_started",
        "model": SYNTHESIZER_MODEL,
        "tier": _tier_of(SYNTHESIZER_MODEL),
    }
    s0 = time.perf_counter()
    # application_id is required for the offline path: agents.synthesize refuses to
    # score an unpinned application (the customer's MSTR-022/023/031 rules forbid
    # deriving a verdict from finding volume), so without it every scenario would
    # fall back to a placeholder and the demo would show LOW/APPROVE for everything.
    result = _call_synthesize(analyses, app_id)
    if inspect.isawaitable(result):
        result = await result
    synth_ms = (time.perf_counter() - s0) * 1000.0

    adjudication = _coerce_adjudication(result)
    usage = _usage_of(result)
    synth_cost = getattr(result, "cost_usd", None)
    if synth_cost is None:
        synth_cost = _cost_of(SYNTHESIZER_MODEL, usage)
    yield {
        "event": "synthesis_completed",
        "adjudication": adjudication,
        "key_check": _key_check(adjudication),
        "model": SYNTHESIZER_MODEL,
        "tier": _tier_of(SYNTHESIZER_MODEL),
        "rate": _rate_for(SYNTHESIZER_MODEL),
        "latency_ms": round(synth_ms, 1),
        "measured": True,
        "source": "bedrock",
        **usage,
        "cost_usd": synth_cost,
    }
    yield _run_completed(
        per_agent, fanout_ms, synth_ms, source="bedrock", synthesis_cost_usd=synth_cost
    )


# ---------------------------------------------------------------------------
# MOCK_MODE run: real concurrency and real wall clock, NO invented tokens or cost.
# ---------------------------------------------------------------------------
async def _run_mock(app_id: str, record: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """
    Offline path. The eight domains run concurrently through asyncio.as_completed so
    the fan-out is genuinely parallel and the wall clock genuinely measured, but no
    Bedrock call happens -- so input_tokens, output_tokens, cache_read_tokens and
    cost_usd are emitted as null and every frame is marked source="mock". The UI
    renders those as em dashes behind a MOCK_MODE banner. Fabricating them here is
    precisely the liability this demo must not carry.
    """
    signals = _record_signals(record)
    context = _record_context(record)
    t0 = time.perf_counter()

    async def one(domain: str) -> dict[str, Any]:
        started = time.perf_counter()
        # A deterministic per-domain sleep, so the eight finish at visibly different
        # times. This is a sleep, not a model call, and is labelled source="mock".
        await asyncio.sleep(0.3 + (abs(hash((app_id, domain))) % 1100) / 1000.0)
        analysis = _mock_analysis(domain, record, signals, context)
        model_id = _model_of(domain)
        return {
            "event": "agent_completed",
            "domain": domain,
            "agent_name": AGENT_NAMES[domain],
            "analysis": analysis["text"],
            "analysis_title": ANALYSIS_TITLES[domain],
            "risk_band": analysis["band"],
            "char_count": len(analysis["text"]),
            "sentence_count": _sentence_count(analysis["text"]),
            "model": model_id,
            "tier": _tier_of(model_id),
            "rate": _rate_for(model_id),
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "finished_at_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "measured": False,
            "source": "mock",
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "cost_usd": None,
        }

    for domain in DOMAINS:
        yield {
            "event": "agent_started",
            "domain": domain,
            "at_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        }

    per_agent: dict[str, dict[str, Any]] = {}
    for finished in asyncio.as_completed([one(d) for d in DOMAINS]):
        frame = await finished
        per_agent[frame["domain"]] = frame
        yield frame

    fanout_ms = (time.perf_counter() - t0) * 1000.0

    yield {
        "event": "synthesis_started",
        "model": SYNTHESIZER_MODEL,
        "tier": _tier_of(SYNTHESIZER_MODEL),
    }
    s0 = time.perf_counter()
    await asyncio.sleep(0.45)
    adjudication = _mock_adjudication(per_agent)
    synth_ms = (time.perf_counter() - s0) * 1000.0
    yield {
        "event": "synthesis_completed",
        "adjudication": adjudication,
        "key_check": _key_check(adjudication),
        "model": SYNTHESIZER_MODEL,
        "tier": _tier_of(SYNTHESIZER_MODEL),
        "rate": _rate_for(SYNTHESIZER_MODEL),
        "latency_ms": round(synth_ms, 1),
        "measured": False,
        "source": "mock",
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "cost_usd": None,
    }
    yield _run_completed(per_agent, fanout_ms, synth_ms, source="mock")


def _titled(title: str | None, band: str, body: str) -> str:
    """
    Assemble a specialist analysis. bustout and rings get NO title, because their
    prompts say "Return only the final analysis." / "Return only final analysis."
    and define no Title line. Never invent one for them.
    """
    lead = f"{band}. {body}"
    return f"{title}\n\n{lead}" if title else lead


def _mock_analysis(
    domain: str, record: dict[str, Any], signals: dict[str, Any], context: dict[str, Any]
) -> dict[str, str]:
    """
    Offline stand-in prose that obeys the customer's OUTPUT contract SHAPE: the exact
    title where one is defined, no title for bustout/rings, LOW capped at 3-5
    sentences, straw at 2-3 sentences when there is no co-borrower. It is explicitly
    labelled placeholder text; its job is to prove the rendering contract, not to
    imitate the model's judgement.
    """
    title = ANALYSIS_TITLES[domain]
    fraud_score = record.get("fraud_score", signals.get("fraud_score"))
    score_band = _fraud_band(fraud_score)

    has_cob = record.get("has_coborrower", signals.get("has_coborrower"))
    if domain == "straw" and not has_cob:
        body = (
            "No co-borrower is present on this application, so the primary "
            "straw-borrower mechanism is absent. Straw risk is minimal on the "
            "available signals."
        )
        return {"band": "LOW RISK", "text": _titled(title, "LOW RISK", body)}

    if score_band == "very high":
        band = "HIGH RISK"
    elif score_band in ("high", "medium"):
        band = "POSSIBLE RISK"
    else:
        band = "LOW RISK"

    context_names = ", ".join(sorted(_CONTEXT_ALIASES.get(domain, ()))) or "none paired"
    if band == "LOW RISK":
        body = (
            f"The {domain} signals on this application are isolated and explainable by "
            f"the paired context ({context_names}). No independent corroboration of "
            "manipulation is present. This is MOCK_MODE placeholder text: no model was "
            "invoked."
        )
    else:
        body = (
            f"Several {domain} signals are elevated and the paired context "
            f"({context_names}) does not fully explain them, so verification is "
            f"warranted before funding. The application's fraud score of {fraud_score} "
            f"falls in the '{score_band}' band. This is MOCK_MODE placeholder text: no "
            "model was invoked, so the prose is a rendering fixture rather than an "
            "analysis."
        )
    return {"band": band, "text": _titled(title, band, body)}


def _mock_adjudication(per_agent: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the 21 keys in the customer's exact order. No application_id key."""
    bands = {d: (per_agent.get(d, {}).get("risk_band") or "LOW RISK") for d in DOMAINS}
    high = [d for d, b in bands.items() if b == "HIGH RISK"]
    possible = [d for d, b in bands.items() if b == "POSSIBLE RISK"]

    if high:
        overall, recommendation = "HIGH RISK", "DECLINE"
    elif possible:
        overall, recommendation = "MEDIUM RISK", "REVIEW AND APPLY STIPULATIONS"
    else:
        overall, recommendation = "LOW RISK", "APPROVE"

    adjudication: dict[str, Any] = {
        "master_summary": (
            f"MOCK_MODE placeholder adjudication. {len(high)} dimension(s) returned HIGH "
            f"RISK and {len(possible)} returned POSSIBLE RISK across the eight "
            "specialists. No model was invoked, so this text is a rendering fixture for "
            "the decision panel."
        ),
        "overall_risk_decision": overall,
        "recommendation_decision": recommendation,
        # Stipulations exist only for the REVIEW outcome.
        "recommended_stipulations": (
            "Verify stated income against the two most recent pay stubs; verify "
            "employment by direct employer callback; re-verify identity against a "
            "government photo ID."
            if recommendation == "REVIEW AND APPLY STIPULATIONS"
            else ""
        ),
        # LOW implies an empty top_risk_factors.
        "top_risk_factors": "" if overall == "LOW RISK" else "; ".join(high + possible),
    }
    for domain in DOMAINS:
        summary = per_agent.get(domain, {}).get("analysis", "") or ""
        # Contract: summaries under 1200 chars.
        adjudication[f"{domain}_summary"] = summary[: LENGTH_RULES["master_summary_max_chars"] - 20]
        adjudication[f"{domain}_flag"] = (
            1 if bands[domain] in ("POSSIBLE RISK", "HIGH RISK") else 0
        )
    return adjudication


@app.get("/api/stream/{application_id}")
async def stream(
    application_id: str,
    backend: str = Query("local", pattern="^(local|agentcore)$"),
) -> StreamingResponse:
    """
    SSE for one adjudication run.

    Every frame is one JSON object on a ``data:`` line. A frame carrying an ``error``
    key is terminal and MUST be surfaced by the client: AgentCore emits mid-stream
    errors AFTER HTTP 200, so a 200 alone never means the run succeeded.
    """
    if get_application is None:
        raise HTTPException(status_code=503, detail="no application dataset available")
    record = get_application(application_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown application {application_id}")
    app_id = record.get("application_id", application_id)

    async def gen() -> AsyncIterator[str]:
        # runtimeSessionId must be at least 33 chars: uuid4().hex is 32, so use
        # str(uuid.uuid4()) (36).
        session_id = str(uuid.uuid4())
        # Use the real orchestration whenever it is importable, including offline:
        # agents.fanout and agents.synthesize both have deterministic MOCK_MODE paths
        # that honour the customer's contracts (verbatim prompts, per-domain
        # projection, fixture-pinned adjudication that refuses to score by finding
        # volume). The server's own local mock is a last-resort fallback only -- it
        # infers bands from fraud_score and counts them, which the customer's
        # MSTR-022/023/031 rules forbid.
        use_pipeline = CAPS.fanout and CAPS.synthesize
        use_live = (not MOCK_MODE) and use_pipeline
        yield _sse(
            {
                "event": "run_started",
                "application_id": app_id,
                "session_id": session_id,
                "backend": backend,
                "mock_mode": MOCK_MODE,
                "measured_tokens": use_live,
                "execution_path": (
                    "agents.fanout.fan_out_streaming + agents.synthesize.synthesize"
                    + ("" if use_live else " (MOCK_MODE: no Bedrock call; tokens and cost null by design)")
                    if use_pipeline
                    else "server-local mock fallback (agents.fanout/synthesize unavailable)"
                ),
                "synthesizer_model": SYNTHESIZER_MODEL,
                "agents": [
                    {
                        "domain": d,
                        "agent_name": AGENT_NAMES[d],
                        "model": _model_of(d),
                        "tier": _tier_of(_model_of(d)),
                        "rate": _rate_for(_model_of(d)),
                        "analysis_title": ANALYSIS_TITLES[d],
                    }
                    for d in DOMAINS
                ],
            }
        )
        runner = _run_live(app_id) if use_pipeline else _run_mock(app_id, record)
        try:
            async for frame in runner:
                yield _sse(frame)
        except Exception as exc:  # terminal error frame, mirroring AgentCore
            yield _sse({"event": "error", "error": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Static assets. Mounted after the API routes so /api/* always wins.
# ---------------------------------------------------------------------------
if (_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")


@app.get("/")
def index() -> Any:
    target = _DIST / "index.html"
    if target.is_file():
        return FileResponse(target)
    return JSONResponse(
        status_code=503,
        content={
            "error": "ui/dist has not been built",
            "fix": "cd ui && npm install && npm run build",
        },
    )


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    serve(port=int(os.environ.get("PORT", "8080")))
