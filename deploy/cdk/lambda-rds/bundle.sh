#!/usr/bin/env bash
#
# Build the read-only SQL Lambda's deployment zip.
#
#   ./deploy/cdk/lambda-rds/bundle.sh            # Data API transport, no dependencies
#   ./deploy/cdk/lambda-rds/bundle.sh --socket   # bundle pg8000 for the in-VPC socket path
#
# WHY THE CONFIDENTIAL SQL IS GENERATED HERE AND NOT COMMITTED
#
# The Lambda needs the customer's verified SQL at runtime in AWS. That SQL is confidential and
# `prompts/verbatim/` is gitignored, so it cannot be checked in beside `main.py`.
#
# So it is GENERATED into the build directory at bundle time, from the gitignored semantic
# model, and the generated file is itself gitignored. Exactly the arrangement
# `deploy/reference-tools.sh` already uses for the alert dictionary: the generator is committed,
# the payload is not.
#
# The zip is a build artefact. Nothing here is added to git.
#
set -euo pipefail

cd "$(dirname "$0")/../../.."

PY="${PYTHON:-python3}"
SRC="deploy/cdk/lambda-rds"
BUILD="${BUILD_DIR:-/tmp/pp-rds-build}"
PAYLOAD="$SRC/use_cases.json"

WITH_SOCKET="false"
[[ "${1:-}" == "--socket" ]] && WITH_SOCKET="true"

echo "==> generating the use-case payload from the semantic model"
"$PY" - "$PAYLOAD" <<'PYEOF'
import json, sys

sys.path.insert(0, ".")

from tools.usecases import USE_CASES, unavailable

path = sys.argv[1]

if not USE_CASES:
    # A hard stop, not a warning. A Lambda deployed with an empty payload answers
    # `list_use_cases` with nothing and looks like a working tool with no capabilities, which
    # is far harder to diagnose than a failed build.
    sys.exit(
        f"cannot bundle: {unavailable()}\n"
        "       The verified SQL is customer confidential; restore prompts/verbatim/ first."
    )

payload = {
    "generated_from": "tools.usecases (bound to prompts/verbatim/FRAUDBOT_SEMANTIC_VIEW.yaml)",
    "note": (
        "CONFIDENTIAL. The customer's own verified SQL. Generated at bundle time and "
        "gitignored; never commit this file."
    ),
    "use_cases": {
        slug: {
            "title": uc.title,
            "intent": uc.intent,
            "description": uc.description,
            "domains": list(uc.domains),
            "tables": list(uc.tables),
            "databases": list(uc.databases),
            "notes": uc.notes,
            "parameters": [
                {
                    "name": p.name,
                    "description": p.description,
                    "pattern": p.pattern,
                    "required": p.required,
                    "example": p.example,
                }
                for p in uc.parameters
            ],
            "variants": [
                {
                    "question": q.question,
                    "sql": q.sql,
                    # Resolved HERE, at bundle time, rather than in the Lambda: the four
                    # lender-volume queries are not identically shaped, so which parameters can
                    # actually be applied differs per variant. Computing it once means
                    # list_use_cases reports the truth instead of the intention.
                    "substitutable": list(uc.substitutable(i)),
                    # The read allowlist for THIS statement. See
                    # UseCase.referenced_tables for why it comes from the query and
                    # not from the semantic model.
                    "reads": list(uc.referenced_tables(i)),
                }
                for i, q in enumerate(uc.queries)
            ],
        }
        for slug, uc in USE_CASES.items()
    },
}

json.dump(payload, open(path, "w"), indent=1)
queries = sum(len(v["variants"]) for v in payload["use_cases"].values())
print(f"    {len(payload['use_cases'])} use cases, {queries} verified queries")
PYEOF

# Refuse to build if the payload could be committed. The generator is safe to publish; the
# payload is the customer's SQL, and a `git add -A` by anyone is all it takes.
if ! git check-ignore -q "$PAYLOAD"; then
  echo "error: $PAYLOAD is NOT gitignored, and it contains the customer's verified SQL." >&2
  echo "       Add it to .gitignore before building." >&2
  exit 1
fi
echo "    confirmed gitignored"

echo "==> packaging"
rm -rf "$BUILD" /tmp/pp-rds.zip
mkdir -p "$BUILD"
cp "$SRC/main.py" "$BUILD/"
cp "$PAYLOAD" "$BUILD/"

if [[ "$WITH_SOCKET" == true ]]; then
  # pg8000 and not psycopg2: pure Python, so it installs from a platform-independent wheel and
  # this does not have to cross-compile a manylinux binary from a Mac.
  "$PY" -m pip install --quiet --target "$BUILD" 'pg8000>=1.31,<2' >/dev/null
  echo "    pg8000 bundled (socket transport)"
else
  echo "    no dependencies (Data API transport; boto3 is in the Lambda runtime)"
fi

(cd "$BUILD" && zip -q -r /tmp/pp-rds.zip .)
echo "    /tmp/pp-rds.zip  $(du -h /tmp/pp-rds.zip | cut -f1)"
