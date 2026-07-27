#!/usr/bin/env python3
"""
Compose architecture.svg from the official AWS Architecture Icons in icons/.

Each icon is inlined (with its internal IDs namespaced to avoid collisions) so
the resulting architecture.svg is self-contained and renders anywhere, including
GitLab's markdown preview. Re-run after changing the layout:

    python architecture/build_diagram.py
"""
from __future__ import annotations
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(HERE, "icons")

W, H = 1040, 660
ACCENT = "#ff9900"
INK = "#232f3e"
LINE = "#5a6b8c"


def place_icon(name: str, x: float, y: float, size: float = 56) -> str:
    """Return a nested <svg> that draws icons/<name>.svg at (x,y) sized size×size."""
    with open(os.path.join(ICONS, f"{name}.svg"), encoding="utf-8") as f:
        raw = f.read()
    vb = re.search(r'viewBox="([^"]+)"', raw)
    viewbox = vb.group(1) if vb else "0 0 80 80"
    inner = raw[raw.index(">", raw.index("<svg")) + 1: raw.rindex("</svg>")]
    prefix = re.sub(r"[^a-zA-Z0-9]", "_", name) + "_"
    ids = set(re.findall(r'id="([^"]+)"', inner))
    for i in ids:
        inner = inner.replace(f'id="{i}"', f'id="{prefix}{i}"')
        inner = inner.replace(f'url(#{i})', f'url(#{prefix}{i})')
        inner = inner.replace(f'xlink:href="#{i}"', f'xlink:href="#{prefix}{i}"')
        inner = inner.replace(f'href="#{i}"', f'href="#{prefix}{i}"')
    return (f'<svg x="{x}" y="{y}" width="{size}" height="{size}" '
            f'viewBox="{viewbox}" xmlns:xlink="http://www.w3.org/1999/xlink">{inner}</svg>')


def text(x, y, s, size=13, fill=INK, weight="normal", anchor="middle"):
    return (f'<text x="{x}" y="{y}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}">{s}</text>')


def box(x, y, w, h, label, stroke=LINE, dash=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" '
            f'stroke="{stroke}" stroke-width="1.5"{d}/>'
            + (text(x + 12, y + 20, label, 12, stroke, "bold", "start") if label else ""))


def node(name, cx, cy, label, sub=""):
    """Icon centered at (cx,cy) with a label below."""
    s = place_icon(name, cx - 28, cy - 28, 56)
    out = [s, text(cx, cy + 44, label, 12, INK, "bold")]
    if sub:
        out.append(text(cx, cy + 60, sub, 10.5, "#5a6b8c"))
    return "".join(out)


def arrow(x1, y1, x2, y2, dash=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{LINE}" '
            f'stroke-width="1.6" marker-end="url(#arrow)"{d}/>')


def build() -> str:
    P = []
    P.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="Arial, Helvetica, sans-serif">')
    P.append('<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
             f'orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 Z" fill="{LINE}"/>'
             '</marker></defs>')
    P.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#f7f9fc"/>')
    P.append(text(W / 2, 30, "Multi-Agent Fraud Underwriting on Amazon Bedrock AgentCore", 18, INK, "bold"))
    P.append(text(W / 2, 50, "Signal retrieval (MCP) \u2192 eight specialists in parallel \u2192 orchestrator synthesizes the decision", 12, "#5a6b8c"))

    # Client
    P.append(node("users", 70, 130, "Underwriter", "/ API"))

    # AWS Cloud + AgentCore Runtime
    P.append(box(150, 70, 640, 500, "AWS Cloud"))
    P.append(box(170, 150, 600, 400, "Amazon Bedrock AgentCore Runtime (serverless)", ACCENT))

    # Signal layer (gateway) + data
    P.append(node("amazon-api-gateway", 250, 210, "AgentCore Gateway", "Cortex Analyst (MCP)"))
    P.append(node("amazon-aurora", 250, 470, "Aurora PostgreSQL", "application data"))

    # Orchestrator
    P.append(node("amazon-bedrock-agentcore", 470, 210, "Orchestrator", "router + synthesizer"))

    # Eight specialists (2 x 4) using the Bedrock icon, with tier labels
    specs = [("identity", "Sonnet"), ("dealer", "Haiku"), ("straw", "Haiku"), ("employment", "Sonnet"),
             ("income", "Sonnet"), ("synthetic", "Opus"), ("bustout", "Sonnet"), ("rings", "Opus")]
    x0, y0, dx, dy = 360, 350, 108, 92
    for i, (dom, tier) in enumerate(specs):
        cx = x0 + (i % 4) * dx
        cy = y0 + (i // 4) * dy
        P.append(place_icon("amazon-bedrock", cx - 20, cy - 20, 40))
        P.append(text(cx, cy + 32, dom, 10.5, INK, "bold"))
        P.append(text(cx, cy + 45, tier, 9.5, "#8a97b5"))
    P.append(text(575, 322, "8 fraud specialists \u2014 run in parallel, each on its own model", 11, "#5a6b8c"))

    # Right column: Bedrock, CloudWatch, IAM
    P.append(node("amazon-bedrock", 900, 140, "Amazon Bedrock", "Haiku / Sonnet / Opus"))
    P.append(node("amazon-cloudwatch", 900, 320, "CloudWatch", "spend / agent dashboard"))
    P.append(node("aws-iam", 900, 480, "IAM", "auth + guardrails"))

    # Arrows
    P.append(arrow(100, 130, 222, 205))                  # user -> gateway
    P.append(arrow(250, 240, 250, 442))                  # gateway -> aurora (data)
    P.append(arrow(250, 442, 250, 240))
    P.append(arrow(298, 205, 442, 205))                  # gateway -> orchestrator
    P.append(arrow(470, 240, 470, 322))                  # orchestrator -> specialists (fan-out)
    P.append(arrow(600, 330, 505, 240))                  # specialists -> orchestrator (synthesize)
    P.append(arrow(700, 380, 872, 168, "4 3"))           # specialists -> Bedrock (inference, dashed)
    P.append(arrow(720, 330, 872, 320, "4 3"))           # -> cloudwatch (telemetry, dashed)
    P.append(arrow(700, 420, 872, 470, "4 3"))           # -> iam (dashed)

    # Decision out (from orchestrator, to the right, clear of the Bedrock arrow)
    P.append(arrow(498, 195, 628, 195))
    P.append(text(690, 190, "Adjudication", 11, INK, "bold"))
    P.append(text(690, 205, "APPROVE / REVIEW / DECLINE", 10, "#5a6b8c"))

    # footnote
    P.append(text(W / 2, H - 12,
                  "AWS Architecture Icons \u00a9 Amazon Web Services. Snowflake Cortex Analyst is called as an external MCP tool so semantic-layer guardrails stay in place.",
                  10, "#8a97b5"))
    P.append("</svg>")
    return "".join(P)


if __name__ == "__main__":
    out = os.path.join(HERE, "..", "architecture.svg")
    with open(os.path.normpath(out), "w", encoding="utf-8") as f:
        f.write(build())
    print("Wrote architecture.svg")
