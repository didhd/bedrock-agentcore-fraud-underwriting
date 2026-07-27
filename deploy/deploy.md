# Deploying to Amazon Bedrock AgentCore Runtime

This walks through taking the demo from local mock mode to a running,
session-isolated agent on **Amazon Bedrock AgentCore Runtime**.

## Prerequisites

- Python 3.10+ (the AgentCore Runtime requirement)
- AWS credentials with the permissions in `iam_policy.json`
- **Bedrock model access enabled** in your target region for:
  - Claude Sonnet (`us.anthropic.claude-sonnet-4-6`) — primary
  - Claude Haiku (`us.anthropic.claude-haiku-4-5-...`) — used internally by some frameworks
  - Enable in Bedrock Console → Model access
- Docker (the starter toolkit builds a container image for the runtime)

## 1. Install dependencies

```bash
cd fraud-agentcore-demo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Validate locally first (no AWS calls)

```bash
# Offline flow demo — deterministic, zero Bedrock cost
MOCK_MODE=1 python tests/test_local.py
MOCK_MODE=1 python app.py --review APP-1004
```

## 3. Validate against Bedrock locally (real inference)

```bash
export AWS_REGION=us-east-1
export AWS_PROFILE=your-bedrock-profile
export BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6   # optional, this is the default

python app.py --review APP-1004
python app.py "Check APP-1004 for fraud ring activity"
```

You can also run the AgentCore dev server (mirrors the deployed request contract,
hot-reloadable):

```bash
python agentcore_runtime.py
# in another shell:
curl -s -XPOST http://localhost:8080/invocations \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Run a full underwriting review of APP-1004"}'
```

## 4. Configure the AgentCore Runtime

The starter toolkit reads `agentcore_runtime.py` (the `@app.entrypoint`) and
`requirements.txt`, builds a container, and provisions the runtime + execution role.

```bash
agentcore configure --entrypoint agentcore_runtime.py
```

When prompted:
- **Execution role** — let it create one, or supply a role that has `iam_policy.json` attached.
- **Region** — a region where you enabled Claude model access.
- **Requirements** — point at `requirements.txt`.

## 5. Launch (deploy)

```bash
agentcore launch
```

This packages the code, builds the image, pushes to ECR, and creates the
AgentCore Runtime. On success you get an agent runtime ARN.

## 6. Invoke the deployed agent

```bash
agentcore invoke '{"prompt": "Run a full underwriting review of APP-1004"}'
agentcore invoke '{"prompt": "Is there income fraud on APP-1003?"}'
```

## 7. Observability

Enable **CloudWatch Transaction Search** once per account, then every
orchestrator run emits traces (routing decisions, each specialist tool call,
latency, token usage) to CloudWatch. This is the AgentCore-native replacement
for having to build your own tracing around the Snowflake Cortex agents.

## Production hardening (not in this demo)

- **Data layer**: replace `tools/application_data.py:fetch_application_record`
  with a call to Snowflake (via PrivateLink), Amazon Athena, or Aurora. Nothing
  above that function changes. Alternatively, front the consortium data with
  **AgentCore Gateway** to expose it as a governed MCP tool.
- **Guardrails**: attach a Bedrock Guardrail to redact/deny PII (SSN) at the
  platform level — enforced server-side, no agent can bypass it.
- **Identity**: put the endpoint behind **AgentCore Identity** (OAuth/JWT) so
  each underwriter's calls are attributable.
- **Memory**: use **AgentCore Memory** to retain per-applicant case context
  across a review session.
- **Model pinning**: keep `BEDROCK_MODEL_ID` pinned to a specific model version
  for reproducible adjudication.
