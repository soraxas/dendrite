"""Field-test dendrite on a real pipeline and report what worked: field_test.py --import MOD --pipeline NAME ...

Runs the pipeline once (cold), then an identical copy (which should reuse every step), and
optionally reruns every step to catch nondeterministic ones. Prints a Markdown report per step:
timing, reuse, console capture, metrics, samples and environment, plus anything suspicious.

    uv run python scripts/field_test.py --import my_project.pipelines --pipeline reconstruction \\
        --input video=data/scan.mp4 --param train.iterations=2000 --rerun-check --out report.md

Use a small but real input: the point is to exercise your actual tools (COLMAP, CUDA, ...),
not to wait for a full-size run. See docs/field-testing.md for what to look at in the UI.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
import warnings
from pathlib import Path

from dendrite import Lab, NondeterministicStep


def _value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _pairs(items: list[str] | None) -> dict[str, str]:
    out = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep:
            sys.exit(f"expected KEY=VALUE, got {item!r}")
        out[key] = value
    return out


def _step_rows(lab: Lab, exp) -> list[dict]:
    rows = []
    for nr in exp.node_runs():
        eid = nr.get("execution_id")
        ex = lab.db.execution(eid) if eid else None
        blob = lab.db.log_blob(eid) if eid else None
        chunks = lab.db.logs(eid) if eid and blob is None else []
        log_bytes = blob["size"] if blob else sum(len(c["text"].encode()) for c in chunks)
        log_lines = sum(c["text"].count("\n") for c in chunks) if not blob else None
        env = lab.db.environment(eid) if eid else None
        rows.append(
            {
                "node": nr["node"],
                "status": nr["status"],
                "seconds": (ex["finished_at"] - ex["started_at"]) if ex and ex.get("finished_at") else None,
                "log_bytes": log_bytes,
                "log_lines": log_lines,
                "stored_log": blob is not None,
                "metrics": len(lab.db.scalars([eid]).get(eid, [])) if eid else 0,
                "samples": len(lab.db.samples(eid)) if eid else 0,
                "env": bool(env),
                "git_dirty": bool(env and env.get("git") and env["git"].get("dirty")),
                "execution": eid,
            }
        )
    return rows


def _fmt_s(s: float | None) -> str:
    return "" if s is None else f"{s:.1f}s"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--import", dest="imports", action="append", required=True, help="module defining the pipeline")
    ap.add_argument("--pipeline", required=True, help="pipeline name (as registered by @pipeline)")
    ap.add_argument("--input", action="append", metavar="NAME=PATH", help="pipeline input (repeatable)")
    ap.add_argument("--param", action="append", metavar="NODE.ARG=VALUE", help="param override, JSON or text")
    ap.add_argument("--rerun-check", action="store_true", help="rerun every step to find nondeterministic ones")
    ap.add_argument("--home", help="lab directory (default: a fresh one under .dendrite-field-test)")
    ap.add_argument("--out", help="also write the report to this file")
    args = ap.parse_args()

    for mod in args.imports:
        importlib.import_module(mod)
    home = Path(args.home or f".dendrite-field-test/{time.strftime('%Y%m%d-%H%M%S')}")
    lab = Lab(home)
    inputs = {k: Path(v) for k, v in _pairs(args.input).items()}
    params = {k: _value(v) for k, v in _pairs(args.param).items()}
    report: list[str] = [f"# dendrite field test: `{args.pipeline}`", "", f"Lab: `{home}`", ""]
    problems: list[str] = []

    t0 = time.perf_counter()
    first = lab.create(args.pipeline, inputs=inputs, params=params, name="field-test cold")
    try:
        first.run()
    except Exception as e:  # report what happened up to the failure
        problems.append(f"The cold run failed: `{e!r}`")
    cold = time.perf_counter() - t0
    rows = _step_rows(lab, first.refresh())

    t0 = time.perf_counter()
    second = first.fork(name="field-test warm")
    try:
        second.run()
    except Exception as e:
        problems.append(f"The warm run failed: `{e!r}`")
    warm = time.perf_counter() - t0
    warm_rows = {r["node"]: r for r in _step_rows(lab, second.refresh())}

    nondeterministic: list[str] = []
    if args.rerun_check and first.status == "completed":
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", NondeterministicStep)
            third = first.fork(name="field-test rerun")
            third.run(rerun=True)
        nondeterministic = [
            str(w.message).split(" is not", 1)[0] for w in caught if issubclass(w.category, NondeterministicStep)
        ]

    report += [
        f"Cold run: {_fmt_s(cold)} ({first.status}). Identical second run: {_fmt_s(warm)} ({second.status}). "
        "Both include Prefect's start-up; the per-step times are the work itself.",
        "",
        "| step | cold | took | warm | console | metrics | samples | environment |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        w = warm_rows.get(r["node"], {})
        console = "stored file" if r["stored_log"] else f"{r['log_lines']} lines" if r["log_bytes"] else "**empty**"
        env = ("dirty git" if r["git_dirty"] else "yes") if r["env"] else "**no**"
        report.append(
            f"| `{r['node']}` | {r['status']} | {_fmt_s(r['seconds'])} | {w.get('status', '—')} | {console} "
            f"| {r['metrics']} | {r['samples']} | {env} |"
        )
        if w and w["status"] not in ("cached", "pinned"):
            problems.append(
                f"`{r['node']}` ran again on an identical run ({w['status']}): something in its signature "
                "changes between runs (an input file rewritten in place, or a param computed at run time)."
            )
        if r["status"] == "computed" and not r["log_bytes"]:
            problems.append(
                f"`{r['node']}` printed nothing that was captured. Fine if it's silent; if you expected "
                "output (a tool's progress, warnings), see \"What isn't captured\" in docs/telemetry.md."
            )
        if r["git_dirty"]:
            problems.append(f"`{r['node']}` ran on a dirty git tree: its environment records the diff.")
    for n in nondeterministic:
        problems.append(f"`{n}` is not deterministic: rerunning it gave different outputs.")
    if args.rerun_check and not nondeterministic and first.status == "completed":
        report += ["", "Rerun check: every step reproduced its outputs."]
    report += ["", "## Things to look at", ""]
    report += [f"- {p}" for p in dict.fromkeys(problems)] or ["- Nothing suspicious found."]
    report += [
        "",
        f"Open `uv run dendrite ui --import {' --import '.join(args.imports)}` with `DENDRITE_HOME={home}` "
        "and walk through docs/field-testing.md.",
    ]
    text = "\n".join(report) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text)


if __name__ == "__main__":
    main()
