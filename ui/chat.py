"""Chat and agent-inspection endpoints for the demo UI.

Two things the batch panel cannot show, and the customer asked for both:

  1. **A seat on an agent.** Their own production UI exposes the orchestrator plus each
     specialist, so an identity analyst can "grab a seat" on the identity agent rather
     than reading a nine-agent adjudication. This module gives every one of the nine a
     chat endpoint.
  2. **What each agent actually is.** Its prompt, its settings, its tools, and where its
     data comes from -- including the RDS seam, which is the migration question their
     workshop opened with ("if you're working with RDS or Postgres, how to get that
     connected").

WHAT IS REAL AND WHAT IS MOCKED, STATED PLAINLY

Real: the chat invokes the DEPLOYED AgentCore runtimes. Francis is
`ppFraud_francis` -- an actual router that calls at most two specialists per query --
and each specialist is its own `ppFraud_<domain>` runtime. Same prompts, same models,
same 8-way fan-out code path. Nothing here re-implements an agent.

Mocked: the RDS connection panel only. `signal_mode()` describes the seam and
`probe_datasource()` reports what a connection attempt WOULD do; neither opens a socket.
It is labelled `mocked: true` in the response and the UI says so, because a fake green
"connected" light on a migration question is worse than no light at all.

A SPECIALIST SEAT IS NOT A CHAT, AND THE UI MUST NOT IMPLY IT IS

`agents.fanout.fan_out_streaming` accepts NO question. A specialist's entire input is its
projected domain view, so a user's text is discarded -- it was only ever scanned for an
`APP-NNNN` to analyse. A free-text box over that is a lie: it invites a question, throws
it away, and then errors on anything that happens not to contain an id
("no application id in this request" for a perfectly reasonable message).

So a specialist seat asks for an APPLICATION, not a question, and the id travels at the
payload root where `extract_application_id` looks first rather than buried in prose for a
regex to find. Francis is the one true conversation: she takes a question, routes it to at
most two specialists, remembers within a session, and answers in her own required format.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, AsyncIterator

#: Deployed runtime ARNs, resolved once from the AgentCore CLI's state file.
_ARN_RE = re.compile(r"arn:aws[a-z-]*:bedrock-agentcore:[^:]+:\d+:runtime/[A-Za-z0-9_.-]+")


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


def runtime_arns() -> dict[str, str]:
    """``{short_name: arn}`` for every deployed runtime, or ``{}`` if none.

    Read from `agentcore/.cli/deployed-state.json` rather than configured, so this
    follows whatever was last deployed. An empty dict is a normal state -- a fresh clone
    has no deployment -- and the caller falls back to the in-process path.
    """
    state = _repo_root() / "agentcore" / ".cli" / "deployed-state.json"
    if not state.is_file():
        return {}
    out: dict[str, str] = {}
    for arn in sorted(set(_ARN_RE.findall(state.read_text(encoding="utf-8")))):
        tail = arn.rsplit("/", 1)[-1]
        short = tail.split("-", 1)[0]
        out[short.split("_", 1)[-1] if "_" in short else short] = arn
    return out


_CLIENT: Any = None


def _client() -> Any:
    global _CLIENT
    if _CLIENT is None:
        import boto3
        from botocore.config import Config

        _CLIENT = boto3.client(
            "bedrock-agentcore",
            region_name=os.environ.get("AWS_REGION", "us-west-2"),
            config=Config(
                max_pool_connections=16,
                read_timeout=300,
                connect_timeout=15,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _CLIENT


# ---------------------------------------------------------------------------
# Agent inventory
# ---------------------------------------------------------------------------


def agent_roster() -> list[dict[str, Any]]:
    """Every agent a user can take a seat on, orchestrator first.

    Order matters for the UI: Francis is what most people will use ("typically I think
    most people would just use that to ask questions"), and the specialists are the
    per-discipline seats behind her.
    """
    from prompts.loader import AGENT_NAMES, DOMAINS

    arns = runtime_arns()
    roster: list[dict[str, Any]] = [
        {
            "id": "francis",
            "kind": "orchestrator",
            "agent_name": AGENT_NAMES.get("master", "FRANCIS"),
            "label": "Francis — orchestrator",
            "description": (
                "Routes an analyst question to at most two specialists and answers in "
                "markdown. Free-form conversation; keeps session context."
            ),
            "runtime": arns.get("francis"),
            "conversational": True,
        }
    ]
    for domain in DOMAINS:
        roster.append(
            {
                "id": domain,
                "kind": "specialist",
                "agent_name": AGENT_NAMES[domain],
                "label": f"{AGENT_NAMES[domain]} — {domain}",
                "description": (
                    f"Analyses the {domain} dimension of one application. Pick an "
                    "application; this agent takes no question -- its input is its own "
                    "projected signals."
                ),
                "runtime": arns.get(domain),
                # Not a chatbot, and saying so is more honest than implying it is.
                "conversational": False,
            }
        )
    return roster


def _deployed_runtime_env() -> dict[str, str]:
    """The envVars `agentcore.json` sets on the runtimes, merged.

    All ten runtimes carry the same data-source variables -- `connect-rds.sh` writes them to
    every one -- so a merge is the right shape and a disagreement between runtimes would be a
    configuration bug worth seeing rather than hiding.

    Never raises: an unreadable or absent config is reported as "not configured", which is the
    honest answer for a clone that has not been deployed.
    """
    try:
        import json as _json  # noqa: PLC0415
        import pathlib as _pathlib  # noqa: PLC0415

        path = _pathlib.Path(__file__).resolve().parent.parent / "agentcore" / "agentcore.json"
        config = _json.loads(path.read_text())
    except Exception:
        return {}

    merged: dict[str, str] = {}
    for runtime in config.get("runtimes", []) or []:
        for entry in runtime.get("envVars", []) or []:
            name, value = entry.get("name"), entry.get("value")
            if name and value:
                merged.setdefault(str(name), str(value))
    return merged


def signal_mode() -> dict[str, Any]:
    """The data seam: where an agent's signals come from, and what else it could be.

    MOCKED. Nothing here connects to anything. It reports the seam that
    `app.runtime_support.build_signal_payload` already is -- the single function every
    agent goes through to get its data -- so the migration question has a concrete
    answer instead of a diagram.
    """
    # WHAT THE DEPLOYED RUNTIME WILL SEE, falling back to this process.
    #
    # The CloudFront build bakes this object at deploy time, in a shell that is not the
    # runtime's, so reading only `os.environ` reported "not configured" beside a live cluster
    # with 840 seeded rows behind it. `agentcore/agentcore.json` is the file `agentcore deploy`
    # acts on, so it is the truthful answer for a panel describing the deployment. A local run
    # still wins, because there the process IS the thing being described.
    deployed = _deployed_runtime_env()

    def setting(name: str) -> str:
        return (os.environ.get(name) or deployed.get(name) or "").strip()

    mode = (setting("SIGNAL_MODE") or "fixtures").lower()

    # COMPUTED, not hardcoded. These were literal `False` from when nothing was connected, and
    # once a cluster existed the panel started lying in the other direction -- reporting "not
    # configured" beside a live connection with 840 seeded rows behind it. A hardcoded
    # availability flag is exactly the kind of stale claim this UI exists to avoid.
    aurora_ready = bool(
        setting("AURORA_SECRET_ARN")
        and setting("AURORA_DATABASE")
        and (setting("AURORA_CLUSTER_ARN") or setting("PGHOST"))
    )
    cortex_ready = bool(setting("SNOWFLAKE_SECRET_ID"))

    return {
        # True only while the active source really is the committed fixtures. When SIGNAL_MODE
        # points at a cluster the agents read that cluster, so calling it "mocked" is wrong --
        # even though this panel still opens no connection of its own, which the note says.
        "mocked": mode == "fixtures",
        "active": mode,
        "active_is_configured": (
            True if mode == "fixtures" else aurora_ready if mode == "aurora" else cortex_ready
        ),
        "swap_point": "app.runtime_support.build_signal_payload",
        "note": (
            "Every agent reads its signals through one function. Changing the source is "
            "changing that function's body, not the agents. Nothing on this panel opens "
            "a connection."
        ),
        "modes": [
            {
                "id": "fixtures",
                "label": "Fixtures (this demo)",
                "detail": "14 committed applications with every registry signal and its "
                "paired _context. No network, no credentials.",
                "available": True,
            },
            {
                "id": "aurora",
                "label": "Aurora PostgreSQL / RDS",
                "detail": "The precomputed governed signals, read per application. This "
                "is the target: the data already lives in Aurora, ~10 GB, 3.4M reads/day, "
                "so the per-application path becomes one indexed read per domain instead "
                "of eight text-to-SQL round trips.",
                "available": aurora_ready,
                "requires": [
                    "AURORA_SECRET_ARN (Secrets Manager)",
                    "AURORA_CLUSTER_ARN or host/port",
                    "AURORA_DATABASE",
                    "VPC networkMode on the runtime, or RDS Data API",
                ],
            },
            {
                "id": "cortex",
                "label": "Snowflake Cortex Analyst (via Gateway)",
                "detail": "Kept as a config flag so live-Cortex and precomputed-Aurora "
                "are one switch apart during migration. Reached through a Lambda "
                "AgentCore Gateway target -- SigV4 outbound to Snowflake is not possible, "
                "and Cortex Analyst /message returns generated SQL rather than rows, so a "
                "second call executes it.",
                "available": cortex_ready,
                "requires": [
                    "AgentCore Gateway with a Lambda target",
                    "Snowflake PAT in Secrets Manager",
                    "PrivateLink if the account enforces network policies",
                ],
            },
        ],
    }


def probe_datasource(mode: str) -> dict[str, Any]:
    """What a connection attempt WOULD do. Deliberately does not attempt one.

    Returns `mocked: true` and a per-requirement checklist read from the environment, so
    the panel is useful (it says which variables are actually set) without ever claiming
    a connection it did not make. A green light nobody earned is the one thing this demo
    must not show on a migration question.
    """
    spec = next((m for m in signal_mode()["modes"] if m["id"] == mode), None)
    if spec is None:
        return {"mocked": True, "mode": mode, "ok": False, "error": f"unknown mode {mode!r}"}

    # Same source as signal_mode: the deployed config, then this process. The per-requirement
    # checklist is the part of this panel people actually read to answer "why is it not
    # connected", so it must agree with the availability flag above rather than answering from
    # a different environment.
    deployed = _deployed_runtime_env()
    checks = []
    for requirement in spec.get("requires", []):
        var = requirement.split()[0]
        present = None
        if var.isupper():
            present = bool(os.environ.get(var) or deployed.get(var))
        checks.append(
            {
                "requirement": requirement,
                "env_var": var if var.isupper() else None,
                "present": present,
                "from": (
                    None
                    if not var.isupper() or not present
                    else "this process" if os.environ.get(var) else "agentcore.json"
                ),
            }
        )
    return {
        "mocked": True,
        "mode": mode,
        "available": spec["available"],
        "checks": checks,
        "would_do": (
            "Read the 14 committed fixtures from disk."
            if mode == "fixtures"
            else f"Open a connection using the values above and read one application's "
            f"{mode} signals. Not attempted: this panel is a mock."
        ),
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def _frame(event: str, **fields: Any) -> str:
    return f"data: {json.dumps({'event': event, **fields})}\n\n"


async def chat_stream(
    agent_id: str,
    message: str,
    session_id: str | None = None,
    application_id: str | None = None,
    analyst_id: str | None = None,
) -> AsyncIterator[str]:
    """SSE for one chat turn against a deployed runtime.

    Frames: ``chat_started``, then ``delta`` / ``tool`` as they arrive, then
    ``chat_completed`` with the measured usage, or ``error``.

    A missing runtime is reported as an error frame rather than silently answered by
    something else. Fabricating a reply from a different code path would make the demo
    unfalsifiable, which is the opposite of what it is for.
    """
    import asyncio

    started = time.perf_counter()
    session = session_id or str(uuid.uuid4())
    roster = {a["id"]: a for a in agent_roster()}
    agent = roster.get(agent_id)

    if agent is None:
        yield _frame("error", error=f"unknown agent {agent_id!r}")
        return

    arn = agent.get("runtime")
    if not arn:
        yield _frame(
            "error",
            error=(
                f"the {agent_id} runtime is not deployed, so there is nothing to chat "
                "with. Run ./deploy/deploy-all.sh, or use the batch tab which runs "
                "in-process."
            ),
        )
        return

    yield _frame(
        "chat_started",
        agent=agent_id,
        agent_name=agent["agent_name"],
        kind=agent["kind"],
        runtime=arn.rsplit("/", 1)[-1],
        session_id=session,
        # Echoed so the UI can state whether this turn is actor-scoped. None means it went to
        # the shared actor, and the header must say so instead of displaying an analyst id that
        # had no effect -- which is exactly how a demo greets the wrong person by name.
        analyst_id=analyst_id or None,
    )

    # Francis takes a prompt. A specialist takes an APPLICATION ID -- and only that.
    #
    # This is not a shortcut, it is the shape of the thing: `fan_out_streaming` accepts no
    # question, so a specialist's entire input is its projected domain view. The user's
    # text is discarded. The UI therefore asks a specialist seat for an application rather
    # than for a question, and the id is sent at the payload ROOT where
    # `extract_application_id` looks first -- not buried in prose for a regex to find.
    body: dict[str, Any] = {"prompt": message} if message else {}
    if application_id:
        body["application_id"] = application_id
    if not body:
        yield _frame("error", error="nothing to send: no message and no application id")
        return
    payload = json.dumps(body).encode()

    def _read(response: Any) -> tuple[str, bytes]:
        body = response["response"]
        raw = body.read() if hasattr(body, "read") else b"".join(body)
        return str(response.get("contentType") or ""), raw

    def invoke() -> tuple[str, bytes]:
        kwargs: dict[str, Any] = {
            "agentRuntimeArn": arn,
            "runtimeSessionId": session,
            "qualifier": "DEFAULT",
            "contentType": "application/json",
            "accept": "text/event-stream" if agent["conversational"] else "application/json",
            "payload": payload,
        }
        client = _client()
        # WHO IS ASKING, and why this is a header hook rather than the obvious parameter.
        #
        # `runtimeUserId` looks like the right field -- it is a first-class
        # InvokeAgentRuntime parameter and becomes
        # X-Amzn-Bedrock-AgentCore-Runtime-User-Id. It does NOT reach the entrypoint:
        # measured, calling with runtimeUserId="probe" still logged "none of ... on this
        # request, so memory falls back to the shared actor". The service consumes that
        # header itself. That is why the AgentCore workshop invents a CUSTOM header instead,
        # and why the runtime allowlists it in agentcore.json.
        #
        # So the custom header is injected here. `before-sign`, NOT after: adding a header
        # after botocore signs makes the signed value differ from the sent value, which is
        # exactly how the traceparent attempt in app/distributed_fanout.py produced a 403.
        # Signing it means SigV4 covers it.
        #
        # Verified: with this hook, `list-actors` on the memory shows the analyst as its own
        # actor instead of everything pooling into the shared persona.
        if analyst_id:
            actor = analyst_id[:120]

            def _add_actor_header(request: Any, **_: Any) -> None:
                request.headers.add_header(
                    "X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Id", actor
                )

            client.meta.events.register(
                "before-sign.bedrock-agentcore.InvokeAgentRuntime", _add_actor_header
            )
            try:
                return _read(client.invoke_agent_runtime(**kwargs))
            finally:
                # Unregister, or the client -- which lives for the container's lifetime --
                # would send the FIRST analyst's identity on every later request.
                client.meta.events.unregister(
                    "before-sign.bedrock-agentcore.InvokeAgentRuntime", _add_actor_header
                )

        return _read(client.invoke_agent_runtime(**kwargs))
        body = response["response"]
        raw = body.read() if hasattr(body, "read") else b"".join(body)
        return str(response.get("contentType") or ""), raw

    try:
        content_type, raw = await asyncio.to_thread(invoke)
    except Exception as exc:  # noqa: BLE001
        yield _frame("error", error=f"{type(exc).__name__}: {exc}")
        return

    text = raw.decode("utf-8", "replace")

    if "text/event-stream" in content_type:
        # Francis streams. Her frames are already the vocabulary we want, so deltas pass
        # through and the terminal frame is re-labelled.
        answer_parts: list[str] = []
        tools: list[str] = []
        usage: dict[str, Any] = {}
        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            try:
                frame = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            kind = frame.get("event")
            if kind == "delta":
                chunk = frame.get("text") or frame.get("delta") or ""
                if chunk:
                    answer_parts.append(chunk)
                    yield _frame("delta", text=chunk)
            elif kind in ("tool_start", "tool_done"):
                name = frame.get("tool") or frame.get("name")
                if kind == "tool_start" and name:
                    tools.append(name)
                yield _frame("tool", phase=kind, tool=name)
            elif kind == "error":
                yield _frame("error", error=frame.get("error"))
                return
            elif kind == "done":
                usage = {
                    k: frame.get(k)
                    for k in (
                        "model",
                        "tier",
                        "input_tokens",
                        "output_tokens",
                        "cache_read_tokens",
                        "cache_write_tokens",
                        "cost_usd",
                        "tools_used",
                    )
                }
                final = frame.get("answer") or frame.get("text")
                if final and not answer_parts:
                    answer_parts.append(final)
        yield _frame(
            "chat_completed",
            answer="".join(answer_parts),
            tools=tools,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            **{k: v for k, v in usage.items() if v is not None},
        )
        return

    # A specialist returns one JSON object.
    try:
        result = json.loads(text)
        if isinstance(result, str):
            result = json.loads(result)
    except json.JSONDecodeError:
        yield _frame("error", error=f"specialist returned non-JSON: {text[:300]}")
        return

    if not result.get("ok"):
        yield _frame("error", error=str(result.get("error") or "specialist failed"))
        return

    analysis = result.get("analysis") or ""
    yield _frame("delta", text=analysis)
    yield _frame(
        "chat_completed",
        answer=analysis,
        tools=[],
        application_id=result.get("application_id"),
        risk_band=result.get("risk_band"),
        analysis_title=result.get("analysis_title"),
        model=result.get("model"),
        tier=result.get("tier"),
        input_tokens=result.get("input_tokens"),
        output_tokens=result.get("output_tokens"),
        cache_read_tokens=result.get("cache_read_tokens"),
        cache_write_tokens=result.get("cache_write_tokens"),
        cost_usd=result.get("cost_usd"),
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        remote_elapsed_ms=result.get("elapsed_ms"),
    )
