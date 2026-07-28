#!/usr/bin/env bash
#
# Non-interactive pre-deploy gate. Creates no AWS resources.
#
# Runs, in order:
#   0. dead-command guard  - fail if `agentcore configure` / `agentcore launch` reappear anywhere
#   1. agentcore validate  - Zod-validate agentcore/*.json (root spec is .strict())
#   2. pytest              - prompt fidelity + output contracts
#   3. agentcore package   - build each runtime's CodeZip bundle offline
#   4. agentcore deploy --dry-run  - synth CloudFormation, resolve target, do not deploy
#
# Steps 1-3 are fully offline. Step 4 needs read-only AWS credentials (it resolves
# the target, checks bootstrap status and stack deployability) and is skipped when
# aws-targets.json is still the empty array `create` writes, or when
# CI_SKIP_DRY_RUN=1. It never mutates anything: the CLI returns before
# `deployStack` when --dry-run is set.
#
# Why step 0 exists: `agentcore configure` and `agentcore launch` do not exist in
# @aws/agentcore. The npm CLI rejects them (exit 1), but the deprecated pip
# package bedrock-agentcore-starter-toolkit installs a *different* binary also
# named `agentcore` on which those subcommands DO parse and then no-op. Whichever
# resolves first on PATH decides. A CI script that shells out to them can
# therefore appear to succeed while deploying nothing, which is exactly the
# failure this repo shipped with. Grep is cheaper than discovering it in prod.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log()  { printf '\n=== %s\n' "$*"; }
fail() { printf '\nFAIL: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. Dead-command guard
# ---------------------------------------------------------------------------
log "0/4 guard: no 'agentcore configure' / 'agentcore launch' anywhere in the repo"

# Scope: tracked files PLUS untracked-but-not-ignored ones, so a config that has
# been written but not yet committed is still checked, while a stale .venv or a
# vendored node_modules (both gitignored) cannot trip the guard.
# --fixed-strings: these are literal command strings, not regexes.
#
# Exempt, because naming the commands is the whole point of these files:
#   deploy/ci.sh        - this guard
#   deploy/deploy.md    - documents that they do not exist
#   evals/README.md     - same warning for the eval workflow
#   tests/test_bench.py - asserts the warning is present and not in a code block
# `git ls-files --cached` also lists tracked-but-deleted paths, so filter to files
# that actually exist before grepping; otherwise grep reports them as missing and
# the guard's output becomes noise.
GUARD_FILES=()
while IFS= read -r -d '' candidate; do
  [ -f "$candidate" ] && GUARD_FILES+=("$candidate")
done < <(
  git ls-files -z --cached --others --exclude-standard --deduplicate \
    -- ':!deploy/ci.sh' ':!deploy/deploy.md' ':!evals/README.md' ':!tests/test_bench.py'
)

DEAD_HITS=""
if [ "${#GUARD_FILES[@]}" -gt 0 ]; then
  DEAD_HITS="$(
    grep -n -I --fixed-strings \
      -e 'agentcore configure' \
      -e 'agentcore launch' \
      /dev/null "${GUARD_FILES[@]}" || true
  )"
fi
if [ -n "$DEAD_HITS" ]; then
  printf '%s\n' "$DEAD_HITS" >&2
  fail "the lines above reference AgentCore subcommands that DO NOT EXIST.
      'agentcore configure' and 'agentcore launch' are rejected by @aws/agentcore, but the
      deprecated pip starter toolkit ships a colliding binary on which they parse and no-op --
      so whichever 'agentcore' resolves first on PATH decides whether your deploy is real.
      The actual sequence is:
        agentcore create / agentcore add agent
        (cd agentcore/cdk && npm install)
        agentcore validate
        agentcore deploy --dry-run
        agentcore deploy --target <target> --yes
      Fix the files above, or add them to this guard's exemption list if their purpose is to
      warn about the dead commands (see the comment above this check)."
fi
echo "ok: no references to the non-existent subcommands"

# The colliding binary must not be installed either: with it on PATH ahead of the
# npm CLI, every `agentcore` call in this script silently targets the wrong tool.
if python3 -m pip show bedrock-agentcore-starter-toolkit >/dev/null 2>&1; then
  fail "bedrock-agentcore-starter-toolkit is installed. Its console script is also named
      'agentcore' and collides with the npm CLI. Run: pip uninstall -y bedrock-agentcore-starter-toolkit"
fi
echo "ok: bedrock-agentcore-starter-toolkit is not installed"

command -v agentcore >/dev/null 2>&1 \
  || fail "agentcore CLI not found. Install it: npm install -g @aws/agentcore"

CLI_VERSION="$(agentcore --version)"
echo "ok: agentcore CLI ${CLI_VERSION}"

# ---------------------------------------------------------------------------
# 1. Config validation
# ---------------------------------------------------------------------------
log "1/4 agentcore validate --json"
# --json makes validate non-interactive; it exits 1 with {"success":false,...} on
# any Zod failure, including an unknown root key (the root spec is .strict()).
agentcore validate --json --directory "$REPO_ROOT"

# ---------------------------------------------------------------------------
# 2. Tests
# ---------------------------------------------------------------------------
log "2/4 pytest"
# --ignore=agentcore: a previous `agentcore package` leaves agentcore/<runtime>/staging/,
# the runtime's full dependency closure. Several third-party wheels in there ship
# their own test modules with colliding basenames (e.g. two copies of
# annotated_types/test_cases.py), which makes pytest abort during collection with
# "import file mismatch" before it reaches a single one of OUR tests. It is a
# build artifact, so it is excluded rather than cleaned -- a stale zip is still a
# valid input to the dry run below.
python3 -m pytest -q --ignore=agentcore

# ---------------------------------------------------------------------------
# 3. Package
# ---------------------------------------------------------------------------
log "3/4 agentcore package"
# Resolves each runtime's pyproject.toml, installs its dependency closure for the
# declared runtimeVersion, and writes agentcore/<runtime>.zip. Offline w.r.t. AWS,
# but it does hit PyPI. Fails loudly if a codeLocation has no pyproject.toml.
agentcore package --directory "$REPO_ROOT"

# ---------------------------------------------------------------------------
# 4. Dry-run deploy
# ---------------------------------------------------------------------------
TARGET="${AGENTCORE_TARGET:-default}"

if [ "${CI_SKIP_DRY_RUN:-0}" = "1" ]; then
  log "4/4 agentcore deploy --dry-run  [SKIPPED: CI_SKIP_DRY_RUN=1]"
  exit 0
fi

# `create` writes aws-targets.json as []. Until a real target is added, a dry run
# can only fail with 'Target "default" not found', which is not a code defect.
if ! python3 -c "
import json, sys
targets = json.load(open('agentcore/aws-targets.json'))
sys.exit(0 if targets else 1)
"; then
  log "4/4 agentcore deploy --dry-run  [SKIPPED: agentcore/aws-targets.json is empty]"
  echo "Add a target before deploying, e.g.:"
  echo '  [{"name":"default","account":"<12-digit account>","region":"us-east-1"}]'
  exit 0
fi

log "4/4 agentcore deploy --dry-run --target ${TARGET}"
# NOTE: --dry-run, not --plan. The AWS docs' --plan flag does not exist in this
# CLI and is rejected by the option parser (exit 1).
# NOTE: deploy has no --region / --profile / --agent flag. Region and account come
# from agentcore/aws-targets.json; credentials come from the ambient chain, so
# AWS_REGION must be exported explicitly (the Python SDK's fallback ignores
# AWS_DEFAULT_REGION and hard-codes us-west-2).
: "${AWS_REGION:?AWS_REGION must be set explicitly before a dry-run deploy}"

# --dry-run implies --json here so the gate stays machine-readable; deploy exits 2
# (not 1) when it succeeds with post-deploy warnings, so treat 2 as a pass.
set +e
agentcore deploy --dry-run --target "$TARGET" --json
DEPLOY_RC=$?
set -e
if [ "$DEPLOY_RC" -ne 0 ] && [ "$DEPLOY_RC" -ne 2 ]; then
  fail "agentcore deploy --dry-run exited ${DEPLOY_RC}"
fi

log "gate passed"
