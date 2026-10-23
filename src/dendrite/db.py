"""Relational lineage metadata on SQLAlchemy Core (SQLite or PostgreSQL).

Entities are kept deliberately separate:

* ``experiments``  – immutable configuration snapshots, optionally forked from a parent
* ``node_runs``    – a node's outcome within one experiment (computed / cached / pinned …)
* ``executions``   – a physical computation, shared by every node run that resolved to it
* ``artifacts``    – immutable content-addressed outputs
* ``execution_inputs`` / ``execution_outputs`` – the lineage edges
* ``leases``       – one row per signature currently being computed
* ``experiment_flags`` – per-experiment flags (``keep``); a separate table because
  ``create_all`` can't add columns to an existing ``experiments`` table
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from .store import ArtifactRef

meta = sa.MetaData()

experiments = sa.Table(
    "experiments",
    meta,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("name", sa.String(200), index=True),
    sa.Column("parent_id", sa.String(32), sa.ForeignKey("experiments.id"), index=True),
    sa.Column("pipeline", sa.String(200), nullable=False),
    sa.Column("params", sa.JSON, nullable=False),
    sa.Column("inputs", sa.JSON, nullable=False),  # name -> artifact hash
    sa.Column("pins", sa.JSON, nullable=False),  # node -> {output -> artifact hash}
    sa.Column("status", sa.String(20), nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("started_at", sa.Float),
    sa.Column("finished_at", sa.Float),
    sa.Column("flow_run_id", sa.String(64)),
    sa.Column("error", sa.Text),
)

artifacts = sa.Table(
    "artifacts",
    meta,
    sa.Column("hash", sa.String(64), primary_key=True),
    sa.Column("kind", sa.String(10), nullable=False),
    sa.Column("size", sa.BigInteger, nullable=False),
    sa.Column("filename", sa.String(500)),
    sa.Column("created_at", sa.Float, nullable=False),
)

executions = sa.Table(
    "executions",
    meta,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("signature", sa.String(64), nullable=False),
    sa.Column("node", sa.String(200), nullable=False),
    sa.Column("spec", sa.JSON, nullable=False),
    sa.Column("cacheable", sa.Boolean, nullable=False),
    sa.Column("status", sa.String(20), nullable=False),  # running | success | failed
    sa.Column("experiment_id", sa.String(32)),  # the experiment that triggered it
    sa.Column("task_run_id", sa.String(64)),
    sa.Column("started_at", sa.Float, nullable=False),
    sa.Column("finished_at", sa.Float),
    sa.Column("error", sa.Text),
    sa.Index("ix_executions_signature_status", "signature", "status"),
)

execution_inputs = sa.Table(
    "execution_inputs",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("name", sa.String(300), primary_key=True),
    sa.Column("artifact_hash", sa.String(64), sa.ForeignKey("artifacts.hash"), nullable=False, index=True),
)

execution_outputs = sa.Table(
    "execution_outputs",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("name", sa.String(300), primary_key=True),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("artifact_hash", sa.String(64), sa.ForeignKey("artifacts.hash"), nullable=False, index=True),
)

metrics = sa.Table(
    "metrics",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("key", sa.String(200), primary_key=True),
    sa.Column("step", sa.Integer, primary_key=True),
    sa.Column("value", sa.Float, nullable=False),
)

node_runs = sa.Table(
    "node_runs",
    meta,
    sa.Column("experiment_id", sa.String(32), sa.ForeignKey("experiments.id"), primary_key=True),
    sa.Column("node", sa.String(200), primary_key=True),
    sa.Column("seq", sa.Integer, nullable=False),
    # queued | running | waiting | computed | cached | pinned | failed
    sa.Column("status", sa.String(20), nullable=False),
    sa.Column("signature", sa.String(64)),
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id")),
    sa.Column("inputs", sa.JSON),  # input name -> artifact hash
    sa.Column("outputs", sa.JSON),  # output name -> artifact hash
    sa.Column("params", sa.JSON),
    sa.Column("queued_at", sa.Float),
    sa.Column("started_at", sa.Float),
    sa.Column("finished_at", sa.Float),
    sa.Column("error", sa.Text),
)

# Source snapshots are kept outside the signature spec so recording them never changes cache keys.
code_snapshots = sa.Table(
    "code_snapshots",
    meta,
    sa.Column("hash", sa.String(64), primary_key=True),  # sha256 of the source text
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("file", sa.Text),
    sa.Column("line", sa.Integer),
    sa.Column("module", sa.String(300)),
    sa.Column("qualname", sa.String(300)),
    sa.Column("created_at", sa.Float, nullable=False),
)

execution_sources = sa.Table(
    "execution_sources",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("source_hash", sa.String(64), sa.ForeignKey("code_snapshots.hash"), nullable=False, index=True),
)

leases = sa.Table(
    "leases",
    meta,
    sa.Column("signature", sa.String(64), primary_key=True),
    sa.Column("owner", sa.String(64), nullable=False),
    sa.Column("expires_at", sa.Float, nullable=False),
)


# Artifacts the pipeline body itself read (`lab.load` in the body, e.g. a config or a plan that
# decides a fan-out). `after` is how many nodes had been called when it was read.
pipeline_reads = sa.Table(
    "pipeline_reads",
    meta,
    sa.Column("experiment_id", sa.String(32), sa.ForeignKey("experiments.id"), primary_key=True),
    sa.Column("seq", sa.Integer, primary_key=True),
    sa.Column("artifact_hash", sa.String(64), nullable=False),
    sa.Column("after", sa.Integer, nullable=False),
)

# Live progress a running step reports (`dendrite.progress`), per execution.
execution_progress = sa.Table(
    "execution_progress",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("done", sa.Float, nullable=False),
    sa.Column("total", sa.Float),
    sa.Column("unit", sa.String(40)),
    sa.Column("updated_at", sa.Float, nullable=False),
)

# The stage path (`dendrite.stage`) each node was called in, per experiment: display only.
node_stages = sa.Table(
    "node_stages",
    meta,
    sa.Column("experiment_id", sa.String(32), sa.ForeignKey("experiments.id"), primary_key=True),
    sa.Column("node", sa.String(200), primary_key=True),
    sa.Column("path", sa.JSON, nullable=False),  # [{name, description, color}], outermost first
)

# A pipeline's UI view: steps assigned to stages and stage labels/colours, overlaying the code.
pipeline_views = sa.Table(
    "pipeline_views",
    meta,
    sa.Column("pipeline", sa.String(200), primary_key=True),
    sa.Column("view", sa.JSON, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False),
)

experiment_flags = sa.Table(
    "experiment_flags",
    meta,
    sa.Column("experiment_id", sa.String(32), sa.ForeignKey("experiments.id"), primary_key=True),
    sa.Column("keep", sa.Boolean, nullable=False, default=False),
    sa.Column("updated_at", sa.Float, nullable=False),
)

# Experiment rows carry their keep flag.
_EXPERIMENT_ROW = sa.select(experiments, sa.func.coalesce(experiment_flags.c.keep, False).label("keep")).outerjoin(
    experiment_flags, experiment_flags.c.experiment_id == experiments.c.id
)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _sqlite_engine(url: str, immediate: bool) -> sa.Engine:
    db_path = sa.engine.make_url(url).database
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(url, connect_args={"timeout": 60, "check_same_thread": False})

    @sa.event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _):
        dbapi_conn.isolation_level = None  # let SQLAlchemy events control BEGIN
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=60000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    @sa.event.listens_for(engine, "begin")
    def _on_begin(conn):
        # Writers take the lock up front so WAL never has to upgrade a read lock (which
        # fails immediately with SQLITE_BUSY instead of waiting).
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    return engine


class Metadata:
    def __init__(self, url: str):
        self.url = url
        if url.startswith("sqlite"):
            self.engine = _sqlite_engine(url, immediate=True)
            self.reader = _sqlite_engine(url, immediate=False)
        else:
            self.engine = sa.create_engine(url, pool_pre_ping=True)
            self.reader = self.engine
        meta.create_all(self.engine)

    @contextmanager
    def tx(self) -> Iterator[sa.Connection]:
        with self.engine.begin() as conn:
            yield conn

    @contextmanager
    def ro(self) -> Iterator[sa.Connection]:
        with self.reader.connect() as conn:
            yield conn

    def dispose(self) -> None:
        self.engine.dispose()
        if self.reader is not self.engine:
            self.reader.dispose()

    # -- artifacts -----------------------------------------------------------------
    def _register_artifacts(self, conn: sa.Connection, refs: Iterable[ArtifactRef]) -> None:
        for ref in {r.hash: r for r in refs}.values():
            exists = conn.execute(sa.select(artifacts.c.hash).where(artifacts.c.hash == ref.hash)).first()
            if not exists:
                conn.execute(
                    artifacts.insert().values(
                        hash=ref.hash, kind=ref.kind, size=ref.size, filename=ref.filename, created_at=time.time()
                    )
                )

    def register_artifacts(self, refs: Iterable[ArtifactRef]) -> None:
        with self.tx() as conn:
            self._register_artifacts(conn, refs)

    def artifact(self, h: str) -> ArtifactRef | None:
        with self.ro() as conn:
            row = conn.execute(sa.select(artifacts).where(artifacts.c.hash == h)).mappings().first()
        return ArtifactRef(row["hash"], row["kind"], row["size"], row["filename"]) if row else None

    def resolve_artifact_prefix(self, prefix: str) -> ArtifactRef | None:
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(artifacts.c.hash).where(artifacts.c.hash.like(f"{prefix}%")).limit(2)
            ).all()
        return self.artifact(rows[0][0]) if len(rows) == 1 else None

    def artifacts_by_hash(self, hashes: Iterable[str]) -> dict[str, ArtifactRef]:
        hs = list(set(hashes))
        if not hs:
            return {}
        with self.ro() as conn:
            rows = conn.execute(sa.select(artifacts).where(artifacts.c.hash.in_(hs))).mappings().all()
        return {r["hash"]: ArtifactRef(r["hash"], r["kind"], r["size"], r["filename"]) for r in rows}

    # -- experiments ---------------------------------------------------------------
    def create_experiment(self, **values: Any) -> None:
        with self.tx() as conn:
            conn.execute(experiments.insert().values(**values))

    def update_experiment(self, exp_id: str, **values: Any) -> None:
        with self.tx() as conn:
            conn.execute(experiments.update().where(experiments.c.id == exp_id).values(**values))

    def update_experiment_if(self, exp_id: str, when: str | Iterable[str], **values: Any) -> bool:
        """Compare-and-set: update only while the experiment's status is ``when``. True if it did."""
        allowed = [when] if isinstance(when, str) else list(when)
        with self.tx() as conn:
            res = conn.execute(
                experiments.update()
                .where(experiments.c.id == exp_id, experiments.c.status.in_(allowed))
                .values(**values)
            )
        return res.rowcount == 1

    def set_keep(self, exp_id: str, keep: bool) -> None:
        with self.tx() as conn:
            res = conn.execute(
                experiment_flags.update().where(experiment_flags.c.experiment_id == exp_id).values(keep=keep, updated_at=time.time())
            )
            if res.rowcount == 0:
                conn.execute(experiment_flags.insert().values(experiment_id=exp_id, keep=keep, updated_at=time.time()))

    def delete_experiments(
        self, ids: list[str], refuse_status: Iterable[str] = ("running",), reparent: dict[str, str | None] | None = None
    ) -> None:
        """Delete experiments (children first), their node runs and flags in one transaction.

        ``reparent`` moves surviving forks (child id -> new parent id) first. Executions,
        artifacts, metrics and lineage edges stay. Raises ``ValueError`` (and changes
        nothing) if any has a refused status or a fork outside ``ids`` that isn't reparented.
        """
        if not ids:
            return
        with self.tx() as conn:
            for child, parent in (reparent or {}).items():
                conn.execute(experiments.update().where(experiments.c.id == child).values(parent_id=parent))
            rows = conn.execute(sa.select(experiments.c.id, experiments.c.status).where(experiments.c.id.in_(ids))).all()
            if len(rows) != len(set(ids)):
                raise ValueError("some experiments no longer exist")
            busy = [i for i, st in rows if st in set(refuse_status)]
            if busy:
                raise ValueError(f"experiment {busy[0]} is running")
            outside = conn.execute(
                sa.select(experiments.c.id).where(experiments.c.parent_id.in_(ids), experiments.c.id.not_in(ids))
            ).first()
            if outside:
                raise ValueError(f"experiment {outside[0]} is forked from one being deleted")
            conn.execute(node_runs.delete().where(node_runs.c.experiment_id.in_(ids)))
            conn.execute(pipeline_reads.delete().where(pipeline_reads.c.experiment_id.in_(ids)))
            conn.execute(node_stages.delete().where(node_stages.c.experiment_id.in_(ids)))
            conn.execute(experiment_flags.delete().where(experiment_flags.c.experiment_id.in_(ids)))
            for exp_id in ids:  # ``ids`` lists children before parents (parent_id is a foreign key)
                conn.execute(experiments.delete().where(experiments.c.id == exp_id))

    def delete_draft(self, exp_id: str) -> None:
        """Delete a draft that has no children; raises ``ValueError`` otherwise."""
        with self.tx() as conn:
            kids = conn.execute(
                sa.select(sa.func.count()).select_from(experiments).where(experiments.c.parent_id == exp_id)
            ).scalar_one()
            if kids:
                raise ValueError(f"experiment {exp_id} has {kids} fork(s); discard those first")
            res = conn.execute(experiments.delete().where(experiments.c.id == exp_id, experiments.c.status == "draft"))
            if res.rowcount != 1:
                raise ValueError(f"experiment {exp_id} is not a draft")

    def experiment(self, key: str) -> dict | None:
        with self.ro() as conn:
            row = conn.execute(_EXPERIMENT_ROW.where(experiments.c.id == key)).mappings().first()
            if row is None:
                row = (
                    conn.execute(
                        _EXPERIMENT_ROW
                        .where(experiments.c.name == key)
                        .order_by(experiments.c.created_at.desc())
                    )
                    .mappings()
                    .first()
                )
        return dict(row, keep=bool(row["keep"])) if row else None

    def experiments(self) -> list[dict]:
        with self.ro() as conn:
            rows = conn.execute(_EXPERIMENT_ROW.order_by(experiments.c.created_at)).mappings().all()
        return [dict(r, keep=bool(r["keep"])) for r in rows]

    # -- node runs -----------------------------------------------------------------
    def clear_node_runs(self, exp_id: str) -> None:
        with self.tx() as conn:
            conn.execute(node_runs.delete().where(node_runs.c.experiment_id == exp_id))
            conn.execute(pipeline_reads.delete().where(pipeline_reads.c.experiment_id == exp_id))
            conn.execute(node_stages.delete().where(node_stages.c.experiment_id == exp_id))

    # -- stages ----------------------------------------------------------------------
    def set_node_stage(self, exp_id: str, node: str, path: list[dict]) -> None:
        with self.tx() as conn:
            res = conn.execute(node_stages.update().where(node_stages.c.experiment_id == exp_id, node_stages.c.node == node).values(path=path))
            if res.rowcount == 0:
                conn.execute(node_stages.insert().values(experiment_id=exp_id, node=node, path=path))

    def node_stages(self, exp_id: str) -> dict[str, list[dict]]:
        with self.ro() as conn:
            rows = conn.execute(sa.select(node_stages.c.node, node_stages.c.path).where(node_stages.c.experiment_id == exp_id)).all()
        return {n: p for n, p in rows}

    def pipeline_view(self, pipeline: str) -> dict:
        with self.ro() as conn:
            row = conn.execute(sa.select(pipeline_views.c.view).where(pipeline_views.c.pipeline == pipeline)).first()
        return row[0] if row else {"assign": {}, "styles": {}}

    def set_pipeline_view(self, pipeline: str, view: dict) -> None:
        with self.tx() as conn:
            res = conn.execute(pipeline_views.update().where(pipeline_views.c.pipeline == pipeline).values(view=view, updated_at=time.time()))
            if res.rowcount == 0:
                conn.execute(pipeline_views.insert().values(pipeline=pipeline, view=view, updated_at=time.time()))

    # -- pipeline reads and progress -------------------------------------------------
    def record_read(self, exp_id: str, seq: int, artifact_hash: str, after: int) -> None:
        with self.tx() as conn:
            conn.execute(pipeline_reads.insert().values(experiment_id=exp_id, seq=seq, artifact_hash=artifact_hash, after=after))

    def reads(self, exp_id: str) -> list[dict]:
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(pipeline_reads.c.artifact_hash, pipeline_reads.c.after)
                .where(pipeline_reads.c.experiment_id == exp_id)
                .order_by(pipeline_reads.c.seq)
            ).all()
        return [{"artifact": h, "after": a} for h, a in rows]

    def set_progress(self, exec_id: str, done: float, total: float | None, unit: str | None) -> None:
        values = dict(done=done, total=total, unit=unit, updated_at=time.time())
        with self.tx() as conn:
            res = conn.execute(execution_progress.update().where(execution_progress.c.execution_id == exec_id).values(**values))
            if res.rowcount == 0:
                conn.execute(execution_progress.insert().values(execution_id=exec_id, **values))

    def progress_for(self, exec_ids: Iterable[str]) -> dict[str, dict]:
        ids = [i for i in set(exec_ids) if i]
        if not ids:
            return {}
        with self.ro() as conn:
            rows = conn.execute(sa.select(execution_progress).where(execution_progress.c.execution_id.in_(ids))).mappings().all()
        return {r["execution_id"]: {k: r[k] for k in ("done", "total", "unit", "updated_at")} for r in rows}

    def change_tokens(self) -> tuple[str, dict[str, str]]:
        """A cheap fingerprint of the experiment list and of each experiment's live state.

        The UI server compares these between polls to push "this changed" events; a run in
        another process is only visible through the database.
        """
        with self.ro() as conn:
            exps = conn.execute(
                sa.select(experiments.c.id, experiments.c.status, experiments.c.name, experiments.c.parent_id,
                          experiments.c.started_at, experiments.c.finished_at)
            ).all()
            flags = conn.execute(sa.select(experiment_flags.c.experiment_id, experiment_flags.c.keep)).all()
            runs = conn.execute(
                sa.select(node_runs.c.experiment_id, node_runs.c.status, sa.func.count(),
                          sa.func.max(sa.func.coalesce(node_runs.c.finished_at, node_runs.c.started_at, node_runs.c.queued_at)))
                .group_by(node_runs.c.experiment_id, node_runs.c.status)
            ).all()
            prog = conn.execute(
                sa.select(node_runs.c.experiment_id, sa.func.max(execution_progress.c.updated_at))
                .join(execution_progress, execution_progress.c.execution_id == node_runs.c.execution_id)
                .group_by(node_runs.c.experiment_id)
            ).all()
        per: dict[str, list] = {}
        for row in exps:
            per.setdefault(row[0], []).append(tuple(row))
        for row in runs:
            per.setdefault(row[0], []).append(tuple(row))
        for row in prog:
            per.setdefault(row[0], []).append(tuple(row))
        tree = repr(sorted((tuple(r) for r in exps), key=repr)) + repr(sorted(map(tuple, flags)))
        return tree, {k: repr(sorted(v, key=repr)) for k, v in per.items()}

    def _upsert_node_run(self, conn: sa.Connection, exp_id: str, node: str, **values: Any) -> None:
        res = conn.execute(
            node_runs.update()
            .where(node_runs.c.experiment_id == exp_id, node_runs.c.node == node)
            .values(**values)
        )
        if res.rowcount == 0:
            seq = conn.execute(
                sa.select(sa.func.count()).select_from(node_runs).where(node_runs.c.experiment_id == exp_id)
            ).scalar_one()
            conn.execute(node_runs.insert().values(experiment_id=exp_id, node=node, seq=seq, **values))

    def upsert_node_run(self, exp_id: str, node: str, **values: Any) -> None:
        with self.tx() as conn:
            self._upsert_node_run(conn, exp_id, node, **values)

    def node_runs(self, exp_id: str) -> list[dict]:
        with self.ro() as conn:
            rows = (
                conn.execute(sa.select(node_runs).where(node_runs.c.experiment_id == exp_id).order_by(node_runs.c.seq))
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def node_runs_for_execution(self, exec_id: str) -> list[dict]:
        """Every experiment node run that resolved to (or pinned the outputs of) ``exec_id``."""
        with self.ro() as conn:
            rows = (
                conn.execute(
                    sa.select(node_runs.c.experiment_id, node_runs.c.node, node_runs.c.status, experiments.c.name)
                    .join(experiments, experiments.c.id == node_runs.c.experiment_id)
                    .where(node_runs.c.execution_id == exec_id)
                    .order_by(experiments.c.created_at)
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def experiments_using_artifact(self, h: str) -> list[dict]:
        """Experiments that took ``h`` as input, or whose node runs produced or read it.

        ``roles`` is ``[{"kind": "input"|"produced"|"read", "node", "name"}]``.
        """
        pat = f"%{h}%"  # hashes are unique hex, so a text match on the JSON is exact enough
        with self.ro() as conn:
            exps = conn.execute(
                sa.select(experiments.c.id, experiments.c.name, experiments.c.inputs).order_by(experiments.c.created_at)
            ).mappings().all()
            nrs = conn.execute(
                sa.select(node_runs.c.experiment_id, node_runs.c.node, node_runs.c.inputs, node_runs.c.outputs)
                .where(
                    sa.or_(
                        sa.cast(node_runs.c.outputs, sa.Text).like(pat),
                        sa.cast(node_runs.c.inputs, sa.Text).like(pat),
                    )
                )
                .order_by(node_runs.c.seq)
            ).mappings().all()
        roles: dict[str, list[dict]] = {}
        for e in exps:
            for name, v in (e["inputs"] or {}).items():
                if v == h:
                    roles.setdefault(e["id"], []).append({"kind": "input", "node": None, "name": name})
        for nr in nrs:
            for name, v in (nr["outputs"] or {}).items():
                if v == h:
                    roles.setdefault(nr["experiment_id"], []).append({"kind": "produced", "node": nr["node"], "name": name})
            for name, v in (nr["inputs"] or {}).items():
                if v == h:
                    roles.setdefault(nr["experiment_id"], []).append({"kind": "read", "node": nr["node"], "name": name})
        out = []
        for e in exps:  # creation order
            if e["id"] not in roles:
                continue
            rs = roles[e["id"]]
            out.append({"id": e["id"], "name": e["name"], "roles": rs})
        return out

    # -- bulk reads for storage accounting -----------------------------------------
    def all_node_runs(self) -> list[dict]:
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(
                    node_runs.c.experiment_id, node_runs.c.node, node_runs.c.status, node_runs.c.execution_id,
                    node_runs.c.outputs, node_runs.c.finished_at,
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    def all_artifacts(self) -> dict[str, dict]:
        with self.ro() as conn:
            rows = conn.execute(sa.select(artifacts)).mappings().all()
        return {r["hash"]: dict(r) for r in rows}

    def successful_executions(self) -> list[dict]:
        """Successful executions with their output hashes (``outputs``: name -> hash)."""
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(executions.c.id, executions.c.node, executions.c.experiment_id, executions.c.started_at, executions.c.finished_at)
                .where(executions.c.status == "success")
            ).mappings().all()
            outs = conn.execute(
                sa.select(execution_outputs.c.execution_id, execution_outputs.c.name, execution_outputs.c.artifact_hash)
                .join(executions, executions.c.id == execution_outputs.c.execution_id)
                .where(executions.c.status == "success")
                .order_by(execution_outputs.c.position)
            ).all()
        by_exec: dict[str, dict[str, str]] = {}
        for eid, name, h in outs:
            by_exec.setdefault(eid, {})[name] = h
        return [dict(r, outputs=by_exec.get(r["id"], {})) for r in rows]

    def running_executions(self) -> list[dict]:
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(executions.c.id, executions.c.signature, executions.c.started_at).where(executions.c.status == "running")
            ).mappings().all()
        return [dict(r) for r in rows]

    def active_leases(self) -> list[dict]:
        with self.ro() as conn:
            rows = conn.execute(sa.select(leases).where(leases.c.expires_at > time.time())).mappings().all()
        return [dict(r) for r in rows]

    # -- executions ----------------------------------------------------------------
    def latest_success_for_node(self, node: str) -> dict | None:
        """Most recent successful execution of ``node`` under any signature (for estimates)."""
        with self.ro() as conn:
            row = (
                conn.execute(
                    sa.select(executions)
                    .where(executions.c.node == node, executions.c.status == "success")
                    .order_by(executions.c.finished_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def find_success(self, signature: str) -> list[tuple[dict, dict[str, ArtifactRef]]]:
        """Successful executions for ``signature``, newest first, with their output refs."""
        with self.ro() as conn:
            rows = (
                conn.execute(
                    sa.select(executions)
                    .where(executions.c.signature == signature, executions.c.status == "success")
                    .order_by(executions.c.finished_at.desc())
                )
                .mappings()
                .all()
            )
            out = []
            for row in rows:
                outs = conn.execute(
                    sa.select(execution_outputs.c.name, artifacts)
                    .join(artifacts, artifacts.c.hash == execution_outputs.c.artifact_hash)
                    .where(execution_outputs.c.execution_id == row["id"])
                    .order_by(execution_outputs.c.position)
                ).mappings()
                refs = {o["name"]: ArtifactRef(o["hash"], o["kind"], o["size"], o["filename"]) for o in outs}
                out.append((dict(row), refs))
        return out

    def start_execution(
        self, *, signature: str, node: str, spec: dict, cacheable: bool, experiment_id: str, task_run_id: str | None
    ) -> str:
        exec_id = _new_id()
        with self.tx() as conn:
            conn.execute(
                executions.insert().values(
                    id=exec_id,
                    signature=signature,
                    node=node,
                    spec=spec,
                    cacheable=cacheable,
                    status="running",
                    experiment_id=experiment_id,
                    task_run_id=task_run_id,
                    started_at=time.time(),
                )
            )
        return exec_id

    def publish(
        self,
        exec_id: str,
        *,
        experiment_id: str,
        node: str,
        inputs: dict[str, ArtifactRef],
        outputs: dict[str, ArtifactRef],
        metric_values: list[tuple[str, int, float]],
        node_run: dict[str, Any],
        lease: tuple[str, str] | None,
    ) -> None:
        """Commit a successful execution, its lineage and the node run in one transaction."""
        with self.tx() as conn:
            self._register_artifacts(conn, [*inputs.values(), *outputs.values()])
            for name, ref in inputs.items():
                conn.execute(execution_inputs.insert().values(execution_id=exec_id, name=name, artifact_hash=ref.hash))
            for pos, (name, ref) in enumerate(outputs.items()):
                conn.execute(
                    execution_outputs.insert().values(
                        execution_id=exec_id, name=name, position=pos, artifact_hash=ref.hash
                    )
                )
            latest: dict[tuple[str, int], float] = {}
            for key, step, value in metric_values:
                latest[(key, step)] = value
            for (key, step), value in latest.items():
                conn.execute(metrics.insert().values(execution_id=exec_id, key=key, step=step, value=value))
            conn.execute(
                executions.update()
                .where(executions.c.id == exec_id)
                .values(status="success", finished_at=time.time())
            )
            self._upsert_node_run(conn, experiment_id, node, **node_run)
            if lease:
                conn.execute(leases.delete().where(leases.c.signature == lease[0], leases.c.owner == lease[1]))

    def record_source(self, exec_id: str, snap: dict[str, Any]) -> None:
        """Link an execution to its node function's source snapshot (stored once per text)."""
        with self.tx() as conn:
            exists = conn.execute(sa.select(code_snapshots.c.hash).where(code_snapshots.c.hash == snap["hash"])).first()
            if not exists:
                conn.execute(code_snapshots.insert().values(created_at=time.time(), **snap))
            conn.execute(execution_sources.insert().values(execution_id=exec_id, source_hash=snap["hash"]))

    def save_snapshot(self, snap: dict[str, Any]) -> None:
        """Store a source snapshot (once per text) without linking it to an execution."""
        with self.tx() as conn:
            exists = conn.execute(sa.select(code_snapshots.c.hash).where(code_snapshots.c.hash == snap["hash"])).first()
            if not exists:
                conn.execute(code_snapshots.insert().values(created_at=time.time(), **snap))

    def snapshot(self, source_hash: str) -> dict | None:
        with self.ro() as conn:
            row = conn.execute(sa.select(code_snapshots).where(code_snapshots.c.hash == source_hash)).mappings().first()
        return dict(row) if row else None

    def source_for_execution(self, exec_id: str) -> dict | None:
        """Snapshot metadata (no text) for an execution, or None for runs recorded before snapshots."""
        with self.ro() as conn:
            row = (
                conn.execute(
                    sa.select(code_snapshots.c.hash, code_snapshots.c.file, code_snapshots.c.line, code_snapshots.c.module, code_snapshots.c.qualname)
                    .join(execution_sources, execution_sources.c.source_hash == code_snapshots.c.hash)
                    .where(execution_sources.c.execution_id == exec_id)
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def fail_execution(self, exec_id: str, error: str) -> None:
        with self.tx() as conn:
            conn.execute(
                executions.update()
                .where(executions.c.id == exec_id)
                .values(status="failed", finished_at=time.time(), error=error)
            )

    def execution(self, exec_id: str) -> dict | None:
        with self.ro() as conn:
            row = conn.execute(sa.select(executions).where(executions.c.id == exec_id)).mappings().first()
            if not row:
                return None
            ins = conn.execute(
                sa.select(execution_inputs.c.name, execution_inputs.c.artifact_hash).where(
                    execution_inputs.c.execution_id == exec_id
                )
            ).all()
            outs = conn.execute(
                sa.select(execution_outputs.c.name, execution_outputs.c.artifact_hash)
                .where(execution_outputs.c.execution_id == exec_id)
                .order_by(execution_outputs.c.position)
            ).all()
            ms = conn.execute(
                sa.select(metrics.c.key, metrics.c.step, metrics.c.value)
                .where(metrics.c.execution_id == exec_id)
                .order_by(metrics.c.key, metrics.c.step)
            ).all()
        out = dict(row)
        out["inputs"] = {n: h for n, h in ins}
        out["outputs"] = {n: h for n, h in outs}
        out["metrics"] = [{"key": k, "step": s, "value": v} for k, s, v in ms]
        return out

    def executions_brief(self, exec_ids: Iterable[str]) -> dict[str, dict]:
        """``{id: {started_at, finished_at, experiment_id}}`` in one query (for DAGs with many nodes)."""
        ids = [i for i in set(exec_ids) if i]
        if not ids:
            return {}
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(executions.c.id, executions.c.started_at, executions.c.finished_at, executions.c.experiment_id).where(executions.c.id.in_(ids))
            ).mappings().all()
        return {r["id"]: dict(r) for r in rows}

    def metrics_for(self, exec_ids: Iterable[str]) -> dict[str, dict[str, float]]:
        """Last-step value of every metric, per execution."""
        ids = [i for i in set(exec_ids) if i]
        if not ids:
            return {}
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(metrics.c.execution_id, metrics.c.key, metrics.c.step, metrics.c.value)
                .where(metrics.c.execution_id.in_(ids))
                .order_by(metrics.c.step)
            ).all()
        out: dict[str, dict[str, float]] = {}
        for eid, key, _, value in rows:
            out.setdefault(eid, {})[key] = value
        return out

    def producer_of(self, h: str) -> str | None:
        with self.ro() as conn:
            row = conn.execute(
                sa.select(execution_outputs.c.execution_id)
                .join(executions, executions.c.id == execution_outputs.c.execution_id)
                .where(execution_outputs.c.artifact_hash == h, executions.c.status == "success")
                .order_by(executions.c.finished_at)
                .limit(1)
            ).first()
        return row[0] if row else None

    # -- leases --------------------------------------------------------------------
    def acquire_lease(self, signature: str, owner: str, ttl: float) -> bool:
        now = time.time()
        with self.tx() as conn:
            conn.execute(leases.delete().where(leases.c.signature == signature, leases.c.expires_at < now))
        try:
            with self.tx() as conn:
                conn.execute(leases.insert().values(signature=signature, owner=owner, expires_at=now + ttl))
            return True
        except IntegrityError:
            return False

    def renew_lease(self, signature: str, owner: str, ttl: float) -> bool:
        with self.tx() as conn:
            res = conn.execute(
                leases.update()
                .where(leases.c.signature == signature, leases.c.owner == owner)
                .values(expires_at=time.time() + ttl)
            )
        return res.rowcount == 1

    def release_lease(self, signature: str, owner: str) -> None:
        with self.tx() as conn:
            conn.execute(leases.delete().where(leases.c.signature == signature, leases.c.owner == owner))

    # -- lineage -------------------------------------------------------------------
    _ANCESTORS = sa.text(
        """
        WITH RECURSIVE ex(id) AS (
            SELECT eo.execution_id FROM execution_outputs eo
              JOIN executions e ON e.id = eo.execution_id
             WHERE eo.artifact_hash = :h AND e.status = 'success'
            UNION
            SELECT eo.execution_id FROM ex
              JOIN execution_inputs ei ON ei.execution_id = ex.id
              JOIN execution_outputs eo ON eo.artifact_hash = ei.artifact_hash
              JOIN executions e ON e.id = eo.execution_id
             WHERE e.status = 'success'
        )
        SELECT id FROM ex
        """
    )
    _DESCENDANTS = sa.text(
        """
        WITH RECURSIVE ex(id) AS (
            SELECT ei.execution_id FROM execution_inputs ei
              JOIN executions e ON e.id = ei.execution_id
             WHERE ei.artifact_hash = :h AND e.status = 'success'
            UNION
            SELECT ei.execution_id FROM ex
              JOIN execution_outputs eo ON eo.execution_id = ex.id
              JOIN execution_inputs ei ON ei.artifact_hash = eo.artifact_hash
              JOIN executions e ON e.id = ei.execution_id
             WHERE e.status = 'success'
        )
        SELECT id FROM ex
        """
    )

    def ancestor_executions(self, h: str) -> list[str]:
        with self.ro() as conn:
            return [r[0] for r in conn.execute(self._ANCESTORS, {"h": h})]

    def descendant_executions(self, h: str) -> list[str]:
        with self.ro() as conn:
            return [r[0] for r in conn.execute(self._DESCENDANTS, {"h": h})]
