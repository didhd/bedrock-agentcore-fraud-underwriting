#!/usr/bin/env bash
#
# One command from an empty account to agents running inside a VPC with a private database.
#
#   ./deploy/one-shot.sh                                   # create the VPC and the database
#   ./deploy/one-shot.sh --vpc vpc-x --subnets a,b         # your VPC, our database
#   ./deploy/one-shot.sh --vpc vpc-x --subnets a,b --db-sg sg-y
#                                                          # your VPC and your database
#   ./deploy/one-shot.sh --plan                            # print what would happen, change nothing
#   ./deploy/one-shot.sh --aurora-version 16.13            # regions differ; check before deploying
#
# WHY THE ORDER IS WHAT IT IS
#
# The network has to exist before the agents, because the agents are deployed WITH
# `networkMode: VPC` on their FIRST deploy. Not deployed public and switched afterwards.
#
# That is deliberate. `agentcore deploy` maps resources by CloudFormation logical id, and
# reconfiguring ten deployed runtimes is a second full deploy plus a class of risk that simply
# does not exist if the first one is already right. The customer has not deployed yet, so the
# first deploy is the only one that has to happen.
#
#   1. cdk deploy PpFraudData   VPC, interface endpoints, Aurora
#   2. this script writes agentcore/agentcore.json  networkMode VPC + networkConfig + AURORA_*
#   3. agentcore deploy         ten runtimes, in-VPC from birth
#   4. reference-tools + connect-rds   the Gateway targets, VPC-attached
#   5. cdk deploy PpFraudSites  CloudFront, with the stream proxy attached to the same subnets
#
# WHAT THIS SCRIPT DOES NOT DO
#
# It does not run steps 3-5. Each is a real spend in someone else's account and each has its
# own confirmation, so this prints them as the exact next commands instead of running them for
# you. Step 1 it does run, because there is nothing to decide once the mode is chosen.
#
set -euo pipefail

cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
CDK_DIR="deploy/cdk"
DATA_STACK="${DATA_STACK_NAME:-PpFraudData}"

VPC_ID=""
SUBNETS=""
DB_SG=""
DB_CLUSTER=""
DB_SECRET=""
DB_NAME="postgres"
WITH_NAT="false"
PLAN_ONLY="false"
AURORA_VERSION=""
APPROVAL="any-change"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vpc)        VPC_ID="${2:?--vpc needs a vpc id}"; shift 2 ;;
    --subnets)    SUBNETS="${2:?--subnets needs subnet ids, comma separated}"; shift 2 ;;
    --db-sg)      DB_SG="${2:?--db-sg needs a security group id}"; shift 2 ;;
    --db-cluster) DB_CLUSTER="${2:?--db-cluster needs a cluster identifier}"; shift 2 ;;
    --db-secret)  DB_SECRET="${2:?--db-secret needs a secret ARN}"; shift 2 ;;
    --database)   DB_NAME="${2:?--database needs a name}"; shift 2 ;;
    # The available Aurora PostgreSQL versions differ BY REGION and are withdrawn over time, so
    # this is a flag rather than a constant. A wrong value fails only after CloudFormation has
    # built the whole VPC, so check first:
    #   aws rds describe-db-engine-versions --engine aurora-postgresql --region <r> \
    #     --query 'DBEngineVersions[].EngineVersion'
    --aurora-version) AURORA_VERSION="${2:?--aurora-version needs a version like 16.13}"; shift 2 ;;
    --with-nat)   WITH_NAT="true"; shift ;;
    --plan)       PLAN_ONLY="true"; shift ;;
    # Non-interactive. Kept OFF by default: the stack creates security-group rules, and a
    # customer running this in their own account should see them before they exist.
    --yes|-y)     APPROVAL="never"; shift ;;
    -h|--help)    sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (try --help)" >&2; exit 1 ;;
  esac
done

# Both or neither. The CDK app enforces this too, but failing here costs a second instead of a
# synth, and the message can name the flag the operator actually typed.
if [[ -n "$VPC_ID" && -z "$SUBNETS" ]] || [[ -z "$VPC_ID" && -n "$SUBNETS" ]]; then
  echo "error: --vpc and --subnets go together. Pass both, or neither to create a new VPC." >&2
  exit 1
fi
if [[ -n "$DB_SG" && -z "$VPC_ID" ]]; then
  echo "error: --db-sg describes a database you already run, which implies a VPC." >&2
  echo "       Add --vpc and --subnets." >&2
  exit 1
fi

MODE="create"
[[ -n "$VPC_ID" ]] && MODE="attach"
[[ -n "$DB_SG" ]] && MODE="wire"

read -r ACCOUNT REGION <<<"$("$PY" -c "
import json, sys
targets = json.load(open('agentcore/aws-targets.json'))
if not targets:
    sys.exit('agentcore/aws-targets.json is empty; add your account and region first.')
t = next((x for x in targets if x.get('name') == 'default'), targets[0])
print(t['account'], t['region'])
")"

CALLER="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo unknown)"

cat <<TEXT
==> plan
    mode         $MODE
    account      $ACCOUNT   (current credentials: $CALLER)
    region       $REGION
    vpc          ${VPC_ID:-<created>}
    subnets      ${SUBNETS:-<created, 2 AZs, PRIVATE_ISOLATED>}
    database     ${DB_SG:+<yours, behind $DB_SG>}${DB_SG:-<created: Aurora PostgreSQL Serverless v2, Data API on>}
    egress       $([[ "$WITH_NAT" == true ]] && echo "NAT Gateway" || echo "interface VPC endpoints only, no NAT, no internet route")
TEXT

if [[ "$CALLER" != "unknown" && "$CALLER" != "$ACCOUNT" ]]; then
  echo
  echo "error: your credentials are for $CALLER but agentcore/aws-targets.json says $ACCOUNT." >&2
  echo "       Deploying a VPC and a database into the wrong account is not a recoverable" >&2
  echo "       mistake, so this stops rather than guessing which one you meant." >&2
  exit 1
fi

CTX=(-c "account=$ACCOUNT" -c "region=$REGION" -c "databaseName=$DB_NAME")
[[ -n "$VPC_ID" ]]    && CTX+=(-c "vpcId=$VPC_ID")
[[ -n "$SUBNETS" ]]   && CTX+=(-c "subnetIds=$SUBNETS")
[[ -n "$DB_SG" ]]     && CTX+=(-c "dbSecurityGroupId=$DB_SG")
[[ -n "$DB_CLUSTER" ]] && CTX+=(-c "dbClusterIdentifier=$DB_CLUSTER")
[[ -n "$DB_SECRET" ]] && CTX+=(-c "dbSecretArn=$DB_SECRET")
[[ -n "$AURORA_VERSION" ]] && CTX+=(-c "auroraVersion=$AURORA_VERSION")
[[ "$WITH_NAT" == true ]] && CTX+=(-c "withNat=true")

echo
echo "==> building the CDK app"
(cd "$CDK_DIR" && npm install --silent && npm run build >/dev/null)

if [[ "$PLAN_ONLY" == true ]]; then
  echo "==> synth only (--plan)"
  (cd "$CDK_DIR" && npx cdk synth --app "node dist/bin/data.js" "${CTX[@]}" >/dev/null)
  echo "    synth succeeded. Re-run without --plan to deploy."
  exit 0
fi

echo "==> deploying $DATA_STACK ($MODE)"
(cd "$CDK_DIR" && npx cdk deploy "$DATA_STACK" --app "node dist/bin/data.js" \
  "${CTX[@]}" --require-approval "$APPROVAL")

# ---------------------------------------------------------------------------
# Read the outputs back. Never ask anyone to copy an ARN by hand -- that is where a
# subnet id ends up in the wrong field and the failure surfaces as a timeout.
# ---------------------------------------------------------------------------
output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$DATA_STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

OUT_SUBNETS="$(output SubnetIds)"
OUT_SG="$(output ClientSecurityGroupId)"
OUT_VPC="$(output VpcId)"
OUT_CLUSTER="$(output ClusterArn)"
OUT_SECRET="$(output SecretArn)"
OUT_DB="$(output DatabaseName)"
OUT_DATA_API="$(output DataApiEnabled)"

if [[ -z "$OUT_SUBNETS" || "$OUT_SUBNETS" == "None" ]]; then
  echo "error: $DATA_STACK produced no SubnetIds output. Nothing was written to" >&2
  echo "       agentcore.json; inspect the stack before continuing." >&2
  exit 1
fi

# Not a secret -- ARNs and ids. Gitignored anyway, because it describes the customer's network.
"$PY" - "$OUT_VPC" "$OUT_SUBNETS" "$OUT_SG" "$OUT_CLUSTER" "$OUT_SECRET" "$OUT_DB" <<'PYEOF'
import json, sys
vpc, subnets, sg, cluster, secret, database = sys.argv[1:7]
json.dump(
    {
        "vpcId": vpc,
        "subnets": [s for s in subnets.split(",") if s],
        "securityGroups": [sg],
        "clusterArn": cluster,
        "secretArn": secret,
        "database": database,
        "note": "Written by deploy/one-shot.sh from the PpFraudData stack outputs.",
    },
    open("deploy/.vpc-config.json", "w"),
    indent=1,
)
PYEOF
echo "==> wrote deploy/.vpc-config.json"

# ---------------------------------------------------------------------------
# The point of the whole script: agentcore.json carries VPC mode BEFORE the first deploy.
# ---------------------------------------------------------------------------
echo "==> setting networkMode=VPC on every runtime in agentcore/agentcore.json"
"$PY" - "$OUT_SUBNETS" "$OUT_SG" "$OUT_VPC" "$OUT_CLUSTER" "$OUT_SECRET" "$OUT_DB" <<'PYEOF'
import json, sys

subnets, sg, vpc, cluster, secret, database = sys.argv[1:7]
path = "agentcore/agentcore.json"
config = json.load(open(path))

network = {
    "subnets": [s for s in subnets.split(",") if s],
    "securityGroups": [sg],
}
# vpcId is optional -- the CLI resolves it from the subnets and logs
# 'Resolved vpcId for agent "<name>"'. Set it anyway so the file states the intent rather than
# depending on a resolution step to be right.
if vpc and vpc != "None":
    network["vpcId"] = vpc

env_wanted = {}
if cluster and cluster not in ("None", "not-supplied"):
    env_wanted["AURORA_CLUSTER_ARN"] = cluster
if secret and secret not in ("None", "not-supplied"):
    env_wanted["AURORA_SECRET_ARN"] = secret
if database and database != "None":
    env_wanted["AURORA_DATABASE"] = database

for runtime in config.get("runtimes", []):
    runtime["networkMode"] = "VPC"
    runtime["networkConfig"] = network
    if env_wanted:
        env = runtime.setdefault("envVars", [])
        by_name = {e["name"]: e for e in env}
        for name, value in env_wanted.items():
            if name in by_name:
                by_name[name]["value"] = value
            else:
                env.append({"name": name, "value": value})

json.dump(config, open(path, "w"), indent=2)
print(f"    {len(config.get('runtimes', []))} runtimes -> networkMode VPC, {len(network['subnets'])} subnets")
if env_wanted:
    print(f"    plus {', '.join(sorted(env_wanted))}")
PYEOF

echo "==> validating"
agentcore validate --json --directory . >/dev/null
echo "    agentcore validate passes"

cat <<TEXT

Data stack: $DATA_STACK ($MODE)
  vpc              $OUT_VPC
  subnets          $OUT_SUBNETS
  security group   $OUT_SG
  database         $OUT_DB
  cluster          $OUT_CLUSTER
  secret           $OUT_SECRET
  Data API         $OUT_DATA_API

Next, in this order:

  1. Deploy the agents. They come up INSIDE the VPC on this first deploy.
       ./deploy/vendor.sh
       agentcore deploy --target default --yes

  2. Prove the database is reachable before pointing the agents at it.
       ./deploy/connect-rds.sh --probe

  3. Switch the specialists from fixtures to the real cluster (only if step 2 was clean).
       ./deploy/connect-rds.sh --enable
       ./deploy/vendor.sh && agentcore deploy --target default --yes

  4. The websites. The stream proxy joins the same subnets, which is what lets it reach the
     private database -- and it still reaches bedrock-agentcore, because this stack created the
     interface endpoint for it.
       ./deploy/deploy-sites.sh -c vpcId=$OUT_VPC -c subnetIds=$OUT_SUBNETS -c securityGroupIds=$OUT_SG

If step 2 reports the Data API is unavailable, use the VPC-attached Gateway Lambda instead:
       ./deploy/connect-rds.sh --vpc $OUT_SUBNETS $OUT_SG
TEXT
