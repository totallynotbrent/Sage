"""Auto-render a plan as a mermaid dependency graph.

Per the design's inspiration: presenting the plan as a graph both gives
the learner a map of what is coming and forces the planner to have
reasoned out the full dependency structure. Generated deterministically
from the approved plan nodes - no extra LLM call required.
"""

from __future__ import annotations


def plan_to_mermaid(nodes: list[dict]) -> str:
    """Build a mermaid flowchart TD from plan nodes and depends_on edges."""
    if not nodes:
        return ""
    lines = ["graph TD"]
    for node in nodes:
        key = node.get("node_key") or ""
        title = (node.get("title") or key).replace('"', "'")
        status = node.get("status") or "pending"
        style = {
            "current": ":::current",
            "done": ":::done",
        }.get(status, "")
        lines.append(f'    {key}["{title}"]{style}')
    for node in nodes:
        key = node.get("node_key") or ""
        for dep in node.get("depends_on") or []:
            lines.append(f"    {dep} --> {key}")
    lines.append("    classDef current stroke-width:4px,stroke:#f6c177")
    lines.append("    classDef done opacity:0.55")
    return "\n".join(lines)
