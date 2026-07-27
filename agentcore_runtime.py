"""
Amazon Bedrock AgentCore Runtime entrypoint.

Deploy with the AgentCore starter toolkit:

    agentcore configure --entrypoint agentcore_runtime.py
    agentcore launch
    agentcore invoke '{"prompt": "Run a full underwriting review of APP-1004"}'

AgentCore Runtime packages this file + requirements.txt into a container,
provisions the runtime, and exposes a secure, session-isolated HTTPS endpoint.
The @app.entrypoint function is invoked per request; the orchestrator and the eight
specialists run inside the managed runtime, calling Amazon Bedrock for
inference (server-authoritative, IAM-scoped).
"""

from bedrock_agentcore import BedrockAgentCoreApp

from agents.orchestrator import analyze

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    """
    AgentCore Runtime handler.

    Expected payload: {"prompt": "<fraud analysis request>"}
    Examples:
      {"prompt": "Run a full underwriting review of APP-1004"}
      {"prompt": "Is there income fraud on APP-1003?"}
      {"prompt": "Check APP-1004 for fraud ring activity"}
    """
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        return {"error": "Provide a 'prompt' describing the fraud-analysis request."}

    result = analyze(prompt)
    return {"result": result}


if __name__ == "__main__":
    # Local dev server (hot-reloadable). Mirrors the deployed runtime contract.
    app.run()
