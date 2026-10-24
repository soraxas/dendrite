"""Mermaid renderings of experiment DAGs, fork trees and artifact lineage."""

from __future__ import annotations

from typing import Any

_CLASSES = {
    "computed": "fill:#d1f2d9,stroke:#1f8a3b,color:#0b3d1a",
    "cached": "fill:#d6e6fb,stroke:#2b6cb0,color:#102a4c",
    "pinned": "fill:#fdecc8,stroke:#b7791f,color:#4a3006",
    "running": "fill:#fff4b3,stroke:#b59f00,color:#3d3500",
    "waiting": "fill:#fff4b3,stroke:#b59f00,color:#3d3500,stroke-dasharray:4 3",
    "queued": "fill:#eeeeee,stroke:#888,color:#333,stroke-dasharray:4 3",
    "failed": "fill:#fbd5d5,stroke:#c53030,color:#4a0f0f",
    "input": "fill:#ffffff,stroke:#555,color:#222",
}


def _mid(s: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in s)


def mermaid(dag: dict[str, Any]) -> str:
    lines = ["flowchart LR"]
    for n in dag["nodes"]:
        if n["kind"] == "input":
            art = n.get("artifact") or {}
            lines.append(f'  {_mid(n["id"])}[/"{n["name"]}<br/><small>{art.get("hash", "")[:10]}</small>"/]:::input')
        else:
            exec_id = (n.get("execution_id") or "")[:8]
            lines.append(f'  {_mid(n["id"])}["{n["name"]}<br/><small>{n["status"]} {exec_id}</small>"]:::{n["status"]}')
    for e in dag["edges"]:
        lines.append(f"  {_mid(e['from'])} -->|{e['label']}| {_mid(e['to'])}")
    for cls, style in _CLASSES.items():
        lines.append(f"  classDef {cls} {style}")
    return "\n".join(lines)


def tree_mermaid(tree: list[dict[str, Any]]) -> str:
    lines = ["flowchart TD"]
    for e in tree:
        label = e["name"] or e["id"]
        counts = ", ".join(f"{v} {k}" for k, v in sorted(e["counts"].items()))
        lines.append(f'  {_mid(e["id"])}["{label}<br/><small>{e["id"]} · {e["status"]}<br/>{counts}</small>"]')
    for e in tree:
        if e["parent_id"]:
            lines.append(f"  {_mid(e['parent_id'])} --> {_mid(e['id'])}")
    return "\n".join(lines)


def lineage_mermaid(graph: dict[str, Any]) -> str:
    lines = ["flowchart LR"]
    focus = graph["artifact"]["hash"]
    for a in graph["artifacts"]:
        style = ":::pinned" if a["hash"] == focus else ""
        label = a.get("label") or a["filename"] or a["kind"]
        lines.append(f'  {_mid(a["hash"])}[("{label}<br/><small>{a["kind"]} {a["hash"][:10]}</small>")]{style}')
    for x in graph["executions"]:
        lines.append(f'  {_mid(x["id"])}["{x["node"]}<br/><small>exec {x["id"][:8]}</small>"]:::computed')
    for e in graph["edges"]:
        lines.append(f"  {_mid(e['from'])} -->|{e['label']}| {_mid(e['to'])}")
    lines.append(f"  classDef computed {_CLASSES['computed']}")
    lines.append(f"  classDef pinned {_CLASSES['pinned']}")
    return "\n".join(lines)
