#!/usr/bin/env python3
"""
Compose architecture.svg from the official AWS Architecture Icons in icons/.

Each icon is inlined (internal IDs namespaced to avoid collisions) so the result
is self-contained and renders anywhere, including GitLab. Layout is arranged so
no arrow crosses another arrow, a service icon, or a text label:
  - labels sit ABOVE any icon that an arrow enters from below
  - one clean vertical connector links the orchestrator and the specialist group
  - the three AWS service arrows run as parallel horizontal lines in an empty gap

Re-run after edits:  python architecture/build_diagram.py
"""
from __future__ import annotations
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(HERE, "icons")

W, H = 1060, 650
ACCENT = "#ff9900"
INK = "#232f3e"
MUTED = "#5a6b8c"
LINE = "#5a6b8c"


def place_icon(name: str, x: float, y: float, size: float = 56) -> str:
    with open(os.path.join(ICONS, f"{name}.svg"), encoding="utf-8") as f:
        raw = f.read()
    vb = re.search(r'viewBox="([^"]+)"', raw)
    viewbox = vb.group(1) if vb else "0 0 80 80"
    inner = raw[raw.index(">", raw.index("<svg")) + 1: raw.rindex("</svg>")]
    prefix = re.sub(r"[^a-zA-Z0-9]", "_", name) + "_"
    for i in set(re.findall(r'id="([^"]+)"', inner)):
        inner = inner.replace(f'id="{i}"', f'id="{prefix}{i}"')
        inner = inner.replace(f'url(#{i})', f'url(#{prefix}{i})')
        inner = inner.replace(f'xlink:href="#{i}"', f'xlink:href="#{prefix}{i}"')
        inner = inner.replace(f'href="#{i}"', f'href="#{prefix}{i}"')
    return (f'<svg x="{x}" y="{y}" width="{size}" height="{size}" '
            f'viewBox="{viewbox}" xmlns:xlink="http://www.w3.org/1999/xlink">{inner}</svg>')


def text(x, y, s, size=12, fill=INK, weight="normal", anchor="middle"):
    return (f'<text x="{x}" y="{y}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}">{s}</text>')


def box(x, y, w, h, label, stroke=LINE):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" '
            f'stroke="{stroke}" stroke-width="1.5"/>'
            + (text(x + 12, y + 20, label, 12, stroke, "bold", "start") if label else ""))


def node(name, cx, cy, title, sub="", above=False, size=56):
    out = [place_icon(name, cx - size / 2, cy - size / 2, size)]
    if above:
        out.append(text(cx, cy - size / 2 - 22, title, 12, INK, "bold"))
        if sub:
            out.append(text(cx, cy - size / 2 - 8, sub, 10.5, MUTED))
    else:
        out.append(text(cx, cy + size / 2 + 16, title, 12, INK, "bold"))
        if sub:
            out.append(text(cx, cy + size / 2 + 31, sub, 10.5, MUTED))
    return "".join(out)


def arrow(x1, y1, x2, y2, dash="", double=False):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    st = ' marker-start="url(#arrowStart)"' if double else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{LINE}" '
            f'stroke-width="1.6" marker-end="url(#arrow)"{st}{d}/>')


def build() -> str:
    P = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="Arial, Helvetica, sans-serif">']
    P.append('<defs>'
             '<marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
             f'orient="auto" markerUnits="strokeWidth"><path d="M0,0 L8,3 L0,6 Z" fill="{LINE}"/></marker>'
             '<marker id="arrowStart" markerWidth="10" markerHeight="10" refX="0" refY="3" '
             f'orient="auto" markerUnits="strokeWidth"><path d="M8,0 L0,3 L8,6 Z" fill="{LINE}"/></marker>'
             '</defs>')
    P.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#f7f9fc"/>')
    P.append(text(W / 2, 28, "Multi-Agent Fraud Underwriting on Amazon Bedrock AgentCore", 18, INK, "bold"))
    P.append(text(W / 2, 48, "Signal retrieval (MCP) \u2192 eight specialists in parallel \u2192 orchestrator synthesizes the decision", 12, MUTED))

    # Containers
    P.append(box(150, 66, 872, 546, "AWS Cloud"))
    P.append(box(170, 132, 545, 452, "Amazon Bedrock AgentCore Runtime (serverless)", ACCENT))

    # Client (outside, left)
    P.append(node("users", 70, 250, "Underwriter", "/ API"))

    # Top lane: Gateway -> Orchestrator (labels ABOVE; arrows enter from left/below)
    P.append(node("amazon-api-gateway", 250, 250, "AgentCore Gateway", "Cortex Analyst (MCP)", above=True))
    P.append(node("amazon-bedrock-agentcore", 470, 250, "Orchestrator", "router + synthesizer", above=True))

    # Data source (bottom-left), labels below
    P.append(node("amazon-aurora", 250, 505, "Aurora PostgreSQL", "application data"))

    # Specialist grid (2 x 4), labels below each
    specs = [("identity", "Sonnet"), ("dealer", "Haiku"), ("straw", "Haiku"), ("employment", "Sonnet"),
             ("income", "Sonnet"), ("synthetic", "Opus"), ("bustout", "Sonnet"), ("rings", "Opus")]
    cols = [330, 440, 550, 660]
    rows = [370, 470]
    for i, (dom, tier) in enumerate(specs):
        cx, cy = cols[i % 4], rows[i // 4]
        P.append(place_icon("amazon-bedrock", cx - 20, cy - 20, 40))
        P.append(text(cx, cy + 32, dom, 10.5, INK, "bold"))
        P.append(text(cx, cy + 45, tier, 9.5, "#8a97b5"))

    # Right column AWS services (inside AWS Cloud), labels below
    P.append(node("amazon-bedrock", 905, 250, "Amazon Bedrock", "Haiku / Sonnet / Opus"))
    P.append(node("amazon-cloudwatch", 905, 390, "CloudWatch", "spend / agent dashboard"))
    P.append(node("aws-iam", 905, 520, "IAM", "auth + guardrails"))

    # --- arrows (each in a clear channel) ---
    P.append(arrow(98, 250, 220, 250))                       # user -> gateway
    P.append(arrow(280, 250, 440, 250))                      # gateway -> orchestrator
    P.append(arrow(250, 477, 250, 280))                      # aurora -> gateway (vertical, left of grid)
    # orchestrator <-> specialist group (single double-headed vertical, in the col gap)
    P.append(arrow(470, 280, 470, 348, double=True))
    P.append(text(486, 308, "dispatch /", 10, MUTED, anchor="start"))
    P.append(text(486, 321, "synthesize", 10, MUTED, anchor="start"))
    # decision output (short, inside runtime, clear area)
    P.append(arrow(500, 245, 556, 245))
    P.append(text(562, 241, "Adjudication", 11, INK, "bold", anchor="start"))
    P.append(text(562, 255, "APPROVE / REVIEW / DECLINE", 9.5, MUTED, anchor="start"))
    # AWS service arrows: parallel lines in the empty gap between runtime and services
    P.append(arrow(716, 320, 875, 265))                      # -> Bedrock (inference)
    P.append(text(760, 300, "inference", 9.5, MUTED, anchor="start"))
    P.append(arrow(716, 390, 875, 390, dash="4 3"))          # -> CloudWatch (telemetry)
    P.append(text(748, 383, "telemetry", 9.5, MUTED, anchor="start"))
    P.append(arrow(716, 500, 875, 515, dash="4 3"))          # -> IAM (auth)
    P.append(text(760, 495, "auth", 9.5, MUTED, anchor="start"))

    P.append(text(W / 2, H - 12,
                  "AWS Architecture Icons \u00a9 Amazon Web Services. Snowflake Cortex Analyst is called as an external MCP tool so semantic-layer guardrails stay in place.",
                  10, "#8a97b5"))
    P.append("</svg>")
    return "".join(P)


if __name__ == "__main__":
    out = os.path.normpath(os.path.join(HERE, "..", "architecture.svg"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(build())
    print("Wrote architecture.svg")
