#!/usr/bin/env bash
#
# One command for the two CloudFront endpoints.
#
#   ./deploy/deploy-sites.sh
#
# Builds both sites, bakes the three static API responses, and deploys a single
# CloudFormation stack holding two S3 buckets, two CloudFront distributions and one
# streaming Lambda. Prints both URLs at the end.
#
# WHAT IT DOES NOT DO: deploy the agents. The AgentCore runtimes belong to
# `agentcore deploy` (see deploy/deploy.md), and this script READS the underwriter's
# ARN back from that deployment rather than taking it as an argument -- an ARN typed
# by hand is an ARN that is one character wrong at the worst moment. Run the agent
# deployment first; this script fails with instructions if it has not been run.
#
# Ordering matters and is enforced below:
#   1. agentcore deploy   (agents; separate stack, separate lifecycle)
#   2. this script        (websites; reads the runtime ARN from step 1)
#
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
BUILD_DIR="deploy/cdk/.build"

# MOCK_MODE=0 is not a preference here, it is a correctness requirement. The baked
# /api/health object reports `measures_tokens`, and it must report the same thing the
# deployed runtime does -- which agentcore/agentcore.json sets to MOCK_MODE=0. Bake
# it from a mock process and the deployed UI will claim it measures nothing while the
# runtime bills real tokens.
export MOCK_MODE=0

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import fastapi' >/dev/null 2>&1; then
  echo "error: '$PY' cannot import fastapi, which ui/server.py needs to bake the" >&2
  echo "       static API objects. Point PYTHON at the right interpreter, e.g." >&2
  echo "         PYTHON=\$(pyenv prefix 3.12.9)/bin/python3 ./deploy/deploy-sites.sh" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 0. Find the deployed runtime.
# ---------------------------------------------------------------------------
STATE="agentcore/.cli/deployed-state.json"
if [[ ! -f "$STATE" ]]; then
  echo "error: $STATE not found. Deploy the agents first:" >&2
  echo "         ./deploy/vendor.sh && agentcore deploy --target default --yes" >&2
  exit 1
fi

read -r RUNTIME_ARN ACCOUNT REGION <<<"$(
  "$PY" - "$STATE" <<'PYEOF'
import json, sys, re
state = json.load(open(sys.argv[1]))

def walk(node):
    """Find the underwriter runtime ARN anywhere in the CLI's state file.

    Searched structurally rather than by a fixed key path: the CLI owns this file's
    shape and has changed it between preview releases, and a hard-coded path that
    silently returns None would deploy a stack pointed at nothing.
    """
    if isinstance(node, str):
        if re.fullmatch(r"arn:aws[a-z-]*:bedrock-agentcore:[^:]+:\d+:runtime/.*underwriter.*", node):
            yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)

arns = sorted(set(walk(state)))
if not arns:
    sys.exit("no underwriter runtime ARN in the deployed state; run `agentcore deploy` first")
if len(arns) > 1:
    sys.exit(f"ambiguous: {len(arns)} underwriter ARNs in deployed state: {arns}")
arn = arns[0]
parts = arn.split(":")
print(arn, parts[4], parts[3])
PYEOF
)"

echo "runtime : $RUNTIME_ARN"
echo "account : $ACCOUNT"
echo "region  : $REGION"
echo

# ---------------------------------------------------------------------------
# 1. Docs site -> site/out
# ---------------------------------------------------------------------------
echo "==> building docs site (static export)"
( cd site && [[ -d node_modules ]] || npm install --silent; npm run build >/dev/null )
test -f site/out/index.html || { echo "error: site/out/index.html missing after build" >&2; exit 1; }
echo "    site/out: $(find site/out -name '*.html' | wc -l | tr -d ' ') html files"

# ---------------------------------------------------------------------------
# 2. Demo UI -> ui/dist
# ---------------------------------------------------------------------------
echo "==> building demo UI"
( cd ui && [[ -d node_modules ]] || npm install --silent; npm run build >/dev/null )
test -f ui/dist/index.html || { echo "error: ui/dist/index.html missing after build" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 3. Bake the three static API responses + the Lambda's bootstrap.json
# ---------------------------------------------------------------------------
echo "==> baking static API objects"
rm -rf "$BUILD_DIR"
"$PY" deploy/cdk/scripts/build-static-api.py --out "$BUILD_DIR"
# The handler reads ./bootstrap.json relative to itself, so it has to sit inside the
# asset directory. Copied rather than generated there so `deploy/cdk/lambda/` holds
# exactly one hand-written file and one generated one, and .gitignore can say so.
cp "$BUILD_DIR/bootstrap.json" deploy/cdk/lambda/bootstrap.json

# ---------------------------------------------------------------------------
# 4. Deploy
# ---------------------------------------------------------------------------
echo "==> deploying CloudFront stack"
cd deploy/cdk
[[ -d node_modules ]] || npm install --silent
npx cdk deploy \
  -c "account=$ACCOUNT" \
  -c "runtimeArn=$RUNTIME_ARN" \
  -c "runtimeRegion=$REGION" \
  --require-approval never \
  --outputs-file "$REPO_ROOT/$BUILD_DIR/outputs.json" \
  "$@"

cd "$REPO_ROOT"
echo
"$PY" - "$BUILD_DIR/outputs.json" <<'PYEOF'
import json, sys
out = next(iter(json.load(open(sys.argv[1])).values()))
print("=" * 62)
print(f"  docs : {out.get('DocsUrl')}")
print(f"  demo : {out.get('DemoUrl')}")
print("=" * 62)
print("A CloudFront distribution takes a few minutes to reach every edge; a 403 in")
print("the first minutes is propagation, not a broken deploy.")
PYEOF
