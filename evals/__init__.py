"""
Measurement and evaluation for the Point Predictive AgentCore port.

This package owns every performance and cost number the repo is allowed to
publish. Nothing here invents a figure: ``bench.py`` measures, and the JSON it
emits carries a ``caveats`` array naming exactly what was and was not measured
in that run. The UI and README consume that JSON rather than hard-coding
illustrative values.

Contents:
  bench.py                        the harness (offline overhead / live Bedrock)
  datasets/underwriting_golden.jsonl   AGENTCORE_EVALUATION_PREDEFINED_V1 scenarios
  evaluators/*.json               AgentCore evaluator definitions (CLI schema)
  README.md                       the real CLI commands + measurement status
"""

from __future__ import annotations

__all__ = ["bench"]
