#!/usr/bin/env python3
"""Prove the deployed system really works. One command, pass/fail per check.

    python3 deploy/verify.py

WHY THIS EXISTS

A deployed AgentCore runtime can return HTTP 200 with a complete, plausible,
well-formed SSE stream having called Bedrock **zero** times. `MOCK_MODE` defaults to
``"1"``, so a runtime deployed without that environment variable runs the whole
pipeline offline and reports mock narratives. Nothing about the response shape says
so; one field does.

That is the failure this script exists to catch, and it is not hypothetical -- it is
what the first deployment of this repository did. Alongside it:

  * `agentcore invoke` prints ``"success": true, "response": ""`` for this runtime.
    The CLI renders text deltas and these frames are JSON objects, so the empty
    string is a CLI artefact and says nothing about the run. This script reads the
    stream directly instead.
  * Eight specialists can be paid for and the synthesis still fail, because the
    synthesizer reaches GPT-5.6 through a different IAM namespace
    (`bedrock-mantle:*`) than the Claude specialists.

So the checks below are ordered the way the pipeline fails: reachability, then
whether a model was actually called, then per-agent completion, then the contract,
then cost.

EXIT CODE is 0 only if every check passes, so this is usable in CI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE = REPO_ROOT / "agentcore" / ".cli" / "deployed-state.json"

#: The customer's field order. Checked positionally, not as a set: a synthesizer that
#: emits the right 21 names in the wrong order still breaks a downstream INSERT.
EXPECTED_FIRST_KEY = "master_summary"
EXPECTED_KEY_COUNT = 21

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

_RUNTIME_ARN = re.compile(
    r"arn:aws[a-z-]*:bedrock-agentcore:[^:]+:\d+:runtime/[A-Za-z0-9_.-]+"
)


class Checks:
    """Accumulates results so one failure does not hide the others."""

    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def add(self, ok: bool, name: str, detail: str = "") -> bool:
        self.rows.append((ok, name, detail))
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  {mark}  {name}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
        return ok

    @property
    def failed(self) -> int:
        return sum(1 for ok, _, _ in self.rows if not ok)


def find_runtime_arn(name_contains: str) -> str | None:
    """Pull a runtime ARN out of the AgentCore CLI's deployed-state file.

    Searched structurally rather than by a fixed key path, because the CLI owns that
    file's shape and has changed it between preview releases. A hard-coded path that
    quietly returned None would make this script report "not deployed" for a system
    that is deployed and working -- a false alarm is as bad as a missed one here.
    """
    if not STATE.is_file():
        return None
    found = sorted(
        {
            match.group(0)
            for match in _RUNTIME_ARN.finditer(STATE.read_text(encoding="utf-8"))
            if name_contains in match.group(0)
        }
    )
    return found[0] if len(found) == 1 else (found[0] if found else None)


def read_stream(arn: str, application_id: str, timeout_s: int) -> tuple[list[dict], float]:
    """Invoke the runtime and return (frames, wall_seconds)."""
    import boto3  # noqa: PLC0415  -- imported here so --help works without boto3

    region = arn.split(":")[3]
    from botocore.config import Config  # noqa: PLC0415

    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(read_timeout=timeout_s, connect_timeout=15, retries={"max_attempts": 0}),
    )
    started = time.monotonic()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        # The API floor is 33 characters. uuid4().hex is 32 and is rejected.
        runtimeSessionId=str(uuid.uuid4()),
        qualifier="DEFAULT",
        payload=json.dumps({"application_id": application_id}).encode(),
    )
    body = response["response"]
    raw = body.read() if hasattr(body, "read") else b"".join(body)
    wall = time.monotonic() - started

    frames: list[dict] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("data: "):
            try:
                frames.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                continue
    return frames, wall


def check_underwriter(checks: Checks, application_id: str, timeout_s: int) -> None:
    print(f"\n{DIM}underwriter — batch path, 8 specialists + synthesis{RESET}")

    arn = find_runtime_arn("underwriter")
    if not checks.add(bool(arn), "runtime is deployed", arn or "no ARN in deployed-state.json"):
        print(f"\n  {YELLOW}Deploy first:  ./deploy/deploy-all.sh{RESET}")
        return

    try:
        frames, wall = read_stream(arn, application_id, timeout_s)
    except Exception as exc:  # noqa: BLE001 -- any failure here is one failed check
        checks.add(False, "runtime responds", f"{type(exc).__name__}: {exc}")
        return
    checks.add(True, "runtime responds", f"{len(frames)} SSE frames in {wall:.1f}s")

    by_event: dict[str, list[dict]] = {}
    for frame in frames:
        by_event.setdefault(str(frame.get("event")), []).append(frame)

    errors = by_event.get("error", [])
    checks.add(
        not errors,
        "no error frame",
        # An exception mid-stream becomes a terminal error frame AFTER HTTP 200, so a
        # 200 is not evidence of success and every frame has to be inspected.
        "" if not errors else str(errors[0].get("error"))[:220],
    )

    start = (by_event.get("start") or [{}])[0]
    mock = start.get("mock_mode")
    checks.add(
        mock is False,
        "called Bedrock (mock_mode is false)",
        "MOCK_MODE is not 0 on the runtime: this run made NO model calls"
        if mock is not False
        else f"signals from {start.get('signal_source')}",
    )

    done_frames = by_event.get("agent_done", [])
    failed_frames = by_event.get("agent_failed", [])
    expected = len(start.get("domains") or []) or 8
    checks.add(
        len(done_frames) == expected and not failed_frames,
        f"all {expected} specialists returned",
        f"{len(done_frames)}/{expected} done"
        + (f", failed: {[f.get('domain') for f in failed_frames]}" if failed_frames else ""),
    )

    measured = [f for f in done_frames if f.get("measured")]
    checks.add(
        len(measured) == len(done_frames) and bool(done_frames),
        "every specialist reported real token usage",
        f"{len(measured)}/{len(done_frames)} measured",
    )

    terminal = (by_event.get("done") or [{}])[0]
    adjudication = terminal.get("adjudication") or {}
    keys = list(adjudication)
    checks.add(
        len(keys) == EXPECTED_KEY_COUNT,
        f"adjudication has exactly {EXPECTED_KEY_COUNT} keys",
        f"{len(keys)} keys",
    )
    checks.add(
        bool(keys) and keys[0] == EXPECTED_FIRST_KEY,
        f"first key is {EXPECTED_FIRST_KEY}",
        keys[0] if keys else "no adjudication",
    )
    checks.add(
        "application_id" not in adjudication,
        "no application_id key in the adjudication",
        # It travels in the transport envelope. An extra key breaks the customer's DDL.
        "present, but the contract has no such key" if "application_id" in adjudication else "",
    )
    key_check = terminal.get("key_check") or {}
    checks.add(
        key_check.get("valid") is True and key_check.get("order_matches") is True,
        "runtime's own contract validation passed",
        f"valid={key_check.get('valid')} order_matches={key_check.get('order_matches')}",
    )

    decision = adjudication.get("overall_risk_decision")
    recommendation = adjudication.get("recommendation_decision")
    checks.add(
        decision in {"LOW RISK", "MEDIUM RISK", "HIGH RISK"},
        "overall_risk_decision is in the enum",
        str(decision),
    )
    checks.add(
        recommendation in {"APPROVE", "REVIEW AND APPLY STIPULATIONS", "DECLINE"},
        "recommendation_decision is in the enum",
        str(recommendation),
    )

    cost = terminal.get("cost") or {}
    total = cost.get("total_usd")
    checks.add(
        isinstance(total, (int, float)) and total > 0 and cost.get("measured") is True,
        "cost was measured and is non-zero",
        f"${total}" if total is not None else "no cost reported",
    )

    timing = terminal.get("timing") or {}
    e2e = timing.get("end_to_end_ms")
    if isinstance(e2e, (int, float)):
        speedup = (
            timing["sum_of_agent_ms"] / timing["fanout_wall_ms"]
            if timing.get("fanout_wall_ms")
            else None
        )
        print(
            f"  {DIM}      {e2e / 1000:.1f}s end to end, "
            f"fan-out speedup {speedup:.2f}x{RESET}"
            if speedup
            else f"  {DIM}      {e2e / 1000:.1f}s end to end{RESET}"
        )


def check_francis(checks: Checks, timeout_s: int) -> None:
    print(f"\n{DIM}francis — interactive path, max 2 tools per query{RESET}")
    arn = find_runtime_arn("francis")
    if not checks.add(bool(arn), "runtime is deployed", arn or "no ARN in deployed-state.json"):
        return

    import boto3  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415

    client = boto3.client(
        "bedrock-agentcore",
        region_name=arn.split(":")[3],
        config=Config(read_timeout=timeout_s, connect_timeout=15, retries={"max_attempts": 0}),
    )
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=str(uuid.uuid4()),
            qualifier="DEFAULT",
            payload=json.dumps(
                {"prompt": "What is the identity fraud risk on APP-1004?"}
            ).encode(),
        )
        body = response["response"]
        raw = body.read() if hasattr(body, "read") else b"".join(body)
    except Exception as exc:  # noqa: BLE001
        checks.add(False, "runtime responds", f"{type(exc).__name__}: {exc}")
        return

    text = raw.decode("utf-8", "replace")
    frames = [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ") and _is_json(line[6:])
    ]
    errors = [f for f in frames if f.get("event") == "error"]
    checks.add(not errors, "no error frame", str(errors[0].get("error"))[:220] if errors else "")

    answer = ""
    for frame in frames:
        if frame.get("event") == "done":
            answer = str(frame.get("answer") or frame.get("text") or "")
    # Her prompt mandates these two headings; RECOMMENDATIONS is conditional and its
    # absence on an unasked-for question is correct, so it is not checked.
    checks.add(
        "## EXECUTIVE SUMMARY" in answer and "## ANALYSIS" in answer,
        "answer uses her required response shape",
        f"{len(answer)} chars",
    )
    checks.add(
        "claude" not in answer.lower(),
        "does not identify as Claude",
    )


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--application-id",
        default="APP-1004",
        help="fixture to adjudicate (default: APP-1004, a HIGH RISK scenario)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="read timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--skip-francis",
        action="store_true",
        help="check the batch path only, which halves the Bedrock spend of a run",
    )
    args = parser.parse_args(argv)

    print(
        "Verifying the DEPLOYED system. This makes real Bedrock calls and costs "
        "roughly $0.16 per application."
    )

    checks = Checks()
    check_underwriter(checks, args.application_id, args.timeout)
    if not args.skip_francis:
        check_francis(checks, args.timeout)

    total = len(checks.rows)
    if checks.failed:
        print(f"\n{RED}{checks.failed} of {total} checks failed.{RESET}")
        print(f"{DIM}Troubleshooting: deploy/README.md{RESET}")
        return 1
    print(f"\n{GREEN}All {total} checks passed.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
