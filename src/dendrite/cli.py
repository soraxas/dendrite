"""``dendrite`` command line: inspect experiments and lineage, or serve the UI."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time

from .experiment import Lab
from .viz import lineage_mermaid, mermaid, tree_mermaid


def _fmt_time(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "-"


def _table(rows: list[list[str]], header: list[str]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, widths))  # noqa: E731
    return "\n".join([line(header), line(["-" * w for w in widths]), *map(line, rows)])


def cmd_list(lab: Lab, args) -> None:
    rows = []
    for e in lab.tree():
        counts = " ".join(f"{k}={v}" for k, v in sorted(e["counts"].items()))
        rows.append([e["id"], e["name"] or "", e["parent_id"] or "", e["status"], _fmt_time(e["created_at"]), counts])
    print(_table(rows, ["id", "name", "parent", "status", "created", "nodes"]))


def cmd_show(lab: Lab, args) -> None:
    exp = lab.get(args.experiment)
    s = exp.summary()
    print(f"{s['id']}  {s['name'] or ''}  [{s['status']}]  pipeline={s['pipeline']}  parent={s['parent_id'] or '-'}")
    print("params:", json.dumps(s["params"], sort_keys=True))
    if s["pins"]:
        print("pins:  ", json.dumps({n: {o: h[:12] for o, h in v.items()} for n, v in s["pins"].items()}))
    metrics = exp.metrics()
    rows = []
    for n in exp.dag()["nodes"]:
        if n["kind"] != "node":
            continue
        outs = ", ".join(f"{k}={v['hash'][:10]}" for k, v in n["outputs"].items() if v)
        m = ", ".join(f"{k}={v:.4g}" for k, v in metrics.get(n["name"], {}).items())
        dur = f"{n['duration']:.2f}s" if n["duration"] is not None else ""
        rows.append([n["name"], n["status"], (n["execution_id"] or "")[:10], dur, outs, m])
    print(_table(rows, ["node", "status", "execution", "time", "outputs", "metrics"]))


def cmd_diff(lab: Lab, args) -> None:
    d = lab.get(args.a).diff(lab.get(args.b))
    print(f"A={d['a']}  B={d['b']}")
    if d["params"]:
        print(_table([[p["key"], json.dumps(p["a"]), json.dumps(p["b"])] for p in d["params"]], ["param", "A", "B"]))
    else:
        print("params identical")
    print()
    print(
        _table(
            [
                [n["node"], n["a_status"] or "-", n["b_status"] or "-", "shared" if n["same_execution"] else "same" if n["same_outputs"] else "differs"]
                for n in d["nodes"]
            ],
            ["node", "A", "B", "result"],
        )
    )
    if d["metrics"]:
        print()
        print(_table([[m["node"], m["key"], m["a"], m["b"]] for m in d["metrics"]], ["node", "metric", "A", "B"]))


def cmd_dag(lab: Lab, args) -> None:
    print(mermaid(lab.get(args.experiment).dag()))


def cmd_tree(lab: Lab, args) -> None:
    print(tree_mermaid(lab.tree()))


def cmd_lineage(lab: Lab, args) -> None:
    g = lab.lineage(args.artifact)
    if args.mermaid:
        print(lineage_mermaid(g))
        return
    a = g["artifact"]
    print(f"artifact {a['hash']}  {a['kind']}  {a['filename'] or ''}  {a['size']} B")
    print(f"producer: {g['producer'] or 'source input'}")
    for title, ids in (("ancestors", g["ancestors"]), ("descendants", g["descendants"])):
        execs = {x["id"]: x for x in g["executions"]}
        print(f"{title}:")
        for i in ids:
            print(f"  {execs[i]['node']:<24} exec {i}")
    print("experiments:", ", ".join(f"{e['name'] or e['id']} ({'/'.join(e['roles'])})" for e in g["experiments"]) or "-")


def cmd_ui(lab: Lab, args) -> None:
    import uvicorn

    from .server import create_app

    for mod in args.imports or []:
        importlib.import_module(mod)
    print(f"dendrite UI on http://{args.host}:{args.port}  ({lab})")
    uvicorn.run(create_app(lab), host=args.host, port=args.port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="dendrite", description=__doc__)
    p.add_argument("--home", help="lab directory (default $DENDRITE_HOME or .dendrite)")
    p.add_argument("--db-url", help="SQLAlchemy URL (default $DENDRITE_DB_URL or SQLite in --home)")
    p.add_argument("--store-url", help="artifact store path or fsspec URL (default $DENDRITE_STORE_URL)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list experiments")
    s = sub.add_parser("show", help="show an experiment's nodes, outputs and metrics")
    s.add_argument("experiment")
    s = sub.add_parser("diff", help="compare two experiments")
    s.add_argument("a")
    s.add_argument("b")
    s = sub.add_parser("dag", help="Mermaid DAG of an experiment")
    s.add_argument("experiment")
    sub.add_parser("tree", help="Mermaid fork tree of all experiments")
    s = sub.add_parser("lineage", help="provenance of an artifact (hash or unique prefix)")
    s.add_argument("artifact")
    s.add_argument("--mermaid", action="store_true")
    s = sub.add_parser("ui", help="serve the lineage web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--import", dest="imports", action="append", help="module to import (registers pipelines)")
    args = p.parse_args(argv)
    lab = Lab(args.home, db_url=args.db_url, store_url=args.store_url)
    try:
        globals()[f"cmd_{args.cmd}"](lab, args)
    except KeyError as e:
        sys.exit(f"error: {e.args[0] if e.args else e}")


if __name__ == "__main__":
    main()
