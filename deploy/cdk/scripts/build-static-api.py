#!/usr/bin/env python3
"""Generate the demo UI's build-time API surface for a compute-free deployment.

THE IDEA

Of the four endpoints `ui/server.py` serves, only one needs a running process:

    /api/health              constant for a given build
    /api/applications        the fixture list; changes when fixtures/ changes
    /api/payload/{id}        one application's signal payload; likewise
    /api/stream/{id}         a live 9-model run -- genuinely dynamic

So three of them are emitted here as plain JSON files, uploaded to S3, and served
by CloudFront with no Lambda in the path. Only `/api/stream/*` routes to compute.
That is why the demo's steady-state cost is an S3 GET.

WHY THIS SCRIPT AND NOT A HAND-WRITTEN JSON

Every value below comes from the same modules the runtime uses -- `agents.pricing`
for the rate card, `agents.models` for the per-agent model and tier,
`prompts.loader` for the domain order and the analysis titles, `fixtures` for the
application list. Nothing is retyped. A rate card copied into JSON is a rate card
that silently goes stale the next time AWS reprices a model, which this repo has
already had happen once (GPT-5.6 Luna, 2026-07-30).

`bootstrap.json` additionally ships inside the Lambda bundle, because the UI's
`run_started` frame carries per-agent `rate` and `analysis_title` that the runtime
does not send, and reimplementing pricing in JavaScript to fill them in would be a
second source of truth for the number the customer cares most about.

Usage:  python3 deploy/cdk/scripts/build-static-api.py --out deploy/cdk/.build
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="directory to write into")
    args = parser.parse_args()

    out = Path(args.out)
    (out / "api").mkdir(parents=True, exist_ok=True)
    (out / "api" / "payload").mkdir(parents=True, exist_ok=True)

    from agents.models import SYNTHESIZER_MODEL, model_for, tier_label
    from prompts.loader import AGENT_NAMES, DOMAINS
    from app.runtime_support import ANALYSIS_TITLES

    # `ui.server._rate_for` is reused rather than reimplemented, and that is the
    # whole point of importing a server module into a build script. It already
    # handles the three things a fresh implementation gets wrong: the Claude card
    # and the GPT-5.6 card live in DIFFERENT modules (agents.pricing vs
    # agents.mantle, and pricing knows nothing about mantle ids); `rates_for`
    # returns a dict keyed "input"/"output"/"cache_read"/"cache_write_applied", not
    # a positional tuple; and it emits exactly the four keys the UI's `rateLabel`
    # reads. An earlier version of that same helper guessed at module-level dict
    # names and returned None for eight of the nine agents -- duplicating the logic
    # here is how that returns.
    from ui.server import _rate_for as rate_of  # noqa: PLC2701

    def _config_of(role: str):
        try:
            from agents.agent_config import agent_config  # noqa: PLC0415

            return agent_config(role)
        except Exception:
            return None

    bootstrap = {
        "generated_from": "agents.models + agents.pricing + prompts.loader "
        "(never hand-written; see this script's docstring)",
        "synthesizer_model": SYNTHESIZER_MODEL,
        "synthesizer_tier": tier_label(SYNTHESIZER_MODEL),
        "synthesizer_rate": rate_of(SYNTHESIZER_MODEL),
        "synthesizer_config": _config_of("synthesizer"),
        "agents": [
            {
                "domain": domain,
                "agent_name": AGENT_NAMES[domain],
                "model": model_for(domain),
                "tier": tier_label(model_for(domain)),
                "rate": rate_of(model_for(domain)),
                # bustout and rings define NO title in the customer's prompts
                # ("Return only the final analysis"), so this is null for them and
                # nothing here may invent one.
                "analysis_title": ANALYSIS_TITLES.get(domain),
                # The exact request-time config -- reasoning on/off and its effort,
                # max output tokens, prompt provenance and size, prompt-cache verdict.
                # Baked so the CloudFront tooltip shows the same facts localhost does.
                # NOTE: prompt TEXT is deliberately absent; it is customer-confidential
                # and this JSON is served from a public URL.
                "config": _config_of(domain),
            }
            for domain in DOMAINS
        ],
    }
    (out / "bootstrap.json").write_text(json.dumps(bootstrap, indent=1), encoding="utf-8")

    # ---- The three static endpoints ----------------------------------------
    #
    # These CALL ui/server.py's own route functions and bake their responses,
    # rather than rebuilding the payloads. The UI is already known to work against
    # those exact bodies on localhost:8080, so baking them means the CloudFront
    # deployment serves byte-identical JSON -- no second projection of the signal
    # layer to drift, and no chance of the deployed selector disagreeing with the
    # local one about which fixtures exist.
    from fixtures.loader import list_application_ids
    from ui.server import agents as agents_route
    from ui.server import applications as applications_route
    from ui.server import health as health_route
    from ui.server import payload as payload_route

    def bake(path: Path, response) -> None:
        path.write_bytes(response.body)

    # /api/health is baked from a process where MOCK_MODE=0, so it reports
    # measures_tokens=true. That is only honest because the deployed runtime also
    # has MOCK_MODE=0 (agentcore/agentcore.json). The two have to move together:
    # deploy the runtime in mock mode and this file becomes a lie that the UI
    # renders verbatim. deploy/deploy-sites.sh exports MOCK_MODE=0 for that reason.
    # NO `.json` extension, deliberately. The UI fetches `/api/health`,
    # `/api/applications` and `/api/payload/APP-1004` -- extensionless paths, because
    # on localhost they are FastAPI routes. For CloudFront to answer those same URLs
    # from S3, the object keys have to match them exactly. Adding `.json` would mean
    # either editing every fetch in the UI or adding a rewrite function, and both are
    # more moving parts than naming the file correctly. The Content-Type is set on
    # the bucket deployment instead of being inferred from the name; see
    # deploy/cdk/lib/sites-stack.ts.
    bake(out / "api" / "health", health_route())
    bake(out / "api" / "applications", applications_route())

    # /api/agents WITHOUT prompt text. UI_INCLUDE_PROMPT=0 is set for this call only:
    # the object is uploaded to a public, unauthenticated CloudFront URL, and the nine
    # prompts are customer-confidential. The local server defaults to including them
    # because it runs on the operator's own machine against their own docs/.
    import os as _os

    _prior = _os.environ.get("UI_INCLUDE_PROMPT")
    _os.environ["UI_INCLUDE_PROMPT"] = "0"
    try:
        bake(out / "api" / "agents", agents_route())
    finally:
        if _prior is None:
            _os.environ.pop("UI_INCLUDE_PROMPT", None)
        else:
            _os.environ["UI_INCLUDE_PROMPT"] = _prior

    ids = list(list_application_ids())
    for app_id in ids:
        bake(out / "api" / "payload" / app_id, payload_route(app_id))

    print(f"bootstrap.json    : {len(bootstrap['agents'])} agents, "
          f"synthesizer {bootstrap['synthesizer_model']}")
    print(f"api/health        : baked from ui.server.health()")
    print(f"api/applications  : baked from ui.server.applications()")
    print(f"api/payload/*     : {len(ids)} files -> {', '.join(ids[:4])}"
          f"{' ...' if len(ids) > 4 else ''}")
    print(f"wrote into {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
