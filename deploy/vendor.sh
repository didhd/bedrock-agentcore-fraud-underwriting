#!/usr/bin/env bash
#
# Vendor the shared repo-root packages into each AgentCore runtime's codeLocation.
#
# WHY THIS EXISTS
#   `agentcore package` zips only the runtime's own directory (codeLocation, e.g.
#   app/underwriter/). The shared packages that main.py imports --
#       prompts/  signal_layer/  agents/  fixtures/  data/  app/runtime_support.py
#   -- live ABOVE codeLocation and are therefore NOT included by a plain package.
#   The container then starts and crashes at import with
#       ModuleNotFoundError: No module named 'app'
#   which surfaces as a 30s "Runtime initialization time exceeded" on invoke.
#   main.py's _find_shared_root() looks next to itself first, so copying these
#   packages beside main.py makes both the packaged and the dev layout work.
#
# Run from anywhere:  ./deploy/vendor.sh
# Idempotent. francis keeps its OWN local tools/ (specialists.py); it is never
# overwritten because tools is not in SHARED below.
#
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

SHARED=(prompts signal_layer agents fixtures)
RUNTIMES=(app/underwriter app/francis)

for rt in "${RUNTIMES[@]}"; do
  if [ ! -f "$rt/main.py" ]; then
    echo "skip $rt (no main.py)"; continue
  fi
  for pkg in "${SHARED[@]}"; do
    [ -d "$pkg" ] || { echo "MISSING repo-root package: $pkg" >&2; exit 1; }
    rm -rf "${rt:?}/$pkg"
    cp -R "$pkg" "$rt/$pkg"
  done
  if [ -d data ]; then
    rm -rf "$rt/data"; cp -R data "$rt/data"
  fi
  # app/ is copied selectively (only the shared module) to avoid recursively
  # vendoring app/underwriter and app/francis into themselves.
  mkdir -p "$rt/app"
  cp app/__init__.py app/runtime_support.py "$rt/app/"
  # strip caches so they never land in the zip
  find "$rt" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
  echo "vendored [${SHARED[*]} data app] into $rt"
done

echo "OK. Next: agentcore package && agentcore deploy --target default --yes"
echo "Note: vendored copies are git-ignored (see .gitignore); do not commit them."
