#!/usr/bin/env python3
"""Merge table names into the SQL Lambda's RDS_TABLE_ALLOWLIST, preserving what is there."""
import json, sys
env = json.loads(sys.argv[1] or "{}") or {}
existing = [t.strip() for t in (env.get("RDS_TABLE_ALLOWLIST") or "").split(",") if t.strip()]
added = [t.strip().lower() for t in sys.argv[2].split(",") if t.strip()]
# MERGED, not replaced, and order preserved: re-running with one more table must not silently
# revoke the ones already granted.
merged = list(dict.fromkeys(existing + added))
env["RDS_TABLE_ALLOWLIST"] = ",".join(merged)
with open(sys.argv[3], "w") as handle:
    json.dump({"Variables": env}, handle)
print(f"    allowlist: {len(existing)} -> {len(merged)} table(s)", file=sys.stderr)
