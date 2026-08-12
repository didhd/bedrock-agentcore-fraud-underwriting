#!/usr/bin/env bash
#
# Connect the agents to Snowflake Cortex Analyst. You need a PAT and nothing else.
#
#   ./deploy/connect-snowflake.sh                      # prompts for what it needs
#   ./deploy/connect-snowflake.sh --probe              # test the stored key, change nothing
#   ./deploy/connect-snowflake.sh --enable             # flip SIGNAL_MODE=cortex and deploy
#   ./deploy/connect-snowflake.sh --disable            # back to fixtures
#
# WHAT IT DOES, IN ORDER
#
#   1. writes the four Snowflake values into ONE Secrets Manager secret
#   2. grants the agent runtimes read access to that secret
#   3. PROBES it -- authenticates, runs a trivial query, and asks Cortex Analyst about the
#      semantic view -- so "does the key work" is answered in seconds
#   4. only then offers to flip SIGNAL_MODE
#
# Step 3 is the point of the script. Without it the first real test of the customer's key is
# an agent invocation, and a wrong warehouse looks identical to a broken agent.
#
# WHAT IT DOES NOT DO
#
# It does not touch the semantic view. The guardrails -- hashed SSNs only, the
# application_id AND hash_lender_id join, fake-record exclusion, lender normalisation,
# address keys, active dealers, invalid-credit-value handling -- stay in Snowflake and stay
# the single source of truth. Nothing on the AWS side reimplements them, which is the whole
# migration posture.
#
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
SECRET_ID="${SNOWFLAKE_SECRET_ID:-pp-fraud/snowflake}"

read -r ACCOUNT REGION <<<"$("$PY" -c "
import json, sys
targets = json.load(open('agentcore/aws-targets.json'))
if not targets:
    sys.exit('agentcore/aws-targets.json is empty; set your account and region first.')
t = next((x for x in targets if x.get('name') == 'default'), targets[0])
print(t['account'], t['region'])
")"

probe() {
  echo "==> probing Snowflake with the stored key"
  SNOWFLAKE_SECRET_ID="$SECRET_ID" AWS_REGION="$REGION" "$PY" - <<'PYEOF'
import json, sys
sys.path.insert(0, ".")
from signal_layer.sources.snowflake import probe

result = probe()
print(json.dumps(result, indent=1, default=str))
if not result.get("ok"):
    print(f"\nFAILED at stage: {result.get('stage')}", file=sys.stderr)
    if result.get("hint"):
        print(f"hint: {result['hint']}", file=sys.stderr)
    sys.exit(1)
print("\nSnowflake is reachable and the semantic view answered.")
PYEOF
}

set_mode() {
  local mode="$1"
  echo "==> setting SIGNAL_MODE=$mode on every runtime"
  "$PY" - "$mode" "$SECRET_ID" <<'PYEOF'
import json, sys

mode, secret_id = sys.argv[1], sys.argv[2]
path = "agentcore/agentcore.json"
config = json.load(open(path))

for runtime in config["runtimes"]:
    env = runtime.setdefault("envVars", [])
    by_name = {e["name"]: e for e in env}
    for name, value in (("SIGNAL_MODE", mode), ("SNOWFLAKE_SECRET_ID", secret_id)):
        if name in by_name:
            by_name[name]["value"] = value
        else:
            env.append({"name": name, "value": value})

json.dump(config, open(path, "w"), indent=2)
print(f"    SIGNAL_MODE={mode} on {len(config['runtimes'])} runtimes")
PYEOF
  agentcore validate --json --directory . >/dev/null
  echo "    run ./deploy/vendor.sh && agentcore deploy --target default --yes to apply"
}

case "${1:-}" in
  --probe)
    probe
    exit 0
    ;;
  --enable)
    probe
    set_mode cortex
    exit 0
    ;;
  --disable)
    set_mode fixtures
    exit 0
    ;;
esac

# ---------------------------------------------------------------------------
# Collect the four values. Only the PAT is secret; the rest are addresses.
# ---------------------------------------------------------------------------
echo "Snowflake connection details. Four values, one of them a credential."
echo

read -r -p "  Account URL   (https://<org>-<account>.snowflakecomputing.com): " ACCOUNT_URL
read -r -p "  Semantic view (e.g. FRAUD_CONSORTIUM_SEMANTIC_VIEW): " SEMANTIC_VIEW
read -r -p "  Warehouse     (executes the SQL Cortex Analyst generates): " WAREHOUSE
# -s so the token is not echoed and does not reach the shell history.
read -r -s -p "  PAT           (programmatic access token, not shown): " PAT
echo
echo

for pair in "ACCOUNT_URL:$ACCOUNT_URL" "SEMANTIC_VIEW:$SEMANTIC_VIEW" "PAT:$PAT"; do
  if [[ -z "${pair#*:}" ]]; then
    echo "error: ${pair%%:*} is required." >&2
    exit 1
  fi
done

SECRET_JSON="$("$PY" -c "
import json, sys
print(json.dumps({
    'account_url': sys.argv[1].rstrip('/'),
    'pat': sys.argv[2],
    'semantic_view': sys.argv[3],
    'warehouse': sys.argv[4],
}))" "$ACCOUNT_URL" "$PAT" "$SEMANTIC_VIEW" "$WAREHOUSE")"

echo "==> storing the secret as $SECRET_ID in $ACCOUNT / $REGION"
if aws secretsmanager describe-secret --region "$REGION" --secret-id "$SECRET_ID" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --region "$REGION" \
    --secret-id "$SECRET_ID" --secret-string "$SECRET_JSON" >/dev/null
  echo "    updated"
else
  aws secretsmanager create-secret --region "$REGION" \
    --name "$SECRET_ID" \
    --description "Snowflake Cortex Analyst access for the Point Predictive fraud agents. The PAT is the only credential; the semantic view keeps the SQL guardrails." \
    --secret-string "$SECRET_JSON" >/dev/null
  echo "    created"
fi

# ---------------------------------------------------------------------------
# Let the runtimes read it. Attached to each execution role as its own inline policy so a
# later `agentcore deploy` -- which rewrites its OWN inline policy -- leaves this intact.
# ---------------------------------------------------------------------------
echo "==> granting the agent runtimes read access"
SECRET_ARN="$(aws secretsmanager describe-secret --region "$REGION" --secret-id "$SECRET_ID" \
  --query ARN --output text)"

ROLES="$(aws cloudformation describe-stack-resources --region "$REGION" \
  --stack-name AgentCore-ppFraud-default \
  --query "StackResources[?ResourceType=='AWS::IAM::Role'].PhysicalResourceId" \
  --output text 2>/dev/null || true)"

if [[ -z "$ROLES" ]]; then
  echo "    no agent stack found yet; re-run this after ./deploy/deploy-all.sh" >&2
else
  POLICY="$("$PY" -c "
import json, sys
print(json.dumps({
    'Version': '2012-10-17',
    'Statement': [{
        'Sid': 'ReadSnowflakeCredential',
        'Effect': 'Allow',
        'Action': ['secretsmanager:GetSecretValue'],
        'Resource': [sys.argv[1]],
    }],
}))" "$SECRET_ARN")"
  for role in $ROLES; do
    aws iam put-role-policy --role-name "$role" \
      --policy-name SnowflakeSecretRead --policy-document "$POLICY"
    echo "    $role"
  done
fi

echo
probe

echo
echo "The key works. To route the agents through Cortex Analyst:"
echo "    ./deploy/connect-snowflake.sh --enable"
echo "    ./deploy/vendor.sh && agentcore deploy --target default --yes"
echo
echo "To go back to the committed fixtures at any point:"
echo "    ./deploy/connect-snowflake.sh --disable"
