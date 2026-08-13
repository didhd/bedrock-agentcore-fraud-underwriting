#!/usr/bin/env bash
#
# Connect the agents to the customer's RDS. Read AND write, which is what they asked for.
#
#   ./deploy/connect-rds.sh                     # prompts, deploys, probes
#   ./deploy/connect-rds.sh --vpc subnet-a,subnet-b sg-x   # plain RDS PostgreSQL, no Data API
#   ./deploy/connect-rds.sh --probe             # test what is stored, change nothing
#   ./deploy/connect-rds.sh --enable            # signals come from RDS instead of fixtures
#   ./deploy/connect-rds.sh --disable           # back to the committed fixtures
#
# WHAT GETS CONNECTED, AND THEY ARE TWO DIFFERENT THINGS
#
#   1. THE SIGNAL PATH (--enable). `SIGNAL_MODE=aurora`, so the eight specialists analyse
#      signals read from their cluster instead of from fixtures, and each adjudication is
#      written back to `underwriting_report_output`. This is the read-and-write they asked
#      for. No model writes SQL here: the pipeline reads the precomputed signals before any
#      agent runs, which is what keeps a text-to-SQL round trip out of the per-application
#      minute.
#
#   2. THE ANALYST TOOL (always deployed by this script). A read-only SQL tool behind
#      AgentCore Gateway, attached to Francis only, for the questions no precomputed signal
#      answers -- decline rates, alert frequencies, one dealer against the book. SELECT
#      only, allowlisted tables, 200-row cap, and the database role is the real boundary.
#
# The eight specialists never receive tool (2). That separation is the property the whole
# port rests on, and it is why this script deploys them as two distinct things.
#
# WHICH TRANSPORT YOU WANT
#
#   default   RDS Data API. Nothing goes in a VPC. Requires Aurora with the Data API
#             enabled -- Serverless v2 or provisioned Aurora PostgreSQL.
#   --vpc     A VPC-attached Lambda holding the socket. Works with ANY RDS PostgreSQL,
#             including a db.t3.micro with no Data API. The ten agent runtimes stay
#             networkMode: PUBLIC either way -- only this Lambda joins the VPC.
#
# `--vpc` is the one to reach for if `--probe` reports the Data API is not enabled.
#
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
STACK="${RDS_TOOLS_STACK:-PpFraudRdsTools}"
SRC="deploy/cdk/lambda-rds"
CONFIG="agentcore/agentcore.json"
BUILD="/tmp/pp-rds-build"

read -r ACCOUNT REGION <<<"$("$PY" -c "
import json, sys
targets = json.load(open('agentcore/aws-targets.json'))
if not targets:
    sys.exit('agentcore/aws-targets.json is empty; add your account and region first.')
t = next((x for x in targets if x.get('name') == 'default'), targets[0])
print(t['account'], t['region'])
")"

# Persisted so --probe and --enable do not ask again. Not a secret: ARNs and a database name.
# The credentials themselves never leave Secrets Manager.
STATE="deploy/.rds-connection.json"

state_get() {
  [[ -f "$STATE" ]] || return 0
  "$PY" -c "import json,sys; print(json.load(open('$STATE')).get(sys.argv[1],''))" "$1"
}

# ---------------------------------------------------------------------------
# Probe. Two halves, because the two paths fail differently and have opposite fixes.
# ---------------------------------------------------------------------------
probe() {
  local cluster secret database
  cluster="$(state_get cluster_arn)"
  secret="$(state_get secret_arn)"
  database="$(state_get database)"

  if [[ -z "$secret" ]]; then
    echo "error: nothing stored yet. Run ./deploy/connect-rds.sh first." >&2
    exit 1
  fi

  echo "==> probing the signal path (RDS Data API, from here)"
  if [[ -n "$cluster" ]]; then
    AURORA_CLUSTER_ARN="$cluster" AURORA_SECRET_ARN="$secret" \
    AURORA_DATABASE="$database" AWS_REGION="$REGION" \
    AURORA_SIGNAL_TABLE="$(state_get signal_table)" "$PY" - <<'PYEOF' || true
import json, os, sys
sys.path.insert(0, ".")
if not os.environ.get("AURORA_SIGNAL_TABLE"):
    os.environ.pop("AURORA_SIGNAL_TABLE", None)
from signal_layer.sources.aurora import probe

result = probe()
print(json.dumps(result, indent=1, default=str)[:2500])
if not result.get("ok"):
    print(f"\n  stopped at stage: {result.get('stage')}", file=sys.stderr)
    if result.get("hint"):
        print(f"  hint: {result['hint']}", file=sys.stderr)
PYEOF
  else
    echo "    skipped: no cluster ARN stored, so this is a --vpc deployment. The signal"
    echo "    path needs the Data API; the analyst tool below does not."
  fi

  echo
  echo "==> probing the analyst tool (the deployed Lambda, as the Gateway calls it)"
  local fn
  fn="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='UnderwritingSqlArn'].OutputValue" \
    --output text 2>/dev/null || true)"
  if [[ -z "$fn" || "$fn" == "None" ]]; then
    echo "    not deployed yet"
    return 0
  fi

  # Invoked exactly as the Gateway does: the tool name arrives in the client context, so a
  # plain payload with no tool name would take the unknown-tool branch and prove nothing.
  local ctx
  ctx="$("$PY" -c "
import base64, json
print(base64.b64encode(json.dumps({'custom': {'bedrockAgentCoreToolName': 'UnderwritingSql___get_schema'}}).encode()).decode())")"
  aws lambda invoke --region "$REGION" --function-name "$fn" \
    --client-context "$ctx" --payload '{}' --cli-binary-format raw-in-base64-out \
    /tmp/pp-rds-probe.json >/dev/null
  "$PY" - <<'PYEOF'
import json
out = json.load(open("/tmp/pp-rds-probe.json"))
print(json.dumps(out, indent=1)[:1800])
if out.get("ok"):
    tables = out.get("tables") or {}
    print(f"\n  reachable. {len(tables)} of {len(out.get('readable_tables') or [])} "
          f"allowlisted tables exist: {', '.join(sorted(tables)) or 'none'}")
    if out.get("not_present_in_this_database"):
        print(f"  absent here: {', '.join(out['not_present_in_this_database'])}")
else:
    print(f"\n  FAILED: {out.get('error')}")
    if out.get("hint"):
        print(f"  hint: {out['hint']}")
PYEOF
}

set_mode() {
  local mode="$1"
  echo "==> setting SIGNAL_MODE=$mode on every runtime"
  "$PY" - "$mode" "$(state_get cluster_arn)" "$(state_get secret_arn)" \
        "$(state_get database)" "$(state_get signal_table)" <<'PYEOF'
import json, sys

mode, cluster, secret, database, signal_table = sys.argv[1:6]
path = "agentcore/agentcore.json"
config = json.load(open(path))

wanted = {"SIGNAL_MODE": mode}
if mode == "aurora":
    wanted.update(
        {
            "AURORA_CLUSTER_ARN": cluster,
            "AURORA_SECRET_ARN": secret,
            "AURORA_DATABASE": database,
        }
    )
    if signal_table:
        wanted["AURORA_SIGNAL_TABLE"] = signal_table

for runtime in config["runtimes"]:
    env = runtime.setdefault("envVars", [])
    by_name = {e["name"]: e for e in env}
    for name, value in wanted.items():
        if not value:
            continue
        if name in by_name:
            by_name[name]["value"] = value
        else:
            env.append({"name": name, "value": value})

json.dump(config, open(path, "w"), indent=2)
print(f"    SIGNAL_MODE={mode} on {len(config['runtimes'])} runtimes")
PYEOF
  agentcore validate --json --directory . >/dev/null
  echo "    run ./deploy/vendor.sh && agentcore deploy --target default --yes to apply"

  # agentcore.json is COMMITTED in this repo by design -- the CLI reads it and the customer's
  # own deploy has to reproduce ours. It now contains the cluster ARN, the secret ARN and the
  # database name, which are not credentials but do identify your infrastructure. Said out loud
  # here rather than left for someone to notice in a diff, because the decision is the
  # operator's and the alternative (injecting them at deploy time from the gitignored state
  # file) is a change to how every runtime gets its environment.
  echo
  echo "    NOTE: agentcore/agentcore.json now carries YOUR infrastructure identifiers"
  echo "          (cluster ARN, secret ARN, database). That file is committed in this repo."
  echo "          Decide whether that is acceptable before you commit it. No credential is"
  echo "          in it -- the Data API fetches those from Secrets Manager itself."
}

VPC_SUBNETS=""
VPC_SGS=""
case "${1:-}" in
  --probe)   probe; exit 0 ;;
  --enable)
    if [[ -z "$(state_get cluster_arn)" ]]; then
      echo "error: SIGNAL_MODE=aurora reads through the Data API from inside the runtime," >&2
      echo "       which needs a cluster ARN. This is a --vpc deployment, so the analyst" >&2
      echo "       tool works but the signal path does not. Leave SIGNAL_MODE=fixtures." >&2
      exit 1
    fi
    probe; set_mode aurora; exit 0 ;;
  --disable) set_mode fixtures; exit 0 ;;
  --vpc)
    VPC_SUBNETS="${2:?--vpc needs subnet ids: --vpc subnet-a,subnet-b sg-x}"
    VPC_SGS="${3:?--vpc needs a security group id: --vpc subnet-a,subnet-b sg-x}"
    ;;
esac

# ---------------------------------------------------------------------------
# Collect what is needed. All of it is addresses; the credential stays in Secrets Manager.
# ---------------------------------------------------------------------------
echo "RDS connection details. The password is NOT one of them -- it stays in the secret."
echo

CLUSTER_ARN=""
PGHOST_VALUE=""
if [[ -n "$VPC_SUBNETS" ]]; then
  echo "  (--vpc: the Lambda holds the socket, so no Data API is required)"
  read -r -p "  Instance endpoint (xxx.yyy.$REGION.rds.amazonaws.com): " PGHOST_VALUE
else
  read -r -p "  Cluster ARN   (arn:aws:rds:$REGION:$ACCOUNT:cluster:...): " CLUSTER_ARN
fi
read -r -p "  Secret ARN    (Secrets Manager secret with the DB credentials): " SECRET_ARN
read -r -p "  Database name [postgres]: " DATABASE
DATABASE="${DATABASE:-postgres}"
read -r -p "  Signal table  [application_signals]: " SIGNAL_TABLE
SIGNAL_TABLE="${SIGNAL_TABLE:-application_signals}"

if [[ -z "$SECRET_ARN" ]]; then
  echo "error: the secret ARN is required -- it is how both paths authenticate." >&2
  exit 1
fi

"$PY" - "$STATE" "$CLUSTER_ARN" "$SECRET_ARN" "$DATABASE" "$SIGNAL_TABLE" \
      "$PGHOST_VALUE" <<'PYEOF'
import json, sys
path, cluster, secret, database, signal_table, host = sys.argv[1:7]
json.dump(
    {
        "cluster_arn": cluster,
        "secret_arn": secret,
        "database": database,
        "signal_table": signal_table,
        "host": host,
        "note": "Addresses only. Credentials live in the Secrets Manager secret above.",
    },
    open(path, "w"),
    indent=1,
)
PYEOF
echo
echo "==> stored in $STATE"

# ---------------------------------------------------------------------------
# Package. pg8000 only for the socket path -- pure Python, so no manylinux cross-compile.
# ---------------------------------------------------------------------------
# Delegated to bundle.sh, not duplicated here. This script used to package inline and that
# packaging omitted the generated use-case payload, so the Lambda deployed with main.py alone:
# `list_use_cases` then answered "no verified queries are available" from a tool that was
# otherwise wired correctly end to end. One packaging path means one place to forget something.
echo "==> packaging the analyst tool"
if [[ -n "$VPC_SUBNETS" ]]; then
  PYTHON="$PY" ./deploy/cdk/lambda-rds/bundle.sh --socket
else
  PYTHON="$PY" ./deploy/cdk/lambda-rds/bundle.sh
fi

# ---------------------------------------------------------------------------
# The stack. Hand-written for the same reason reference-tools.sh is: one function and one
# role does not justify a second node_modules, and this stays readable in a review.
# ---------------------------------------------------------------------------
VPC_PROPS=""
VPC_POLICY=""
if [[ -n "$VPC_SUBNETS" ]]; then
  # Generated by Python rather than assembled with sed and tr. The shell version of this was
  # a five-stage pipeline whose output nobody could verify by reading it, and a malformed
  # SubnetIds list fails at CloudFormation validation with a message that points at the
  # template rather than at the argument that produced it.
  VPC_PROPS="$("$PY" - "$VPC_SUBNETS" "$VPC_SGS" <<'PYEOF'
import json, sys

subnets = [s.strip() for s in sys.argv[1].split(",") if s.strip()]
groups = [g.strip() for g in sys.argv[2].split(",") if g.strip()]
if not subnets or not groups:
    sys.exit("--vpc needs at least one subnet id and one security group id")
print("      VpcConfig:")
print(f"        SubnetIds: {json.dumps(subnets)}")
print(f"        SecurityGroupIds: {json.dumps(groups)}")
PYEOF
)"
  # A VPC-attached Lambda creates ENIs, which needs the three EC2 actions in this managed
  # policy. Without it the function is created and then fails to initialise with a message
  # that does not mention networking at all.
  VPC_POLICY="        - arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
fi

cat > /tmp/pp-rds-tools.yaml <<YAML
AWSTemplateFormatVersion: "2010-09-09"
Description: >
  Read-only SQL over the customer's underwriting database, exposed to Francis through
  AgentCore Gateway. Separate stack from the agents because agentcore.json needs this
  function's ARN before the CLI synthesises the agent stack.
Resources:
  UnderwritingSqlRole:
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
$VPC_POLICY
      Policies:
        - PolicyName: ReadTheDatabase
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              # ExecuteStatement only. No BatchExecuteStatement, no transaction APIs: this
              # function reads, and the IAM policy says so as well as the code.
              - Effect: Allow
                Action: [rds-data:ExecuteStatement]
                Resource: ["*"]
              - Effect: Allow
                Action: [secretsmanager:GetSecretValue]
                Resource: ["$SECRET_ARN"]
  UnderwritingSql:
    Type: AWS::Lambda::Function
    Properties:
      Description: >
        Read-only SQL tool for AgentCore Gateway. SELECT and WITH only, allowlisted tables,
        200-row cap. Attached to the interactive agent; the eight fraud specialists do not
        receive it.
      Runtime: python3.12
      Handler: main.handler
      Role: !GetAtt UnderwritingSqlRole.Arn
      MemorySize: 512
      # Under the Gateway an analyst is waiting, and 60s is long enough that a query which
      # needs more than that should be a precomputed signal instead.
      Timeout: 60
      Environment:
        Variables:
          AURORA_CLUSTER_ARN: "$CLUSTER_ARN"
          AURORA_SECRET_ARN: "$SECRET_ARN"
          AURORA_DATABASE: "$DATABASE"
          PGHOST: "$PGHOST_VALUE"
$VPC_PROPS
      Code:
        ZipFile: |
          def handler(event, context):
              return {"ok": False, "error": "placeholder; connect-rds.sh pushes the code"}
  # The Gateway invokes as bedrock-agentcore. Without this the target is created
  # successfully and every tool call returns AccessDenied at request time.
  UnderwritingSqlGatewayPermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !GetAtt UnderwritingSql.Arn
      Action: lambda:InvokeFunction
      Principal: bedrock-agentcore.amazonaws.com
      SourceAccount: !Ref AWS::AccountId
Outputs:
  UnderwritingSqlArn:
    Description: Gateway target ARN for agentcore.json
    Value: !GetAtt UnderwritingSql.Arn
YAML

echo "==> deploying $STACK to $ACCOUNT / $REGION"
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK" \
  --template-file /tmp/pp-rds-tools.yaml \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset >/dev/null

ARN="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='UnderwritingSqlArn'].OutputValue" --output text)"
if [[ -z "$ARN" || "$ARN" == "None" ]]; then
  echo "error: $STACK deployed but produced no UnderwritingSqlArn output" >&2
  exit 1
fi

echo "==> pushing code"
aws lambda update-function-code --region "$REGION" \
  --function-name "$ARN" --zip-file fileb:///tmp/pp-rds.zip >/dev/null
aws lambda wait function-updated --region "$REGION" --function-name "$ARN"

# ---------------------------------------------------------------------------
# Register it as a second target on the gateway that already exists.
# ---------------------------------------------------------------------------
echo "==> adding the UnderwritingSql target to the ppfraud-reference gateway"
"$PY" - "$CONFIG" "$ARN" <<'PYEOF'
import json, sys

path, arn = sys.argv[1], sys.argv[2]
config = json.load(open(path))
gateways = config.get("agentCoreGateways") or []
if not gateways:
    sys.exit("no agentCoreGateways in agentcore.json; run ./deploy/reference-tools.sh first")

gateway = gateways[0]
targets = gateway.setdefault("targets", [])
target = next((t for t in targets if t.get("name") == "UnderwritingSql"), None)
if target is None:
    targets.append(
        {
            "name": "UnderwritingSql",
            "targetType": "lambdaFunctionArn",
            "lambdaFunctionArn": {
                "lambdaArn": arn,
                "toolSchemaFile": "agentcore/tool/underwriting_sql_schema.json",
            },
        }
    )
    print(f"    target added ({len(targets)} on {gateway['name']})")
else:
    target["lambdaFunctionArn"]["lambdaArn"] = arn
    print("    target ARN updated")

json.dump(config, open(path, "w"), indent=2)
PYEOF
agentcore validate --json --directory . >/dev/null && echo "    agentcore validate passes"

echo
probe

cat <<TEXT

Lambda: $ARN

Deploy the gateway target so Francis can call it:
    ./deploy/vendor.sh && agentcore deploy --target default --yes

Then, if the signal path probed clean, point the specialists at the cluster:
    ./deploy/connect-rds.sh --enable
    ./deploy/vendor.sh && agentcore deploy --target default --yes

Back to the committed fixtures at any time:
    ./deploy/connect-rds.sh --disable
TEXT
