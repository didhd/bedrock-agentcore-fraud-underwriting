"""
Offline smoke tests for the fraud demo (MOCK_MODE, no Bedrock calls).

Run:  MOCK_MODE=1 python -m pytest tests/ -q
  or: MOCK_MODE=1 python tests/test_local.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MOCK_MODE", "1")

from agents.orchestrator import analyze, adjudicate, DOMAINS
from agents.models import SPECIALIST_MODELS, LIGHT_MODEL, HEAVY_MODEL, model_for
from tools.application_data import available_application_ids, fetch_application_record


def test_dataset_loads():
    ids = available_application_ids()
    assert ids == ["APP-1001", "APP-1002", "APP-1003", "APP-1004", "APP-1005"]
    for app_id in ids:
        rec = fetch_application_record(app_id)
        assert rec is not None
        assert "signals" in rec and "context" in rec


def test_clean_app_approves():
    out = json.loads(analyze("Run a full underwriting review of APP-1001"))
    assert out["overall_risk_decision"] == "LOW RISK"
    assert out["recommendation_decision"] == "APPROVE"


def test_synthetic_ring_declines():
    out = json.loads(analyze("Run a full underwriting review of APP-1004"))
    assert out["overall_risk_decision"] == "HIGH RISK"
    assert out["recommendation_decision"] == "DECLINE"
    assert out["synthetic_flag"] == 1
    assert out["rings_flag"] == 1


def test_income_case_reviews():
    out = json.loads(analyze("Run a full underwriting review of APP-1003"))
    assert out["recommendation_decision"] == "REVIEW AND APPLY STIPULATIONS"


def test_targeted_routing():
    out = analyze("Is there income fraud on APP-1003?")
    assert "income agent" in out.lower()
    assert "APP-1003" in out


def test_per_agent_model_config():
    # simple specialists use the light tier, complex ones use heavy
    assert model_for("straw") == LIGHT_MODEL
    assert model_for("dealer") == LIGHT_MODEL
    assert model_for("synthetic") == HEAVY_MODEL
    assert model_for("rings") == HEAVY_MODEL
    assert set(SPECIALIST_MODELS.keys()) == set(DOMAINS)


def test_parallel_pipeline_timing():
    res = adjudicate("APP-1004")
    assert set(res["verdicts"].keys()) == set(DOMAINS)
    t = res["timing"]
    # parallel per-app latency must beat sequential and be under a minute
    assert t["nominal_parallel_per_app_s"] < t["nominal_sequential_per_app_s"]
    assert t["nominal_parallel_per_app_s"] < 60
    # each agent reports the model it ran on
    assert res["per_agent"]["synthetic"]["tier"].startswith("heavy")


if __name__ == "__main__":
    test_dataset_loads()
    test_clean_app_approves()
    test_synthetic_ring_declines()
    test_income_case_reviews()
    test_targeted_routing()
    test_per_agent_model_config()
    test_parallel_pipeline_timing()
    print("All offline smoke tests passed.")
