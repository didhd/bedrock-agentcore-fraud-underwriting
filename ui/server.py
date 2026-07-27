"""
Live demo UI server (FastAPI + Server-Sent Events).

Serves a single page that shows the eight specialists firing in parallel on
Amazon Bedrock AgentCore, each on its own model, with per-agent latency and
cost, then the orchestrator's synthesized adjudication and an AgentCore-vs-
Snowflake head-to-head.

Run:
  python app.py --serve
  # or:
  MOCK_MODE=1 uvicorn ui.server:app --port 8080
Then open http://localhost:8080
"""

from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

from agents.orchestrator import adjudicate_stream
from tools.application_data import available_application_ids, fetch_application_record

# In offline mode, stagger the agents a little so the parallel fan-out is visible.
if os.environ.get("MOCK_MODE") == "1" and "SIM_LATENCY_SCALE" not in os.environ:
    os.environ["SIM_LATENCY_SCALE"] = "0.12"

app = FastAPI(title="Multi-Agent Fraud Underwriting on Amazon Bedrock AgentCore")

_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

_SCENARIOS = {
    "APP-1001": "Clean first-time buyer",
    "APP-1002": "Spousal co-borrower, thin file",
    "APP-1003": "Income overstatement (ambiguous)",
    "APP-1004": "Synthetic identity + fraud ring",
    "APP-1005": "Bust-out velocity pattern",
}


@app.get("/")
def index():
    return FileResponse(_HTML)


@app.get("/api/apps")
def apps():
    out = []
    for app_id in available_application_ids():
        rec = fetch_application_record(app_id) or {}
        out.append({
            "application_id": app_id,
            "scenario": _SCENARIOS.get(app_id, ""),
            "loan_amount": rec.get("loan_amount"),
            "vehicle": rec.get("vehicle"),
            "fraud_score": rec.get("fraud_score"),
        })
    return JSONResponse(out)


@app.get("/api/stream/{application_id}")
def stream(application_id: str):
    def gen():
        for event in adjudicate_stream(application_id):
            yield f"data: {json.dumps(event)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def serve(host: str = "127.0.0.1", port: int = 8080):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    serve()
