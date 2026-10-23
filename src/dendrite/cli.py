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
    print(f"artifact {a['label']}  {a['hash']}  {a['kind']}  {a['size']} B")
    print(f"producer: {a['producer_node'] + ' (exec ' + g['producer'] + ')' if g['producer'] else 'source input'}")
    for title, ids in (("ancestors", g["ancestors"]), ("descendants", g["descendants"])):
        execs = {x["id"]: x for x in g["executions"]}
        print(f"{title}:")
        for i in ids:
            print(f"  {execs[i]['node']:<24} exec {i}")
    print("experiments:")
    for e in g["experiments"]:
        print(f"  {e['name'] or e['id']:<20} {e['id']}  {describe_roles(e['roles'])}")


def describe_roles(roles: list[dict]) -> str:
    """e.g. ``input video · produced by extract · read by colmap, train``."""
    parts = []
    inputs = [r["name"] for r in roles if r["kind"] == "input"]
    if inputs:
        parts.append("input " + ", ".join(inputs))
    for kind, verb in (("produced", "produced by"), ("read", "read by")):
        nodes = list(dict.fromkeys(r["node"] for r in roles if r["kind"] == kind))
        if nodes:
            parts.append(f"{verb} {', '.join(nodes)}")
    return " · ".join(parts)


def cmd_check(lab: Lab, args) -> None:
    """Static findings for imported steps and pipelines, and for experiments not yet run."""
    import warnings

    from .analysis import StepDefinitionWarning
    from .experiment import _PIPELINES
    from .task import _STEPS

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", StepDefinitionWarning)  # reported below, once
        for mod in args.imports or []:
            importlib.import_module(mod)
    ignore = set(args.ignore or [])
    found: list[tuple[str, str]] = []
    for step in _STEPS.values():
        found += [(d.severity, d.render()) for d in step.diagnostics if d.code not in ignore]
    for pipe in _PIPELINES.values():
        shape = pipe.shape()
        for d in shape["diagnostics"]:
            if d["code"] not in ignore:
                line = (d["line"] or 1) + shape["first_line"] - 1
                found.append((d["severity"], f"{shape['file'] or pipe.name}:{line}: {d['severity']} {d['code']}: {d['message']}"))
    for exp in lab.experiments():
        if exp.status in ("draft", "created") and exp.pipeline_name in _PIPELINES:
            for d in exp.check():
                if d["code"] not in ignore:
                    found.append((d["severity"], f"experiment {exp.name or exp.id} ({exp.id}): {d['severity']} {d['code']}: {d['message']}"))
    for _, text in found:
        print(text)
    errors = sum(sev == "error" for sev, _ in found)
    print(f"{errors} error(s), {len(found) - errors} warning(s) in {len(_STEPS)} steps and {len(_PIPELINES)} pipelines")
    if errors:
        sys.exit(1)


def cmd_run(lab: Lab, args) -> None:
    for mod in args.imports or []:
        importlib.import_module(mod)
    exp = lab.get(args.experiment)
    if exp.status not in ("draft", "created"):
        sys.exit(f"error: {exp.id} is {exp.status}; fork it to run a variation")
    print(f"running {exp.name or exp.id} ({exp.id})")
    try:
        exp.run()
    finally:
        for nr in exp.node_runs():
            print(f"  {nr['node']:<24} {nr['status']}")
    print(exp.refresh().status)


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def cmd_du(lab: Lab, args) -> None:
    inv = lab.storage()
    if args.json:
        print(json.dumps(inv, indent=2, default=str))
        return
    st = inv["store"]
    print(f"store {st['url']}: {st['objects']} objects, {fmt_bytes(st['bytes'])}")
    print(
        f"  live {fmt_bytes(st['live']['bytes'])} (shared {fmt_bytes(st['shared']['bytes'])})"
        f" · collectable {fmt_bytes(st['collectable']['bytes'])} · scratch "
        + ", ".join(f"{k} {fmt_bytes(v)}" for k, v in inv["scratch"].items())
    )
    print()
    rows = [
        [e["id"], e["name"] or "", e["status"], "keep" if e["keep"] else "", fmt_bytes(e["bytes"]), fmt_bytes(e["exclusive"]),
         fmt_bytes(e["with_forks"]) if e["forks"] else ""]
        for e in sorted(inv["experiments"], key=lambda e: -e["exclusive"])
    ]
    print(_table(rows, ["id", "name", "status", "", "size", "only here", "with forks"]))
    print()
    rows = [
        [c["execution_id"], c["node"], fmt_bytes(c["bytes"]), str(c["reused"]), _fmt_time(c["last_used"]),
         "" if c["present"] else "evicted", "" if c["live"] else "unreferenced"]
        for c in inv["cache"][: args.top]
    ]
    print(_table(rows, ["execution", "step", "size", "reused", "last used", "", ""]))
    for why in inv["busy"]:
        print(f"note: {why}")


def _print_report(report: dict, verb: str, yes: bool) -> None:
    for d in report["deleted"]:
        print(f"  {d['hash'][:12]}  {fmt_bytes(d['size']):>10}  {d['why']}")
    skipped: dict[str, int] = {}
    for s in report["skipped"]:
        skipped[s["why"]] = skipped.get(s["why"], 0) + 1
    if skipped:
        print("skipped: " + ", ".join(f"{n} {why}" for why, n in sorted(skipped.items())))
    n, size = len(report["deleted"]), fmt_bytes(report["bytes"])
    if report["dry_run"]:
        print(f"would {verb} {n} object(s), {size}" + ("" if yes else "; re-run with --yes to do it"))
    else:
        print(f"{verb}d {n} object(s), {size}")


def _reclaim(fn, *a, **kw):
    from .storage import EvictRefused, StorageBusy

    try:
        return fn(*a, **kw)
    except (StorageBusy, EvictRefused, ValueError) as e:
        sys.exit(f"error: {e}")


def cmd_rm(lab: Lab, args) -> None:
    exp = lab.get(args.experiment)
    below = exp.descendants()
    if below and not (args.forks or args.keep_forks):
        sys.exit(
            f"error: {exp.id} has fork(s) {', '.join(e.id for e in below)}; pass --forks to delete them too, "
            "or --keep-forks to keep them as forks of its parent"
        )
    est = lab.deletion_estimate([exp.id], forks="delete" if args.forks else "keep")
    kept = f"; its forks become forks of {exp.parent_id or 'nothing'}" if below and args.keep_forks else ""
    print(f"delete {', '.join(est['ids'])}; frees ~{fmt_bytes(est['bytes'])} after `dendrite gc` (lineage records stay){kept}")
    if not args.yes:
        print("re-run with --yes to delete")
        return
    ids = _reclaim(exp.delete, forks=args.forks, keep_forks=args.keep_forks)
    print(f"deleted {len(ids)} experiment(s)")


def cmd_keep(lab: Lab, args) -> None:
    exp = lab.get(args.experiment).keep(not args.off)
    print(f"{exp.id}: keep {'on' if exp.kept else 'off'}")


def cmd_evict(lab: Lab, args) -> None:
    report = _reclaim(lab.evict, args.target, dry_run=not args.yes, force=args.force)
    _print_report(report, "evict", args.yes)


def cmd_gc(lab: Lab, args) -> None:
    older = args.older_than * 86400 if args.older_than is not None else None
    preview = _reclaim(lab.gc, older, force=args.force)
    if args.yes:  # delete exactly what was listed
        report = _reclaim(lab.gc, older, dry_run=False, force=args.force, hashes=[d["hash"] for d in preview["deleted"]])
    else:
        report = preview
    _print_report(report, "delete", args.yes)


def cmd_clean(lab: Lab, args) -> None:
    out = _reclaim(lab.clean, force=args.force)
    print(f"removed {out['removed']} checkout/scratch entr{'y' if out['removed'] == 1 else 'ies'}, {fmt_bytes(out['bytes'])}")


def cmd_ui(lab: Lab, args) -> None:
    import uvicorn

    from .server import create_app, ui_mode

    for mod in args.imports or []:
        importlib.import_module(mod)
    import ipaddress

    try:
        loopback = args.host == "localhost" or ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = False
    allow_edit = args.allow_edit or loopback
    print(f"dendrite UI on http://{args.host}:{args.port}  ({lab})")
    print(f"code editing {'enabled' if allow_edit else 'disabled (use --allow-edit)'}")
    if ui_mode() == "legacy":
        print("serving the legacy UI; run scripts/build-ui.sh to build the React UI")
    uvicorn.run(create_app(lab, allow_edit=allow_edit), host=args.host, port=args.port, log_level="warning")


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
    s = sub.add_parser("check", help="static checks of steps, pipelines and not-yet-run experiments")
    s.add_argument("--import", dest="imports", action="append", help="module to import (registers pipelines)")
    s.add_argument("--ignore", action="append", metavar="CODE", help="diagnostic code to skip (repeatable)")
    s = sub.add_parser("run", help="run a draft (or never-run) experiment in this process")
    s.add_argument("experiment")
    s.add_argument("--import", dest="imports", action="append", help="module to import (registers pipelines)")
    s = sub.add_parser("du", help="storage use: store totals, per experiment, cache entries")
    s.add_argument("--json", action="store_true")
    s.add_argument("--top", type=int, default=20, help="cache entries to list (largest first)")
    s = sub.add_parser("rm", help="delete an experiment (lineage records stay; then run gc)")
    s.add_argument("experiment")
    s.add_argument("--forks", action="store_true", help="also delete its forks")
    s.add_argument("--keep-forks", action="store_true", help="keep its forks, re-attached to its parent")
    s.add_argument("--yes", action="store_true", help="delete instead of previewing")
    s = sub.add_parser("keep", help="protect an experiment's data from age-based gc")
    s.add_argument("experiment")
    s.add_argument("--off", action="store_true", help="remove the protection")
    s = sub.add_parser("evict", help="delete an execution's (or artifact's) stored outputs; they recompute when needed")
    s.add_argument("target", help="execution id or artifact hash/prefix")
    s.add_argument("--force", action="store_true", help="even if pinned or while runs are in progress")
    s.add_argument("--yes", action="store_true", help="evict instead of previewing")
    s = sub.add_parser("gc", help="delete stored data no experiment uses")
    s.add_argument("--older-than", type=float, metavar="DAYS", help="also evict cache unused for DAYS")
    s.add_argument("--force", action="store_true", help="even while runs are in progress")
    s.add_argument("--yes", action="store_true", help="delete instead of previewing")
    s = sub.add_parser("clean", help="clear local checkouts and leftover step workdirs")
    s.add_argument("--force", action="store_true", help="even while runs are in progress")
    s = sub.add_parser("ui", help="serve the lineage web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8454)
    s.add_argument("--import", dest="imports", action="append", help="module to import (registers pipelines)")
    s.add_argument(
        "--allow-edit",
        action="store_true",
        help="let the UI save edited step source to disk even when not bound to localhost",
    )
    args = p.parse_args(argv)
    lab = Lab(args.home, db_url=args.db_url, store_url=args.store_url)
    try:
        globals()[f"cmd_{args.cmd}"](lab, args)
    except KeyError as e:
        sys.exit(f"error: {e.args[0] if e.args else e}")


if __name__ == "__main__":
    main()
