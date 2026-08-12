#!/usr/bin/env bash
#
# Create the Gateway's Lambda target, then write its ARN into agentcore.json.
#
#   ./deploy/reference-tools.sh          # create/update, then patch the config
#   ./deploy/reference-tools.sh --show   # just print the ARN
#
# WHY THIS IS A SEPARATE STEP AND NOT PART OF THE AGENT STACK
#
# `agentcore.json` has to contain the target's Lambda ARN, and the CLI reads that file
# BEFORE it synthesises CloudFormation. A function created by the agent stack therefore
# cannot be referenced from the config that creates the agent stack -- the ARN does not
# exist yet at the moment it is needed. This is the same ordering the AgentCore workshop
# uses, where the Lambda is a prerequisite and its ARN is retrieved from Parameter Store.
#
# So: this tiny stack first, then `agentcore deploy`.
#
#   ./deploy/reference-tools.sh
#   ./deploy/vendor.sh && agentcore deploy --target default --yes
#
# `deploy/deploy-all.sh` runs them in that order for you.
#
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
STACK="${REFERENCE_TOOLS_STACK:-PpFraudReferenceTools}"
SRC="deploy/cdk/lambda-alerts"
CONFIG="agentcore/agentcore.json"

read -r ACCOUNT REGION <<<"$("$PY" -c "
import json, sys
targets = json.load(open('agentcore/aws-targets.json'))
if not targets:
    sys.exit('agentcore/aws-targets.json is empty; add your account and region first.')
t = next((x for x in targets if x.get('name') == 'default'), targets[0])
print(t['account'], t['region'])
")"

arn_of() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='AlertDictionaryArn'].OutputValue" \
    --output text 2>/dev/null || true
}

if [[ "${1:-}" == "--show" ]]; then
  arn_of
  exit 0
fi

# The alert table is GENERATED from signal_layer.alert_categories, never typed, so this
# lookup cannot disagree with the specialists about what an alert means -- and the agent is
# the one that would sound authoritative if it did. Regenerated on every deploy so a change
# to the categories cannot ship half-applied.
echo "==> regenerating the alert table from signal_layer.alert_categories"
"$PY" - <<'PYEOF'
import json, sys
sys.path.insert(0, ".")
from signal_layer.alert_categories import CATEGORY_ALERTS

table = {}
for category, ids in CATEGORY_ALERTS.items():
    for alert_id in ids:
        table.setdefault(str(alert_id), {"id": alert_id, "categories": []})
        table[str(alert_id)]["categories"].append(category)

json.dump(
    {
        "generated_from": "signal_layer.alert_categories.CATEGORY_ALERTS",
        "note": (
            "The ten alert-ID categories from the customer's General Rule 3. Generated, "
            "never retyped: the specialists and this lookup must agree on what an alert "
            "means."
        ),
        "category_count": len(CATEGORY_ALERTS),
        "alert_count": len(table),
        "categories": {c: sorted(i) for c, i in CATEGORY_ALERTS.items()},
        "alerts": table,
    },
    open("deploy/cdk/lambda-alerts/alerts.json", "w"),
    indent=1,
)
print(f"    {len(CATEGORY_ALERTS)} categories, {len(table)} distinct alert ids")
PYEOF

echo "==> packaging"
rm -f /tmp/pp-alerts.zip
(cd "$SRC" && zip -q -r /tmp/pp-alerts.zip main.py alerts.json)
echo "    $(du -h /tmp/pp-alerts.zip | cut -f1)"

# A hand-written template rather than another CDK app: one function and one role does not
# justify a second node_modules, and this stays readable in a review.
cat > /tmp/pp-reference-tools.yaml <<'YAML'
AWSTemplateFormatVersion: "2010-09-09"
Description: >
  Reference-data tools exposed to the fraud agents through AgentCore Gateway. Separate
  from the agent stack because agentcore.json needs this function's ARN before the CLI
  synthesises the agent stack.
Resources:
  AlertDictionaryRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal: { Service: lambda.amazonaws.com }
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  AlertDictionary:
    Type: AWS::Lambda::Function
    Properties:
      Description: >
        Alert-definition lookup for AgentCore Gateway: what a Point Predictive alert id
        means and which of the ten General Rule 3 categories owns it. Reference data only,
        no borrower information.
      Runtime: python3.12
      Handler: main.handler
      Role: !GetAtt AlertDictionaryRole.Arn
      # A dict lookup over 148 ids. More memory is paying for idle.
      MemorySize: 256
      Timeout: 15
      Code:
        ZipFile: |
          def handler(event, context):
              return {"error": "placeholder; deploy/reference-tools.sh updates this code"}
  # The Gateway invokes the function as bedrock-agentcore. Without this the target is
  # created successfully and every tool call returns AccessDenied at request time.
  AlertDictionaryGatewayPermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !GetAtt AlertDictionary.Arn
      Action: lambda:InvokeFunction
      Principal: bedrock-agentcore.amazonaws.com
      SourceAccount: !Ref AWS::AccountId
Outputs:
  AlertDictionaryArn:
    Description: Gateway target ARN for agentcore.json
    Value: !GetAtt AlertDictionary.Arn
YAML

echo "==> deploying $STACK to $ACCOUNT / $REGION"
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK" \
  --template-file /tmp/pp-reference-tools.yaml \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset >/dev/null

ARN="$(arn_of)"
if [[ -z "$ARN" || "$ARN" == "None" ]]; then
  echo "error: $STACK deployed but produced no AlertDictionaryArn output" >&2
  exit 1
fi

# The template ships a placeholder body so the stack can create the function before the
# real code exists; the code is pushed here, where the zip is available.
echo "==> pushing code"
aws lambda update-function-code --region "$REGION" \
  --function-name "$ARN" --zip-file fileb:///tmp/pp-alerts.zip >/dev/null
aws lambda wait function-updated --region "$REGION" --function-name "$ARN"

echo "==> writing the ARN into $CONFIG"
"$PY" - "$CONFIG" "$ARN" <<'PYEOF'
import json, sys

path, arn = sys.argv[1], sys.argv[2]
config = json.load(open(path))
patched = 0
for gateway in config.get("agentCoreGateways", []):
    for target in gateway.get("targets", []):
        block = target.get("lambdaFunctionArn")
        if block and block.get("lambdaArn") != arn:
            block["lambdaArn"] = arn
            patched += 1
json.dump(config, open(path, "w"), indent=2)
print(f"    {patched} target(s) updated" if patched else "    already current")
PYEOF

echo
echo "Lambda ARN: $ARN"
echo "Next: ./deploy/vendor.sh && agentcore deploy --target default --yes"
