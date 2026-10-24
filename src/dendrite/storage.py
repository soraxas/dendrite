"""Storage accounting and reclamation: inventory, eviction, garbage collection, scratch clean-up.

What is kept is defined by **reachability**. A blob is live when an existing experiment
reaches it through its input hashes, its pins or its node runs' outputs, including the
members of directory manifests. Everything else in ``objects/`` is collectable. Metadata
rows are never deleted here: an artifact whose blob is gone shows as missing, and a cache
hit on it fails ``store.verify()`` and recomputes. So reclaiming space can cost time, never
correctness.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .store import ArtifactRef

if TYPE_CHECKING:
    from .experiment import Lab


class StorageBusy(RuntimeError):
    """A reclaiming action was refused because work is in progress (pass ``force=True``)."""


class EvictRefused(RuntimeError):
    """Eviction was refused, e.g. because a living experiment pins the outputs."""


@dataclass
class _Graph:
    """One consistent read of the metadata, plus the store listing."""

    objects: dict[str, tuple[int, float]]  # hash -> (size, mtime)
    artifacts: dict[str, dict]
    experiments: list[dict]
    node_runs: list[dict]
    executions: list[dict]
    manifests: dict[str, list[str]] = field(default_factory=dict)  # dir hash -> member hashes

    def size(self, h: str) -> int:
        return self.objects.get(h, (0, 0.0))[0]


def _pin_hashes(pins: dict | None) -> list[str]:
    return [h for outs in (pins or {}).values() for h in (outs or {}).values()]


class Storage:
    def __init__(self, lab: Lab):
        self.lab = lab

    # -- reading -----------------------------------------------------------------------
    def _read(self) -> _Graph:
        db = self.lab.db
        # Metadata first, then the listing: a blob published in between is then in the
        # listing but not yet in the metadata, which the grace period protects.
        g = _Graph(
            objects={},
            artifacts=db.all_artifacts(),
            experiments=db.experiments(),
            node_runs=db.all_node_runs(),
            executions=db.successful_executions(),
        )
        g.objects = {h: (size, mtime) for h, size, mtime in self.lab.store.iter_objects()}
        return g

    def _members(self, g: _Graph, h: str) -> list[str]:
        """Member hashes of a directory artifact (read once; empty if not a present dir)."""
        if h in g.manifests:
            return g.manifests[h]
        members: list[str] = []
        row = g.artifacts.get(h)
        if row and row["kind"] == "dir" and h in g.objects:
            try:
                members = [mh for _, mh, _ in self.lab.store.manifest(ArtifactRef(h, "dir", row["size"]))]
            except Exception:  # unreadable manifest: its members just aren't attributed
                members = []
        g.manifests[h] = members
        return members

    def _closure(self, g: _Graph, roots: Iterable[str]) -> set[str]:
        out: set[str] = set()
        for h in roots:
            if h and h not in out:
                out.add(h)
                out.update(self._members(g, h))
        return out

    def _roots(self, g: _Graph, skip_exec: Iterable[str] = ()) -> dict[str, set[str]]:
        """Root hashes per experiment id. ``skip_exec`` drops node-run outputs of those executions."""
        skip = set(skip_exec)
        roots: dict[str, set[str]] = {
            e["id"]: set(e["inputs"].values()) | set(_pin_hashes(e["pins"])) for e in g.experiments
        }
        for nr in g.node_runs:
            if nr["experiment_id"] in roots and nr["execution_id"] not in skip:
                roots[nr["experiment_id"]].update((nr["outputs"] or {}).values())
        return roots

    def _last_use(self, g: _Graph) -> dict[str, float]:
        """Per execution, the newest time an existing experiment's node run resolved to it."""
        now = time.time()
        last: dict[str, float] = {}
        for nr in g.node_runs:
            eid = nr["execution_id"]
            if eid:
                last[eid] = max(last.get(eid, 0.0), nr["finished_at"] or now)
        return last

    def busy(self) -> list[str]:
        """Why reclaiming is unsafe right now: running experiments and held leases."""
        why = [f"experiment {e['id']} is running" for e in self.lab.db.experiments() if e["status"] == "running"]
        why += [f"a step is computing (lease {lease['signature'][:12]})" for lease in self.lab.db.active_leases()]
        return why

    def _guard(self, force: bool) -> None:
        if not force:
            reasons = self.busy()
            if reasons:
                raise StorageBusy("; ".join(reasons) + " (wait for it to finish, or pass force=True)")

    def _reach(self, g: _Graph) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """(blobs per experiment, experiments per *present* blob)."""
        reach = {eid: self._closure(g, hs) for eid, hs in self._roots(g).items()}
        reachers: dict[str, set[str]] = {}
        for eid, hs in reach.items():
            for h in hs:
                if h in g.objects:
                    reachers.setdefault(h, set()).add(eid)
        return reach, reachers

    def frees(self, ids: Iterable[str]) -> int:
        """Bytes freed after GC by deleting these experiments: blobs only they reach."""
        targets = set(ids)
        g = self._read()
        _, reachers = self._reach(g)
        return sum(g.size(h) for h, rs in reachers.items() if rs <= targets)

    # -- inventory ---------------------------------------------------------------------
    def inventory(self, largest: int = 25) -> dict[str, Any]:
        g = self._read()
        reach, reachers = self._reach(g)
        reached = set().union(*reach.values())  # including blobs that are missing (evicted)
        live_bytes = sum(g.size(h) for h in reachers)
        shared = {h for h, r in reachers.items() if len(r) > 1}

        # Bytes freed by deleting an experiment *and its forks*: blobs whose reachers all sit
        # in that subtree, i.e. credited to every common ancestor of the reachers.
        parent = {e["id"]: e["parent_id"] for e in g.experiments}

        def chain(eid: str) -> list[str]:
            out = []
            while eid and eid in parent and eid not in out:
                out.append(eid)
                eid = parent[eid]
            return out

        subtree: dict[str, int] = {}
        for h, rs in reachers.items():
            common: set[str] | None = None
            for r in rs:
                c = set(chain(r))
                common = c if common is None else common & c
            for eid in common or ():
                subtree[eid] = subtree.get(eid, 0) + g.size(h)

        children: dict[str, list[str]] = {}
        for e in g.experiments:
            if e["parent_id"]:
                children.setdefault(e["parent_id"], []).append(e["id"])
        experiments = []
        for e in g.experiments:
            mine = reach[e["id"]]
            experiments.append(
                {
                    "id": e["id"],
                    "name": e["name"],
                    "pipeline": e["pipeline"],
                    "status": e["status"],
                    "parent_id": e["parent_id"],
                    "keep": e["keep"],
                    "created_at": e["created_at"],
                    "finished_at": e["finished_at"],
                    "forks": children.get(e["id"], []),
                    "bytes": sum(g.size(h) for h in mine if h in g.objects),
                    "exclusive": sum(g.size(h) for h in mine if reachers.get(h) == {e["id"]}),
                    "with_forks": subtree.get(e["id"], 0),
                }
            )

        last = self._last_use(g)
        uses: dict[str, list[dict]] = {}
        for nr in g.node_runs:
            if nr["execution_id"]:
                uses.setdefault(nr["execution_id"], []).append(nr)
        cache = []
        for ex in g.executions:
            blobs = self._closure(g, ex["outputs"].values())
            nrs = uses.get(ex["id"], [])
            cache.append(
                {
                    "execution_id": ex["id"],
                    "node": ex["node"],
                    "experiment_id": ex["experiment_id"],
                    "finished_at": ex["finished_at"],
                    "duration": (ex["finished_at"] - ex["started_at"]) if ex["finished_at"] else None,
                    "outputs": ex["outputs"],
                    "bytes": sum(g.size(h) for h in blobs),
                    "present": all(h in g.objects for h in blobs),
                    "uses": len(nrs),
                    "reused": sum(nr["status"] == "cached" for nr in nrs),
                    "last_used": last.get(ex["id"]),
                    "live": any(h in reached for h in blobs),  # some existing experiment uses it
                }
            )
        cache.sort(key=lambda c: -c["bytes"])

        producer: dict[str, str] = {}
        for ex in g.executions:
            for h in ex["outputs"].values():
                producer.setdefault(h, ex["node"])
        top = sorted(g.artifacts.values(), key=lambda a: -self._artifact_bytes(g, a["hash"]))[:largest]
        largest_rows = [
            {
                "hash": a["hash"],
                "kind": a["kind"],
                "filename": a["filename"],
                "size": self._artifact_bytes(g, a["hash"]),
                "producer_node": producer.get(a["hash"]),
                "present": a["hash"] in g.objects,
                "live": a["hash"] in reached,
                "experiments": len(reachers.get(a["hash"], ())),
            }
            for a in top
            if self._artifact_bytes(g, a["hash"])
        ]

        total = sum(size for size, _ in g.objects.values())
        return {
            "computed_at": time.time(),
            "store": {
                "url": self.lab.store.url,
                "objects": len(g.objects),
                "bytes": total,
                "live": {"objects": len(reachers), "bytes": live_bytes},
                "shared": {"objects": len(shared), "bytes": sum(g.size(h) for h in shared)},
                "collectable": {"objects": len(g.objects) - len(reachers), "bytes": total - live_bytes},
            },
            "experiments": experiments,
            "cache": cache,
            "largest": largest_rows,
            "scratch": self._scratch_sizes(),
            "busy": self.busy(),
        }

    def _artifact_bytes(self, g: _Graph, h: str) -> int:
        return sum(g.size(x) for x in self._closure(g, [h]))

    def _scratch_dirs(self) -> dict[str, Path]:
        return {"checkouts": self.lab.store.cache_dir / "checkouts", "tmp": self.lab.home / "tmp"}

    def _scratch_sizes(self) -> dict[str, int]:
        out = {}
        for key, root in self._scratch_dirs().items():
            out[key] = sum(_disk_size(p) for p in root.iterdir()) if root.is_dir() else 0
        return out

    # -- reclaiming --------------------------------------------------------------------
    def gc(
        self,
        older_than: float | None = None,
        *,
        dry_run: bool = True,
        grace: float | None = None,
        force: bool = False,
        hashes: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Delete blobs no existing experiment reaches; with ``older_than`` (seconds), also the
        outputs of executions last used before ``now - older_than``.

        Age-based eviction spares whatever a kept experiment, any pin, or the newest completed
        experiment of each pipeline reaches. Blobs modified within ``grace`` seconds are
        skipped. ``hashes`` restricts deletion to those (a previewed report's), so nothing
        that became collectable after the preview is deleted.
        """
        if not dry_run:
            self._guard(force)
        grace = self.lab.settings.gc_grace if grace is None else grace
        now = time.time()
        g = self._read()
        last = self._last_use(g)
        old: set[str] = set()
        if older_than is not None:
            cutoff = now - older_than
            old = {eid for eid, t in last.items() if t < cutoff}
        retained = self._closure(g, (h for hs in self._roots(g, skip_exec=old).values() for h in hs))
        live = retained if not old else self._closure(g, (h for hs in self._roots(g).values() for h in hs))

        protect: dict[str, str] = {}  # hash -> why it's spared from age eviction
        if old:
            newest: dict[str, dict] = {}
            for e in g.experiments:
                if e["status"] == "completed" and (
                    e["pipeline"] not in newest or (e["finished_at"] or 0) > (newest[e["pipeline"]]["finished_at"] or 0)
                ):
                    newest[e["pipeline"]] = e
            full = self._roots(g)
            for e in g.experiments:
                if e["keep"]:
                    for h in self._closure(g, full[e["id"]]):
                        protect.setdefault(h, "kept")
            for e in g.experiments:
                for h in self._closure(g, _pin_hashes(e["pins"])):
                    protect.setdefault(h, "pinned")
            for e in newest.values():
                for h in self._closure(g, full[e["id"]]):
                    protect.setdefault(h, "newest")
            protect = {h: why for h, why in protect.items() if h in live and h not in retained}
            retained |= set(protect)

        since: dict[str, float] = {}  # hash -> last use of the old execution that produced it
        for ex in g.executions:
            if ex["id"] in old:
                for h in self._closure(g, ex["outputs"].values()):
                    since[h] = max(since.get(h, 0.0), last[ex["id"]])

        members = self._all_members(g)
        wanted = set(hashes) if hashes is not None else None
        deleted, skipped = [], []
        for h, (size, mtime) in sorted(g.objects.items()):
            if h in retained:
                if h in protect:  # would have been evicted for age
                    skipped.append({"hash": h, "size": size, "why": protect[h]})
                continue
            if wanted is not None and h not in wanted:
                continue
            if mtime > now - grace:
                skipped.append({"hash": h, "size": size, "why": "grace"})
                continue
            if h in live:
                why = "unused-since:" + time.strftime("%Y-%m-%d", time.localtime(since.get(h, 0.0)))
            elif h in g.artifacts or h in members:
                why = "unreferenced"
            else:
                why = "orphan"
            deleted.append({"hash": h, "size": size, "why": why})
        if not dry_run:
            self._delete(d["hash"] for d in deleted)
        return _report(deleted, skipped, dry_run)

    def _delete(self, hashes: Iterable[str]) -> None:
        hs = list(hashes)
        self.lab.store.delete_many(hs)
        self.lab.store.drop_checkouts(hs)

    def _all_members(self, g: _Graph) -> set[str]:
        """Members of every registered directory still in the store (to tell members from orphans)."""
        out: set[str] = set()
        for h, row in g.artifacts.items():
            if row["kind"] == "dir":
                out.update(self._members(g, h))
        return out

    def evict(self, target: str, *, dry_run: bool = True, force: bool = False) -> dict[str, Any]:
        """Delete the outputs of one execution (or one artifact), keeping every metadata row.

        Blobs that something else still reaches (another execution's outputs, an experiment's
        input, a directory shared with another artifact) are skipped. Refused without
        ``force`` when a living experiment pins the outputs, or while work is running.
        """
        if not dry_run:
            self._guard(force)
        g = self._read()
        execution = next((ex for ex in g.executions if ex["id"] == target), None)
        if execution is not None:
            tops = set(execution["outputs"].values())
        else:
            art = self.lab.artifact(target)
            tops = {art.hash}
        pinned = [e["id"] for e in g.experiments if tops & set(_pin_hashes(e["pins"]))]
        if pinned and not force:
            raise EvictRefused(
                f"experiment {pinned[0]} pins these outputs, so it couldn't run without them (force=True to evict anyway)"
            )
        # Everything else that stays reachable. Experiment inputs always stay (they're the
        # user's data, not cache); pins of these outputs only reach here with ``force``.
        inputs = {h for e in g.experiments for h in e["inputs"].values()}
        others = set(inputs)
        others |= {h for e in g.experiments for h in _pin_hashes(e["pins"]) if h not in tops}
        others |= {h for nr in g.node_runs for h in (nr["outputs"] or {}).values() if h not in tops}
        keep = self._closure(g, others)
        deleted, skipped = [], []
        for h in sorted(self._closure(g, tops)):
            if h not in g.objects:
                continue
            size = g.size(h)
            if h in keep:
                skipped.append({"hash": h, "size": size, "why": "input" if h in inputs else "shared"})
            else:
                deleted.append({"hash": h, "size": size, "why": f"evicted:{execution['id'] if execution else h[:12]}"})
        if not dry_run:
            self._delete(d["hash"] for d in deleted)
        return _report(deleted, skipped, dry_run)

    def clean(self, *, force: bool = False, grace: float | None = None) -> dict[str, Any]:
        """Remove local checkouts and the workdirs of executions that aren't running.

        Never touches stored objects; checkouts re-materialise on the next load.
        """
        self._guard(force)
        grace = self.lab.settings.gc_grace if grace is None else grace
        now = time.time()
        freed, removed = 0, []
        checkouts, tmp = self._scratch_dirs().values()
        if checkouts.is_dir():
            for p in checkouts.iterdir():
                freed += _disk_size(p)
                _rm(p)
                removed.append(str(p))
        if tmp.is_dir():
            running = {e["id"]: e for e in self.lab.db.running_executions()}
            leased = {lease["signature"] for lease in self.lab.db.active_leases()}
            for p in tmp.iterdir():
                ex = running.get(p.name)
                if ex is not None and (ex["signature"] in leased or p.stat().st_mtime > now - grace):
                    continue  # still computing
                freed += _disk_size(p)
                _rm(p)
                removed.append(str(p))
        root = getattr(self.lab.store, "root", None)
        if root is not None:  # interrupted object writes in a local store
            for p in (Path(root) / "objects").glob("*/.tmp-*"):
                if p.stat().st_mtime < now - grace:
                    freed += _disk_size(p)
                    p.unlink(missing_ok=True)
        return {"removed": len(removed), "bytes": freed}


def _report(deleted: list[dict], skipped: list[dict], dry_run: bool) -> dict[str, Any]:
    groups: dict[str, dict[str, int]] = {}
    for d in deleted:
        g = groups.setdefault(d["why"].split(":", 1)[0], {"count": 0, "bytes": 0})
        g["count"] += 1
        g["bytes"] += d["size"]
    return {
        "dry_run": dry_run,
        "deleted": deleted,
        "bytes": sum(d["size"] for d in deleted),
        "skipped": skipped,
        "groups": groups,
    }


def _disk_size(p: Path) -> int:
    try:
        if p.is_symlink() or p.is_file():
            return p.lstat().st_size
        return sum(f.lstat().st_size for f in p.rglob("*") if not f.is_dir())
    except OSError:
        return 0


def _rm(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
    else:
        p.unlink(missing_ok=True)
