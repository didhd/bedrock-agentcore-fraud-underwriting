#!/usr/bin/env bash
#
# Everything, in one command.
#
#   ./deploy/deploy-all.sh
#
# Deploys the agents and then the two websites, in that order, and prints the two
# CloudFront URLs at the end. Both halves are CDK; they are two stacks with two
# front-ends, and this script is the seam between them:
#
#   AgentCore-ppFraud-default   `agentcore deploy`   wraps agentcore/cdk/ (@aws/agentcore-cdk)
#   PpFraudSites                `npx cdk deploy`     deploy/cdk/
#
# WHY NOT ONE STACK. The agent stack's CDK app is generated and managed by the
# AgentCore CLI, which owns `agentcore.json` as its source of truth and re-synthesises
# from it. Putting two S3 buckets and two CloudFront distributions in there would tie
# a website upload to every prompt change, and would put resources the CLI does not
# know about inside the stack it manages. Two stacks, deployed in order, keeps each
# tool authoritative over what it actually owns -- and the website stack reads the
# runtime ARN back out of the agent deployment, so the seam is data, not a copied
# string.
#
# ORDER IS LOAD-BEARING: step 2 cannot resolve a runtime ARN that step 1 has not
# created yet.
#
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
TARGET="${TARGET:-default}"

echo "=============================================================="
echo " 1/2  agents  ->  stack AgentCore-ppFraud-default"
echo "=============================================================="

# Not optional, and skipping it fails misleadingly: `agentcore package` roots each
# zip at the runtime's own codeLocation, so the shared packages one level up are
# absent and the container dies on `ModuleNotFoundError: No module named 'app'` --
# which the service reports as a 30-second initialisation timeout.
./deploy/vendor.sh

if [[ ! -d agentcore/cdk/node_modules ]]; then
  echo "==> installing agentcore/cdk dependencies"
  (cd agentcore/cdk && npm install --silent)
fi

agentcore validate --json
agentcore deploy --target "$TARGET" --yes

echo
echo "=============================================================="
echo " 2/2  websites  ->  stack PpFraudSites"
echo "=============================================================="
PYTHON="$PY" ./deploy/deploy-sites.sh

echo
echo "Both stacks deployed. To verify the agents actually reached Bedrock rather"
echo "than running offline, check that the first SSE frame says mock_mode: false --"
echo "see the 'Verify' section of deploy/DEPLOY.md."
