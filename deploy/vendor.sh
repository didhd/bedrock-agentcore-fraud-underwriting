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
PYTHON_BIN="${PYTHON:-python3}"

SHARED=(prompts signal_layer agents fixtures)
# app/specialist is ONE directory shared by all eight specialist runtimes -- they
# declare the same codeLocation and differ only by SPECIALIST_DOMAIN -- so it is
# vendored once, not eight times.
RUNTIMES=(app/underwriter app/specialist app/francis)

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
  # Every MODULE in app/, and none of its per-runtime DIRECTORIES. The glob is the
  # point: this used to name two files explicitly, and adding
  # app/distributed_fanout.py without adding it here deployed an orchestrator that
  # raised `ModuleNotFoundError: No module named 'app.distributed_fanout'` on EVERY
  # invocation -- including the ones that would never have used it. A glob grows on
  # its own; a list has to be remembered.
  #
  # Directories are excluded, not incidentally but necessarily: app/ also holds
  # underwriter/, specialist/ and francis/, and copying those into each other is how
  # you get app/underwriter/app/underwriter/app/...
  mkdir -p "$rt/app"
  for f in app/*.py; do
    cp "$f" "$rt/app/"
  done
  # strip caches so they never land in the zip
  find "$rt" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

  # VERIFY, do not trust. Every `app.<module>` this runtime imports must now exist
  # next to it. Vendoring is the step whose failure looks like a 30-second
  # initialisation timeout or a ModuleNotFoundError at request time, both of which
  # point somewhere other than here -- so it is checked at the moment it is cheap.
  missing=""
  while read -r mod; do
    [ -z "$mod" ] && continue
    [ -f "$rt/app/$mod.py" ] || missing="$missing app.$mod"
  done < <(grep -ho 'from app\.[a-z_]*' "$rt/main.py" 2>/dev/null | sed 's/from app\.//' | sort -u)
  if [ -n "$missing" ]; then
    echo "ERROR: $rt/main.py imports modules that were not vendored:$missing" >&2
    echo "       They are probably not in app/*.py, or app/ has grown a package." >&2
    exit 1
  fi

  # The check above only reads main.py's own top-level `from app.X` lines, which is not
  # enough. `app/runtime_support.py` imports `signal_layer.sources.aurora` INSIDE the
  # SIGNAL_MODE branch, so a submodule that was never vendored passes CI, passes
  # `agentcore validate`, deploys clean, and then raises ModuleNotFoundError on the first
  # aurora-mode invocation -- in front of the customer, looking like a broken agent.
  #
  # So: resolve every dotted import of a SHARED package found anywhere in the vendored
  # tree, wherever in the file it appears.
  deep_missing="$("$PYTHON_BIN" - "$rt" "${SHARED[@]}" <<'PYEOF'
import pathlib, re, sys

root = pathlib.Path(sys.argv[1])
shared = set(sys.argv[2:])
pattern = re.compile(
    r"^\s*(?:from\s+(" + "|".join(map(re.escape, sorted(shared))) + r")((?:\.\w+)+)\s+import"
    r"|\s*import\s+(" + "|".join(map(re.escape, sorted(shared))) + r")((?:\.\w+)+))",
    re.MULTILINE,
)

missing = set()
for source in root.rglob("*.py"):
    if "__pycache__" in source.parts:
        continue
    text = source.read_text(encoding="utf-8", errors="replace")
    for match in pattern.finditer(text):
        package = match.group(1) or match.group(3)
        trail = match.group(2) or match.group(4)
        parts = [package, *trail.strip(".").split(".")]
        # The whole dotted path left of `import` must resolve, as a module file or as a
        # package directory. There is deliberately NO "well, the parent exists" fallback: the
        # first version of this had one, and since `signal_layer/sources/` exists for any
        # `signal_layer.sources.anything`, it accepted every typo and the check was a no-op.
        # Caught by mutating the source to import a module that does not exist and watching
        # vendor.sh pass.
        base = root.joinpath(*parts)
        if base.with_suffix(".py").exists() or (base / "__init__.py").exists():
            continue
        missing.add(".".join(parts))

print(" ".join(sorted(missing)))
PYEOF
)"
  if [ -n "${deep_missing// /}" ]; then
    echo "ERROR: $rt has unresolvable imports of shared packages: $deep_missing" >&2
    echo "       The vendored copy is stale or the submodule does not exist. These are" >&2
    echo "       lazily imported, so nothing else in the pipeline would catch them." >&2
    exit 1
  fi

  echo "vendored [${SHARED[*]} data app] into $rt"
done

echo "OK. Next: agentcore package && agentcore deploy --target default --yes"
echo "Note: vendored copies are git-ignored (see .gitignore); do not commit them."
