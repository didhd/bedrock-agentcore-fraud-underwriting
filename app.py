#!/usr/bin/env python3
"""
Local CLI runner for the multi-agent fraud-detection demo.

Usage:
  # Offline flow demo (no AWS creds, no Bedrock calls):
  MOCK_MODE=1 python app.py --review APP-1004
  MOCK_MODE=1 python app.py "Is there income fraud on APP-1003?"

  # Live against Amazon Bedrock (requires AWS creds + model access):
  python app.py --review APP-1004
  python app.py "Check APP-1004 for fraud ring activity"

Flags:
  --review APP-XXXX   Run the parallel underwriting pipeline (8 specialists
                      concurrent, the orchestrator synthesizes) with a latency view.
  --batch [N]         Throughput demo: process all demo apps (repeated to ~N,
                      default 12) concurrently and report daily capacity.
  --signal-demo APP-XXXX  Two-phase flow: signal retrieval (Cortex Analyst
                      semantic view via MCP) then parallel adjudication.
  --models            Print the per-agent model configuration.
  --demo              Run the pipeline on all demo applications.
  --list              List available demo application ids.
  <free text>         Ask the orchestrator a targeted question (routes to specialists).

Env:
  MOCK_MODE=1         Offline deterministic mode (no Bedrock).
  SIM_LATENCY_SCALE   Offline only: scale of nominal latency to actually sleep,
                      so the parallel speedup is observable (CLI sets 0.05).
"""

import os
import sys


def _enable_timing_sim():
    """In offline mode, sleep a small scaled latency so timing is observable."""
    if os.environ.get("MOCK_MODE") == "1" and "SIM_LATENCY_SCALE" not in os.environ:
        os.environ["SIM_LATENCY_SCALE"] = "0.05"


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    # import after env is settable
    from agents.orchestrator import (
        analyze, underwriting_pipeline, batch_throughput, signal_layer_demo,
    )
    from agents.models import model_assignment_table
    from tools.application_data import available_application_ids

    if argv[0] == "--list":
        print("Available demo applications:")
        for app_id in available_application_ids():
            print(f"  {app_id}")
        return 0

    if argv[0] == "--models":
        print(model_assignment_table())
        return 0

    if argv[0] == "--demo":
        _enable_timing_sim()
        for app_id in available_application_ids():
            print(underwriting_pipeline(app_id))
            print()
        return 0

    if argv[0] == "--review":
        if len(argv) < 2:
            print("Usage: app.py --review APP-XXXX")
            return 1
        _enable_timing_sim()
        print(underwriting_pipeline(argv[1]))
        return 0

    if argv[0] == "--batch":
        _enable_timing_sim()
        target = int(argv[1]) if len(argv) > 1 else 12
        base = available_application_ids()
        app_ids = [base[i % len(base)] for i in range(target)]
        print(batch_throughput(app_ids, concurrency=4))
        return 0

    if argv[0] == "--signal-demo":
        if len(argv) < 2:
            print("Usage: app.py --signal-demo APP-XXXX")
            return 1
        _enable_timing_sim()
        print(signal_layer_demo(argv[1]))
        return 0

    print(analyze(" ".join(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
