#!/usr/bin/env python3
"""Prove that ONE trace spans all nine runtimes.

    python3 deploy/verify-tracing.py

WHAT IT ACTUALLY CHECKS

The distributed topology splits the eight specialists into eight independent AgentCore
runtimes. Independent runtimes produce eight unrelated traces unless the parent context
travels with each request, so "tracing works" is not something to assume from a green
deploy -- the failure looks identical to success in every place except the trace view.

So this script does the one thing that settles it: it starts a trace of its own, invokes
the orchestrator with that trace as the parent, and then reads every runtime's `spans` log
stream to see whether the specialists reported under the SAME trace id. A pass means the
span tree in the CloudWatch GenAI Observability console is one tree rooted at the
orchestrator. A fail means nine disconnected traces, which is worse than no tracing
because it looks like tracing.

READ THE LOG GROUPS, NOT X-RAY. This script asked `xray:BatchGetTraces` first and got
"no spans on trace ..." for a trace that had ten of them. With the unified span
destination -- the default for new agents in this region -- AgentCore delivers spans to
each agent's own log group rather than to X-Ray's trace store, so BatchGetTraces returns
an empty result that is indistinguishable from propagation being broken.

HOW THE CONTEXT TRAVELS

    this script  --traceParent-->  orchestrator  --traceParent-->  8 specialists

`InvokeAgentRuntime` carries W3C trace context as request parameters (botocore's
bedrock-agentcore/2024-02-28 model): `traceParent` -> header `traceparent`, plus
`traceState`, `baggage`, and the X-Ray-format `traceId` -> `X-Amzn-Trace-Id`.

This script passes them explicitly because it runs on a laptop with no ADOT to do it.
The orchestrator does NOT: inside an instrumented runtime, ADOT's botocore patch already
propagates, and supplying the parameters as well changes a request botocore has signed,
which fails with "The request signature we calculated does not match". See
`app/distributed_fanout._trace_headers`. `runtimeSessionId` IS passed on every hop -- it
is an ordinary API parameter -- and reusing the orchestrator's value is what puts all
nine runtimes in one session.

WHY THE TRACE ID IS BUILT FROM AN X-RAY ID AND NOT AT RANDOM

X-Ray trace ids are not opaque: `1-<8 hex seconds since epoch>-<24 hex random>`. The
timestamp is real and X-Ray rejects a trace whose time is implausible, so a randomly
generated 32-hex W3C id would be accepted on the wire and then be unqueryable. This
builds the 32-hex W3C id from a valid X-Ray id so the same trace is addressable in both
formats.

Requires CloudWatch Transaction Search, without which AgentCore cannot deliver spans to
an agent's log group at all. Ingestion is asynchronous, so the script polls -- and it
polls for a SECOND service to appear, because the orchestrator's own spans arriving prove
nothing about propagation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE = REPO_ROOT / "agentcore" / ".cli" / "deployed-state.json"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

_RUNTIME_ARN = re.compile(
    r"arn:aws[a-z-]*:bedrock-agentcore:[^:]+:\d+:runtime/[A-Za-z0-9_.-]+"
)

SPECIALISTS = (
    "identity",
    "dealer",
    "straw",
    "employment",
    "income",
    "synthetic",
    "bustout",
    "rings",
)


def runtime_arns() -> dict[str, str]:
    """Every deployed runtime ARN, keyed by the short name inside it."""
    if not STATE.is_file():
        return {}
    out: dict[str, str] = {}
    for arn in sorted(set(_RUNTIME_ARN.findall(STATE.read_text(encoding="utf-8")))):
        # arn:...:runtime/ppFraud_identity-AB12CD34  ->  identity
        tail = arn.rsplit("/", 1)[-1]
        name = tail.split("-", 1)[0]
        name = name.split("_", 1)[-1] if "_" in name else name
        out[name] = arn
    return out


def new_xray_trace_id() -> tuple[str, str]:
    """``(xray_id, w3c_id)`` for the same trace.

    X-Ray: ``1-<hex epoch seconds>-<24 hex>``. W3C: the same 32 hex digits with no
    separators. Built in that direction, not the other, because the embedded timestamp
    has to be real for X-Ray to accept and index the trace.
    """
    epoch_hex = format(int(time.time()), "08x")
    rand_hex = secrets.token_hex(12)  # 24 hex chars
    return f"1-{epoch_hex}-{rand_hex}", f"{epoch_hex}{rand_hex}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--application-id", default="APP-1004")
    parser.add_argument(
        "--wait",
        type=int,
        default=150,
        help="seconds to poll for spans (default: 150; ingestion is async)",
    )
    parser.add_argument(
        "--timeout", type=int, default=420, help="invoke read timeout in seconds"
    )
    args = parser.parse_args(argv)

    import boto3
    from botocore.config import Config

    arns = runtime_arns()
    orchestrator = arns.get("underwriter")
    if not orchestrator:
        print(f"{RED}no underwriter runtime in {STATE}. Deploy first.{RESET}")
        return 1

    region = orchestrator.split(":")[3]
    deployed_specialists = [d for d in SPECIALISTS if d in arns]
    print(f"region      : {region}")
    print(f"orchestrator: {orchestrator.rsplit('/', 1)[-1]}")
    print(
        f"specialists : {len(deployed_specialists)}/8 deployed"
        + (f"  {DIM}missing: {[d for d in SPECIALISTS if d not in arns]}{RESET}"
           if len(deployed_specialists) < 8 else "")
    )
    if not deployed_specialists:
        print(
            f"\n{YELLOW}No specialist runtimes are deployed, so there is nothing to "
            f"trace ACROSS. This script only means something for the distributed "
            f"topology.{RESET}"
        )
        return 1

    xray_id, w3c_id = new_xray_trace_id()
    parent_span = secrets.token_hex(8)
    traceparent = f"00-{w3c_id}-{parent_span}-01"  # 01 = sampled
    session_id = str(uuid.uuid4())

    print(f"\ntrace id    : {xray_id}")
    print(f"traceparent : {traceparent}")
    print(f"session id  : {session_id}")

    core = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(read_timeout=args.timeout, connect_timeout=15,
                      retries={"max_attempts": 0}),
    )

    print(f"\n{DIM}invoking the orchestrator with FANOUT_MODE as deployed...{RESET}")
    started = time.monotonic()
    try:
        response = core.invoke_agent_runtime(
            agentRuntimeArn=orchestrator,
            runtimeSessionId=session_id,
            qualifier="DEFAULT",
            payload=json.dumps({"application_id": args.application_id}).encode(),
            # Both formats. X-Ray indexes on traceId; ADOT and the W3C propagator in
            # app/distributed_fanout.py continue from traceParent.
            traceId=f"Root={xray_id};Parent={parent_span};Sampled=1",
            traceParent=traceparent,
        )
        body = response["response"]
        raw = body.read() if hasattr(body, "read") else b"".join(body)
    except Exception as exc:  # noqa: BLE001
        print(f"{RED}invoke failed: {type(exc).__name__}: {exc}{RESET}")
        return 1

    wall = time.monotonic() - started
    frames = [
        json.loads(line[6:])
        for line in raw.decode("utf-8", "replace").splitlines()
        if line.startswith("data: ")
    ]
    done = [f for f in frames if f.get("event") == "agent_done"]
    failed = [f for f in frames if f.get("event") == "agent_failed"]
    transports = Counter(f.get("transport") or "in-process" for f in done)
    errors = [f for f in frames if f.get("event") == "error"]

    print(f"  {wall:.1f}s, {len(frames)} frames, {len(done)} done, {len(failed)} failed")
    print(f"  transport   : {dict(transports)}")
    for f in failed:
        print(f"  {YELLOW}failed {f.get('domain')}: {str(f.get('error'))[:150]}{RESET}")
    for f in errors:
        print(f"  {RED}error: {str(f.get('error'))[:200]}{RESET}")

    overheads = [
        f["invocation_overhead_ms"]
        for f in done
        if isinstance(f.get("invocation_overhead_ms"), (int, float))
    ]
    if overheads:
        print(
            f"  {DIM}AgentCore invocation overhead per specialist: "
            f"min {min(overheads):.0f}ms  median "
            f"{sorted(overheads)[len(overheads) // 2]:.0f}ms  max {max(overheads):.0f}ms"
            f"  (n={len(overheads)}){RESET}"
        )

    # ---- the actual question -------------------------------------------------
    #
    # CloudWatch Logs, NOT X-Ray BatchGetTraces. This script asked X-Ray first and got
    # "no spans on trace ..." for a trace that had ten of them: with the unified span
    # destination (the default for new agents in this region) AgentCore delivers spans to
    # each agent's OWN log group, not to X-Ray's trace store, so BatchGetTraces returns
    # nothing and the failure is indistinguishable from broken propagation. The spans are
    # in `/aws/bedrock-agentcore/runtimes/<id>-DEFAULT`, stream `spans`.
    logs = boto3.client("logs", region_name=region)
    groups = [
        f"/aws/bedrock-agentcore/runtimes/{a.rsplit('/', 1)[-1]}-DEFAULT"
        for a in [orchestrator, *[arns[d] for d in deployed_specialists]]
    ]
    print(f"\n{DIM}reading spans from {len(groups)} runtime log groups "
          f"(ingestion is async)...{RESET}")

    by_service: Counter = Counter()
    span_names: Counter = Counter()
    other_traces: Counter = Counter()
    sessions: set[str] = set()
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        by_service.clear(); span_names.clear(); other_traces.clear(); sessions.clear()
        for group in groups:
            try:
                events = logs.filter_log_events(
                    logGroupName=group,
                    logStreamNames=["spans"],
                    startTime=int((time.time() - 1800) * 1000),
                )["events"]
            except Exception:
                continue  # a specialist that has never run has no `spans` stream yet
            for event in events:
                message = event.get("message", "").strip()
                if not message.startswith("{"):
                    continue
                try:
                    doc = json.loads(message)
                except json.JSONDecodeError:
                    continue
                trace = doc.get("traceId")
                service = (
                    ((doc.get("resource") or {}).get("attributes") or {}).get("service.name")
                    or group.rsplit("/", 1)[-1]
                )
                if trace == w3c_id:
                    by_service[service] += 1
                    if doc.get("name"):
                        span_names[doc["name"]] += 1
                    session = (doc.get("attributes") or {}).get("session.id")
                    if session:
                        sessions.add(session)
                elif trace:
                    other_traces[trace] += 1
        # Wait for the CHILDREN, not just the parent: the orchestrator's own spans
        # appearing proves nothing about propagation.
        if len(by_service) >= 2:
            break
        time.sleep(10)

    total = sum(by_service.values())
    if not total:
        print(
            f"{RED}FAIL{RESET}  no spans carrying trace {w3c_id} after {args.wait}s.\n"
            f"      {len(other_traces)} other trace ids were present, so spans ARE being "
            f"delivered -- the parent context did not propagate."
        )
        return 1

    print(f"\n  {total} spans on ONE trace ({w3c_id}), by service:")
    for name, count in by_service.most_common():
        print(f"    {count:>3}  {name}")
    print(f"\n  span names: {dict(span_names.most_common(6))}")
    print(f"  session ids on this trace: {len(sessions)}"
          + (f"  {DIM}{list(sessions)[0]}{RESET}" if len(sessions) == 1 else ""))

    joined = len(by_service) >= 2
    one_session = len(sessions) <= 1
    mark = f"{GREEN}PASS{RESET}" if joined else f"{RED}FAIL{RESET}"
    print(
        f"\n  {mark}  {len(by_service)} distinct runtimes reported under one trace"
        + ("" if joined else "  -- only the orchestrator did, so the children started "
                            "their own traces")
    )
    if not one_session:
        print(
            f"  {YELLOW}WARN{RESET}  {len(sessions)} session ids on one trace; the "
            f"orchestrator's runtimeSessionId is not reaching every child"
        )
    print(
        f"\n{DIM}View it: "
        f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
        f"#gen-ai-observability{RESET}"
    )
    return 0 if joined else 1


if __name__ == "__main__":
    sys.exit(main())
