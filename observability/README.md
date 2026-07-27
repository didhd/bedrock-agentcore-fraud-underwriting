# Observability: per-agent spend on CloudWatch

This closes the demo with a CloudWatch dashboard that shows **cost, tokens, and
latency broken down by agent** for the parallel underwriting pipeline. It makes
the per-agent-model story concrete: the light-tier agents are cheap, the
heavy-tier reasoning agents cost more, and you can see exactly where spend goes.

## Pieces

- `metrics.py` — publishes per-agent metrics (`CostUSD`, `InputTokens`,
  `OutputTokens`, `LatencySeconds`) plus pipeline metrics (`PerAppCostUSD`,
  `ParallelLatencySeconds`, `Applications`) to the `AgentCoreFraudDemo`
  namespace. Emission is off unless `EMIT_CLOUDWATCH_METRICS=1`.
- `cloudwatch_dashboard.json` — the dashboard body (spend per agent, latency per
  agent, tokens per agent, cost per application, total spend over time). Uses
  `SEARCH` expressions so it auto-discovers agents.
- `create_dashboard.py` — creates/updates the dashboard via PutDashboard.

## Run it

```bash
# 1) create the dashboard (needs boto3 + cloudwatch:PutDashboard)
python observability/create_dashboard.py --region us-east-1 --name AgentCoreFraudDemo

# 2) drive traffic with emission enabled to populate it
export EMIT_CLOUDWATCH_METRICS=1
export AWS_REGION=us-east-1
python app.py --review APP-1004
python app.py --batch 20
# or drive it live from the UI (python app.py --serve) or AgentCore Runtime
```

Open the printed dashboard URL. Metrics appear within a minute or two.

## IAM

The principal running the pipeline needs `cloudwatch:PutMetricData`, and
whoever creates the dashboard needs `cloudwatch:PutDashboard`. Both are included
in `deploy/iam_policy.json`.

## Notes

- Offline / `MOCK_MODE` costs and tokens are illustrative nominal figures (see
  `agents/pricing.py`); live runs use actual Bedrock token usage.
- On AgentCore Runtime, these custom metrics complement AgentCore Observability
  (CloudWatch Transaction Search), which traces each agent invocation.
