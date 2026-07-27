"""
Per-agent telemetry to Amazon CloudWatch.

Publishes spend, tokens, and latency per agent so the CloudWatch dashboard can
break down cost by agent (the "who is spending what" view the demo closes on).

Emission is OFF by default and only runs when EMIT_CLOUDWATCH_METRICS=1 and
boto3 + AWS credentials are available, so offline/mock runs never touch AWS.
Enable it for the live demo:

    export EMIT_CLOUDWATCH_METRICS=1
    export AWS_REGION=us-east-1

Namespace defaults to AgentCoreFraudDemo (override with METRICS_NAMESPACE).
Each per-agent datapoint carries an `Agent` dimension.
"""

from __future__ import annotations
import os
from datetime import datetime, timezone

NAMESPACE = os.environ.get("METRICS_NAMESPACE", "AgentCoreFraudDemo")


def _enabled() -> bool:
    return os.environ.get("EMIT_CLOUDWATCH_METRICS", "0") == "1"


def _client():
    import boto3  # imported lazily so the demo has no hard boto3 dependency offline
    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("cloudwatch", region_name=region)


def emit_adjudication(application_id: str, per_agent: dict, cost: dict,
                      timing: dict, decision: str) -> bool:
    """
    Publish per-agent + pipeline metrics for one adjudication.

    per_agent: {agent_name: {model, tier, input_tokens, output_tokens,
                             cost_usd, elapsed_s|nominal_s}}
    Returns True if metrics were sent, False if disabled or on error.
    """
    if not _enabled():
        return False
    try:
        cw = _client()
    except Exception:
        return False

    ts = datetime.now(timezone.utc)
    data: list[dict] = []

    for agent, info in per_agent.items():
        dims = [{"Name": "Agent", "Value": agent},
                {"Name": "Tier", "Value": str(info.get("tier", "unknown"))}]
        latency = float(info.get("elapsed_s") or info.get("nominal_s") or 0.0)
        data += [
            {"MetricName": "CostUSD", "Dimensions": dims, "Timestamp": ts,
             "Value": float(info.get("cost_usd", 0.0)), "Unit": "None"},
            {"MetricName": "InputTokens", "Dimensions": dims, "Timestamp": ts,
             "Value": float(info.get("input_tokens", 0)), "Unit": "Count"},
            {"MetricName": "OutputTokens", "Dimensions": dims, "Timestamp": ts,
             "Value": float(info.get("output_tokens", 0)), "Unit": "Count"},
            {"MetricName": "LatencySeconds", "Dimensions": dims, "Timestamp": ts,
             "Value": latency, "Unit": "Seconds"},
        ]

    # pipeline-level (no Agent dimension)
    data += [
        {"MetricName": "PerAppCostUSD", "Timestamp": ts,
         "Value": float(cost.get("total_usd", 0.0)), "Unit": "None"},
        {"MetricName": "ParallelLatencySeconds", "Timestamp": ts,
         "Value": float(timing.get("nominal_parallel_per_app_s", 0.0)), "Unit": "Seconds"},
        {"MetricName": "Applications", "Timestamp": ts, "Value": 1.0, "Unit": "Count"},
    ]

    try:
        for i in range(0, len(data), 20):  # CloudWatch caps at 20 datapoints/call
            cw.put_metric_data(Namespace=NAMESPACE, MetricData=data[i:i + 20])
        return True
    except Exception:
        return False
