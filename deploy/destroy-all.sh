#!/usr/bin/env bash
#
# Remove everything this repository deployed.
#
#   ./deploy/destroy-all.sh              # asks once, then deletes both stacks
#   ./deploy/destroy-all.sh --yes        # no prompt
#
# Reverse order of deploy-all.sh: websites first, then agents. The website stack holds
# a reference to the agent runtime's ARN, so deleting the agents first would leave a
# stack pointing at something that no longer exists.
#
# WHAT IS LOST. Both S3 buckets hold only build output, so nothing there is a source of
# truth. The one thing that does NOT come back is the AgentCore Memory attached to
# francis, which holds her conversation history; it is deleted with its stack.
#
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

PY="${PYTHON:-python3}"
AGENT_STACK="${AGENT_STACK:-AgentCore-ppFraud-default}"
SITES_STACK="${SITES_STACK:-PpFraudSites}"

# The account and region come from the deployment itself, not from a flag, so this
# cannot be pointed at the wrong account by a typo.
TARGETS="agentcore/aws-targets.json"
if [[ ! -f "$TARGETS" ]]; then
  echo "error: $TARGETS not found; nothing to destroy." >&2
  exit 1
fi
read -r ACCOUNT REGION <<<"$("$PY" -c "
import json, sys
targets = json.load(open('$TARGETS'))
if not targets:
    sys.exit('aws-targets.json is empty; nothing was ever deployed from this checkout.')
t = next((x for x in targets if x.get('name') == 'default'), targets[0])
print(t['account'], t['region'])
")"

echo "This will DELETE, in account $ACCOUNT / $REGION:"
echo "    $SITES_STACK   2 CloudFront distributions, 2 S3 buckets, 1 Lambda"
echo "    $AGENT_STACK   2 AgentCore runtimes, 1 AgentCore memory, 3 IAM roles"
echo
echo "francis's conversation history in AgentCore Memory does not come back."
echo

if [[ "${1:-}" != "--yes" ]]; then
  read -r -p "Type the account id to confirm: " CONFIRM
  if [[ "$CONFIRM" != "$ACCOUNT" ]]; then
    echo "Did not match. Nothing deleted."
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 1. Websites
# ---------------------------------------------------------------------------
if aws cloudformation describe-stacks --region "$REGION" --stack-name "$SITES_STACK" \
     >/dev/null 2>&1; then
  echo "==> destroying $SITES_STACK"
  # Through the CDK app rather than delete-stack, so the same context values that
  # created it are present. `cdk destroy` synthesises before deleting, and the app
  # throws without them.
  ( cd deploy/cdk && npx cdk destroy --force \
      -c "account=$ACCOUNT" \
      -c "runtimeArn=arn:aws:bedrock-agentcore:$REGION:$ACCOUNT:runtime/placeholder" \
      -c "runtimeRegion=$REGION" \
      "$SITES_STACK" ) || {
    echo "cdk destroy failed; falling back to delete-stack" >&2
    aws cloudformation delete-stack --region "$REGION" --stack-name "$SITES_STACK"
    aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$SITES_STACK"
  }
else
  echo "==> $SITES_STACK not present, skipping"
fi

# ---------------------------------------------------------------------------
# 2. Agents
# ---------------------------------------------------------------------------
if aws cloudformation describe-stacks --region "$REGION" --stack-name "$AGENT_STACK" \
     >/dev/null 2>&1; then
  echo "==> destroying $AGENT_STACK"
  aws cloudformation delete-stack --region "$REGION" --stack-name "$AGENT_STACK"
  aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$AGENT_STACK"
else
  echo "==> $AGENT_STACK not present, skipping"
fi

# The CLI's record of what is deployed is now wrong. Resetting it means a later
# `agentcore deploy` starts clean instead of trying to reconcile against resources
# that no longer exist.
STATE="agentcore/.cli/deployed-state.json"
if [[ -f "$STATE" ]]; then
  echo '{
  "targets": {}
}' > "$STATE"
  echo "==> reset $STATE"
fi

cd "$REPO_ROOT"
echo
echo "Done. Nothing from this repository remains in $ACCOUNT / $REGION."
echo "The CDK bootstrap stack (CDKToolkit) is shared infrastructure and was left alone."
