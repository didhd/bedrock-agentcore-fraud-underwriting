# Architecture

## Underwriting pipeline: parallel fan-out + synthesis

```
                          ┌────────────────────────────────────────────────┐
                          │         Amazon Bedrock AgentCore Runtime         │
                          │         (serverless, session-isolated)           │
   Underwriter / API      │   agentcore_runtime.py  @app.entrypoint          │
   ────────────────►      │            │                                     │
   {"prompt": "Run a      │            ▼                                     │
    full review of        │   Phase 0: signal layer                          │
    APP-1004"}            │   tools/cortex_analyst_mcp.py ──► Cortex Analyst  │
                          │   (semantic view via MCP / AgentCore Gateway)    │
                          │            │ governed signals + _context         │
                          │            ▼                                     │
                          │   Phase 1: 8 specialists RUN IN PARALLEL         │
                          │   ┌────┬────┬────┬────┬────┬────┬────┬────┐       │
                          │   IDEN DEAL STRAW EMPL INC  SYN  BUST RING        │
                          │   Son  Hai  Hai  Son  Son  Opus Son  Opus  <- per-agent model
                          │   └──┬──┴──┬─┴──┬─┴──┬─┴─┬──┴──┬─┴──┬─┴──┬─┘       │
                          │      └─────┴────┴────┴───┴─────┴────┴────┘        │
                          │            │ eight RISK/FINDINGS verdicts         │
                          │            ▼                                     │
                          │   Phase 2: ORCHESTRATOR (synthesizer)            │
                          │   totality-of-evidence ──► strict-JSON decision  │
                          └────────────────────────┬─────────────────────────┘
                                                    │
                     every agent call ─────────────┼────────► Amazon Bedrock
                     (inference, IAM-scoped,        │          (Haiku / Sonnet / Opus,
                      per-agent model)              │           per agents/models.py)
                                                    ▼
                                      Data source (demo = synthetic dataset;
                                      prod = Snowflake Cortex Analyst via MCP,
                                      or Aurora/Athena via PrivateLink)

           Cross-cutting: AgentCore Observability (CloudWatch traces),
           Guardrails (PII/SSN redaction), Identity (per-user auth), Memory.
```

Interactive (non-underwriting) questions take a different path: the orchestrator acts as
an **orchestrator/router** and calls one or two specialists as tools instead of
fanning out to all eight. See `analyze()` vs `adjudicate()` in the orchestrator.

## Request flow (underwriting)

1. A request arrives at the AgentCore Runtime entrypoint as `{"prompt": "..."}`.
2. **Phase 0 (signal layer):** the Cortex Analyst semantic view is queried via
   MCP for governed signals + `_context` (guardrails stay in Snowflake).
3. **Phase 1 (parallel analysis):** all eight specialists run **concurrently**,
   each on its **configured model** (`agents/models.py`). Each validates signals
   against context and returns a `RISK / FINDINGS` verdict. Parallelism is the
   latency win; per-agent model right-sizing is the cost win.
4. **Phase 2 (synthesis):** **the orchestrator** synthesizes the eight verdicts with
   totality-of-evidence decisioning into a **strict-JSON adjudication**
   (APPROVE / REVIEW AND APPLY STIPULATIONS / DECLINE, per-fraud-type flags).

## How this maps from the current Snowflake Cortex implementation

| Current (Snowflake Cortex Agents)                 | This demo (Amazon Bedrock AgentCore)                          |
|---------------------------------------------------|---------------------------------------------------------------|
| the orchestrator: orchestrator (interactive) + synthesizer (underwriting) | `agents/orchestrator.py` — `analyze()` router + `adjudicate()` parallel synthesizer |
| 8 subagents run in parallel, results synthesized  | `concurrent.futures` fan-out + `_synthesize_*` in the orchestrator |
| Single LLM (Claude Sonnet 4.6) across all agents  | `agents/models.py` — per-agent Haiku/Sonnet/Opus, env-overridable |
| Sub-agent prompt files (`*_agent_prompt.txt`)     | `agents/prompts.py` — ported verbatim in intent               |
| Cortex Analyst + semantic view over consortium SQL| `tools/cortex_analyst_mcp.py` signal layer: the semantic view called as an external MCP tool via AgentCore Gateway (see `docs/semantic-layer-mcp.md`) |
| Snowflake-hosted, Snowflake-managed model         | AgentCore Runtime (serverless) + Claude on Amazon Bedrock      |
| Strict-JSON master output contract                | Same JSON contract, produced by the orchestrator                        |
| Ad-hoc logging                                    | AgentCore Observability (CloudWatch Transaction Search)        |

The **prompts, calibration, and JSON output contract are preserved**. What
moves to AWS is the runtime, the model host, per-agent model choice, and
parallel execution.
