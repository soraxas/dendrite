"""``dendrite`` command line: inspect experiments and lineage, or serve the UI."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time

from .experiment import Lab
from .viz import lineage_mermaid, mermaid, tree_mermaid


def _fmt_time(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "-"


def _table(rows: list[list[str]], header: list[str]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows, strict=False)]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, widths, strict=False))  # noqa: E731
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
                [
                    n["node"],
                    n["a_status"] or "-",
                    n["b_status"] or "-",
                    "shared" if n["same_execution"] else "same" if n["same_outputs"] else "differs",
                ]
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
                found.append(
                    (d["severity"], f"{shape['file'] or pipe.name}:{line}: {d['severity']} {d['code']}: {d['message']}")
                )
    for exp in lab.experiments():
        if exp.status in ("draft", "created") and exp.pipeline_name in _PIPELINES:
            for d in exp.check():
                if d["code"] not in ignore:
                    found.append(
                        (
                            d["severity"],
                            f"experiment {exp.name or exp.id} ({exp.id}): {d['severity']} {d['code']}: {d['message']}",
                        )
                    )
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
        [
            e["id"],
            e["name"] or "",
            e["status"],
            "keep" if e["keep"] else "",
            fmt_bytes(e["bytes"]),
            fmt_bytes(e["exclusive"]),
            fmt_bytes(e["with_forks"]) if e["forks"] else "",
        ]
        for e in sorted(inv["experiments"], key=lambda e: -e["exclusive"])
    ]
    print(_table(rows, ["id", "name", "status", "", "size", "only here", "with forks"]))
    print()
    rows = [
        [
            c["execution_id"],
            c["node"],
            fmt_bytes(c["bytes"]),
            str(c["reused"]),
            _fmt_time(c["last_used"]),
            "" if c["present"] else "evicted",
            "" if c["live"] else "unreferenced",
        ]
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
    print(
        f"delete {', '.join(est['ids'])}; frees ~{fmt_bytes(est['bytes'])} after `dendrite gc` (lineage records stay){kept}"
    )
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
        report = _reclaim(
            lab.gc, older, dry_run=False, force=args.force, hashes=[d["hash"] for d in preview["deleted"]]
        )
    else:
        report = preview
    _print_report(report, "delete", args.yes)


def cmd_clean(lab: Lab, args) -> None:
    out = _reclaim(lab.clean, force=args.force)
    print(
        f"removed {out['removed']} checkout/scratch entr{'y' if out['removed'] == 1 else 'ies'}, {fmt_bytes(out['bytes'])}"
    )


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
    if ui_mode() == "missing":
        print("the UI isn't built (only the API is served); run scripts/build-ui.sh")
    app = create_app(lab, allow_edit=allow_edit)

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            # Live-update streams (/api/events) never finish on their own: end them first, so a
            # graceful shutdown (Ctrl+C) completes instead of waiting on open browser tabs.
            app.state.closing.set()
            super().handle_exit(sig, frame)

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning", timeout_graceful_shutdown=3)
    Server(config).run()


def cmd_init(args) -> None:
    from pathlib import Path

    from .config import PROJECT_FILE, write_project_file

    target = Path(args.dir).resolve()
    if (target / PROJECT_FILE).exists() and not args.force:
        sys.exit(f"error: {target / PROJECT_FILE} exists (use --force to replace it)")
    path = write_project_file(target, args.server, args.project)
    print(f"wrote {path}: runs here record to {args.server}, project {args.project}")
    print("commit it, then run your pipeline; you'll be asked to sign in the first time")


def _server_of(args) -> tuple[str, str | None]:
    """The server (and project) to talk to: ``--server``/``--project``, else the resolved config."""
    from .config import Settings

    if getattr(args, "server", None):
        return args.server.rstrip("/"), getattr(args, "project", None)
    try:
        s = Settings.resolve()
    except ValueError as e:
        sys.exit(f"error: {e}")
    if not s.hosted:
        sys.exit("error: no server configured: pass --server, set DENDRITE_SERVER, or run `dendrite init`")
    return s.server, getattr(args, "project", None) or s.project


def _client(args, project: str | None = None):
    from .remote import ServerClient

    server, proj = _server_of(args)
    return ServerClient(server, project or proj or "")


def cmd_login(args) -> None:
    from .remote import device_login

    server, project = _server_of(args)
    device_login(server, project=project, out=sys.stdout)
    cmd_whoami(args)


def cmd_logout(args) -> None:
    from .remote import NotLoggedIn, save_token

    server, _ = _server_of(args)
    try:
        _client(args).request("POST", "/api/v1/logout")
    except NotLoggedIn:
        pass
    save_token(server, None)
    print(f"signed out of {server}")


def cmd_whoami(args) -> None:
    c = _client(args)
    s = c.request("GET", "/api/v1/session")
    who = s["user"]["email"] or s["user"]["name"] or s["user"]["id"]
    print(f"{who} on {c.server}" + (" (server admin)" if s["user"]["admin"] else ""))
    for p in s["projects"]:
        print(f"  {p['name']}: {p['role']}")


def cmd_token(args) -> None:
    c = _client(args)
    if args.action == "create":
        if not (args.project and args.role):
            sys.exit("error: token create needs --project and --role")
        t = c.request(
            "POST",
            "/api/v1/tokens",
            {"project": args.project, "role": args.role, "name": args.name, "ttl_days": args.days},
        )
        print(t["token"])
        print(
            f"# {t['name']}: {args.role} in {args.project}, id {t['id']}; shown once, store it as DENDRITE_TOKEN",
            file=sys.stderr,
        )
    elif args.action == "list":
        rows = c.request("GET", f"/api/v1/tokens?project={args.project}")["tokens"]
        print(
            _table(
                [[t["id"], t["name"] or "", t["role"] or "", _fmt_time(t["expires_at"])] for t in rows],
                ["id", "name", "role", "expires"],
            )
        )
    else:
        if not args.id:
            sys.exit("error: token revoke needs a token id")
        print("revoked" if c.request("DELETE", f"/api/v1/tokens/{args.id}")["revoked"] else "already revoked")


def cmd_project(args) -> None:
    c = _client(args)
    if args.action == "create":
        c.request("POST", "/api/v1/projects", {"name": args.name})
        print(f"created project {args.name}")
    else:
        for p in c.request("GET", "/api/v1/projects")["projects"]:
            print(f"{p['name']}: {p['role']}")


def cmd_member(args) -> None:
    c = _client(args)
    if args.action == "list":
        rows = c.request("GET", f"/api/v1/projects/{args.project_name}/members")["members"]
        print(_table([[m["email"] or m["id"], m["role"]] for m in rows], ["email", "role"]))
        return
    role = None if args.action == "remove" else args.role
    if args.action == "add" and role is None:
        sys.exit("error: member add needs a role (viewer, runner or admin)")
    c.request("PUT", f"/api/v1/projects/{args.project_name}/members", {"email": args.email, "role": role})
    print(f"{args.email}: {role or 'removed'} in {args.project_name}")


def cmd_server(args) -> None:
    from .hosted.settings import ServerSettings

    settings = ServerSettings.load(args.config)
    if args.action == "run":
        import uvicorn

        from .hosted.app import create_server_app

        app = create_server_app(settings)
        print(f"dendrite server at {settings.base_url} (auth: {settings.auth.provider}, store: {settings.store_url})")
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            timeout_graceful_shutdown=3,
            timeout_keep_alive=settings.keep_alive_seconds,
        )
        return
    if args.action == "check":
        from .hosted.check import run_checks

        marks = {"ok": "  ok  ", "warn": " warn ", "FAIL": " FAIL "}
        ok = run_checks(settings, lambda status, name, detail: print(f"[{marks[status]}] {name}: {detail}"))
        sys.exit(0 if ok else 1)
    from .hosted.app import Hosted

    server = Hosted(settings)
    if args.action == "admin-token":
        if not args.email:
            sys.exit("error: admin-token needs --email")
        user = server.control.invite(args.email, admin=True)
        secret, _ = server.control.create_token(user["id"], name="bootstrap")
        print(secret)
        print(f"# a server-admin token for {args.email}; shown once", file=sys.stderr)
    elif args.action == "adopt":
        if not (args.project and args.schema):
            sys.exit("error: adopt needs --project and --schema (and --store for its blobs)")
        server.create_project(args.project, db_schema=args.schema, store_url=args.store)
        print(f"adopted schema {args.schema} as project {args.project}")
    elif args.action == "create-project":
        server.create_project(args.project)
        print(f"created project {args.project}")


def cmd_push(args) -> None:
    """Copy experiments from this machine's local lab to the team server."""
    from pathlib import Path

    from .push import push

    home = Path(args.home or os.environ.get("DENDRITE_HOME", ".dendrite"))
    db_url = args.from_db_url or f"sqlite:///{home.resolve() / 'lineage.db'}"
    if not args.from_db_url and not (home / "lineage.db").is_file():
        sys.exit(f"error: no local lab at {home} (pass --home or --from-db-url)")
    source = Lab(home, db_url=db_url, store_url=args.from_store_url or str(home / "store"))
    server, project = _server_of(args)
    target = Lab(home / "push-cache", server=server, project=project)
    if not args.experiments and not args.all:
        sys.exit("error: name experiments to push, or pass --all")
    try:
        push(source, target, None if args.all else args.experiments, parents=not args.no_parents)
    except (KeyError, ValueError) as e:
        sys.exit(f"error: {e.args[0] if e.args else e}")
    print(f"open {server}/p/{project}/")


# Commands that don't open a Lab (they configure, sign in to or run a server).
NO_LAB = {"init", "login", "logout", "whoami", "token", "project", "member", "server", "push"}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="dendrite", description=__doc__)
    p.add_argument("--home", help="lab directory (default $DENDRITE_HOME or .dendrite)")
    p.add_argument("--db-url", help="SQLAlchemy URL (default $DENDRITE_DB_URL or SQLite in --home)")
    p.add_argument("--store-url", help="artifact store path or fsspec URL (default $DENDRITE_STORE_URL)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="point this repo at a dendrite server (writes dendrite.toml)")
    s.add_argument("--server", required=True, help="server URL, e.g. https://lineage.example.com")
    s.add_argument("--project", required=True)
    s.add_argument("--dir", default=".", help="where to write dendrite.toml (default: here)")
    s.add_argument("--force", action="store_true", help="replace an existing dendrite.toml")
    for name, help_ in (
        ("login", "sign in to the dendrite server"),
        ("logout", "sign out and revoke this machine's token"),
        ("whoami", "who you are on the server, and your projects"),
    ):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--server", help="server URL (default: from dendrite.toml or DENDRITE_SERVER)")
    s = sub.add_parser("push", help="copy experiments from this machine's local lab to the team server")
    s.add_argument("experiments", nargs="*", help="experiment ids or names (or --all)")
    s.add_argument("--all", action="store_true", help="every experiment in the local lab")
    s.add_argument("--no-parents", action="store_true", help="don't bring the experiments a fork came from")
    s.add_argument("--from-db-url", help="local metadata URL (default: SQLite in --home)")
    s.add_argument("--from-store-url", help="local store (default: <home>/store)")
    s.add_argument("--server", help="server URL (default: from dendrite.toml or DENDRITE_SERVER)")
    s.add_argument("--project", help="project (default: from dendrite.toml or DENDRITE_PROJECT)")
    s = sub.add_parser("token", help="service tokens for CI (project admins)")
    s.add_argument("action", choices=["create", "list", "revoke"])
    s.add_argument("id", nargs="?", help="token id (revoke)")
    s.add_argument("--project")
    s.add_argument("--role", choices=["viewer", "runner", "admin"])
    s.add_argument("--name")
    s.add_argument("--days", type=int, default=365, help="lifetime (default 365)")
    s.add_argument("--server")
    s = sub.add_parser("project", help="list projects, or create one (server admins)")
    s.add_argument("action", choices=["list", "create"])
    s.add_argument("name", nargs="?")
    s.add_argument("--server")
    s = sub.add_parser("member", help="manage a project's members (project admins)")
    s.add_argument("action", choices=["list", "add", "remove"])
    s.add_argument("project_name", metavar="project")
    s.add_argument("email", nargs="?")
    s.add_argument("role", nargs="?", choices=["viewer", "runner", "admin"])
    s.add_argument("--server")
    s = sub.add_parser("server", help="run or administer a dendrite server (on the server host)")
    s.add_argument("action", choices=["run", "check", "admin-token", "adopt", "create-project"])
    s.add_argument("--config", default=os.environ.get("DENDRITE_SERVER_CONFIG", "server.toml"))
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8455)
    s.add_argument("--email", help="admin-token: whose")
    s.add_argument("--project", help="adopt/create-project: the project name")
    s.add_argument("--schema", help="adopt: the existing Postgres schema")
    s.add_argument("--store", help="adopt: the existing blob store URL (default: <store_url>/<project>)")
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
    if args.cmd in NO_LAB:
        globals()[f"cmd_{args.cmd}"](args)
        return
    lab = Lab(args.home, db_url=args.db_url, store_url=args.store_url)
    try:
        globals()[f"cmd_{args.cmd}"](lab, args)
    except KeyError as e:
        sys.exit(f"error: {e.args[0] if e.args else e}")


if __name__ == "__main__":
    main()
