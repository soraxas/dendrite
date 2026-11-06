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

import re
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from .store import ArtifactRef

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class NameConflict(ValueError):
    """A name points elsewhere: ``code`` is ``name_taken`` (move it explicitly) or ``name_moved``."""

    def __init__(self, message: str, code: str = "name_taken"):
        super().__init__(f"{code}: {message}")
        self.code = code


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(
            f"invalid name {name!r}: use letters, digits, '.', '_' or '-', starting with a letter or digit"
        )
    return name


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

# Console output a step wrote (stdout, stderr, logging), in chunks: display only. ``seq`` -1 is
# the rolling tail of a log past its size limit, overwritten while it runs.
execution_logs = sa.Table(
    "execution_logs",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("seq", sa.Integer, primary_key=True),
    sa.Column("stream", sa.String(10), nullable=False),  # stdout | stderr | log | marker
    sa.Column("t", sa.Float, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
)

# A finished log too big for the database, stored as one file in the artifact store.
execution_log_blobs = sa.Table(
    "execution_log_blobs",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("hash", sa.String(64), nullable=False, index=True),
    sa.Column("size", sa.BigInteger, nullable=False),
)

# Intermediate images/media a step logged (`log_image`, `log_media`): artifacts, never outputs.
execution_samples = sa.Table(
    "execution_samples",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("title", sa.String(200), primary_key=True),
    sa.Column("series", sa.String(200), primary_key=True),
    sa.Column("step", sa.Integer, primary_key=True),
    sa.Column("hash", sa.String(64), nullable=False, index=True),
    sa.Column("t", sa.Float, nullable=False),
)

# Where an execution ran (git, packages, system), stored once per distinct snapshot.
environments = sa.Table(
    "environments",
    meta,
    sa.Column("hash", sa.String(64), primary_key=True),
    sa.Column("data", sa.JSON, nullable=False),
)
execution_environments = sa.Table(
    "execution_environments",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("env_hash", sa.String(64), nullable=False),
)

# When each metric point was logged (for a wall-time axis); points written without one have none.
metric_times = sa.Table(
    "metric_times",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("key", sa.String(200), primary_key=True),
    sa.Column("step", sa.Integer, primary_key=True),
    sa.Column("t", sa.Float, nullable=False),
)

# When an execution last wrote telemetry, so pushed updates notice live logs and metrics.
execution_activity = sa.Table(
    "execution_activity",
    meta,
    sa.Column("execution_id", sa.String(32), sa.ForeignKey("executions.id"), primary_key=True),
    sa.Column("updated_at", sa.Float, nullable=False),
)

# Human-readable names for artifacts (`@best-splat`): metadata only, never in a signature.
artifact_names = sa.Table(
    "artifact_names",
    meta,
    sa.Column("name", sa.String(100), primary_key=True),
    sa.Column("hash", sa.String(64), nullable=False, index=True),
    sa.Column("experiment_id", sa.String(32)),  # where it was named from, if anywhere
    sa.Column("node", sa.String(200)),
    sa.Column("output", sa.String(200)),
    sa.Column("note", sa.Text),
    sa.Column("set_by", sa.String(200)),
    sa.Column("set_at", sa.Float, nullable=False),
)

# Earlier targets of names that were moved or removed (append-only).
artifact_name_history = sa.Table(
    "artifact_name_history",
    meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String(100), nullable=False, index=True),
    sa.Column("hash", sa.String(64), nullable=False),
    sa.Column("experiment_id", sa.String(32)),
    sa.Column("node", sa.String(200)),
    sa.Column("output", sa.String(200)),
    sa.Column("note", sa.Text),
    sa.Column("set_by", sa.String(200)),
    sa.Column("set_at", sa.Float, nullable=False),
    sa.Column("ended_by", sa.String(200)),
    sa.Column("ended_at", sa.Float, nullable=False),
    sa.Column("action", sa.String(10), nullable=False),  # moved | removed
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
    def __init__(self, url: str, **pool: Any):
        """``pool`` passes pool settings (``pool_size``, ``max_overflow``) to a non-SQLite engine."""
        self.url = url
        if url.startswith("sqlite"):
            self.engine = _sqlite_engine(url, immediate=True)
            self.reader = _sqlite_engine(url, immediate=False)
        else:
            self.engine = sa.create_engine(url, pool_pre_ping=True, **pool)
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
            rows = conn.execute(sa.select(artifacts.c.hash).where(artifacts.c.hash.like(f"{prefix}%")).limit(2)).all()
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
                experiment_flags.update()
                .where(experiment_flags.c.experiment_id == exp_id)
                .values(keep=keep, updated_at=time.time())
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
            rows = conn.execute(
                sa.select(experiments.c.id, experiments.c.status).where(experiments.c.id.in_(ids))
            ).all()
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
                        _EXPERIMENT_ROW.where(experiments.c.name == key).order_by(experiments.c.created_at.desc())
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
            res = conn.execute(
                node_stages.update()
                .where(node_stages.c.experiment_id == exp_id, node_stages.c.node == node)
                .values(path=path)
            )
            if res.rowcount == 0:
                conn.execute(node_stages.insert().values(experiment_id=exp_id, node=node, path=path))

    def node_stages(self, exp_id: str) -> dict[str, list[dict]]:
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(node_stages.c.node, node_stages.c.path).where(node_stages.c.experiment_id == exp_id)
            ).all()
        return {n: p for n, p in rows}

    def pipeline_view(self, pipeline: str) -> dict:
        with self.ro() as conn:
            row = conn.execute(sa.select(pipeline_views.c.view).where(pipeline_views.c.pipeline == pipeline)).first()
        return row[0] if row else {"assign": {}, "styles": {}}

    def set_pipeline_view(self, pipeline: str, view: dict) -> None:
        with self.tx() as conn:
            res = conn.execute(
                pipeline_views.update()
                .where(pipeline_views.c.pipeline == pipeline)
                .values(view=view, updated_at=time.time())
            )
            if res.rowcount == 0:
                conn.execute(pipeline_views.insert().values(pipeline=pipeline, view=view, updated_at=time.time()))

    # -- pipeline reads and progress -------------------------------------------------
    def record_read(self, exp_id: str, seq: int, artifact_hash: str, after: int) -> None:
        with self.tx() as conn:
            conn.execute(
                pipeline_reads.insert().values(experiment_id=exp_id, seq=seq, artifact_hash=artifact_hash, after=after)
            )

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
            res = conn.execute(
                execution_progress.update().where(execution_progress.c.execution_id == exec_id).values(**values)
            )
            if res.rowcount == 0:
                conn.execute(execution_progress.insert().values(execution_id=exec_id, **values))

    def progress_for(self, exec_ids: Iterable[str]) -> dict[str, dict]:
        ids = [i for i in set(exec_ids) if i]
        if not ids:
            return {}
        with self.ro() as conn:
            rows = (
                conn.execute(sa.select(execution_progress).where(execution_progress.c.execution_id.in_(ids)))
                .mappings()
                .all()
            )
        return {r["execution_id"]: {k: r[k] for k in ("done", "total", "unit", "updated_at")} for r in rows}

    def change_tokens(self) -> tuple[str, dict[str, str]]:
        """A cheap fingerprint of the experiment list and of each experiment's live state.

        The UI server compares these between polls to push "this changed" events; a run in
        another process is only visible through the database.
        """
        with self.ro() as conn:
            exps = conn.execute(
                sa.select(
                    experiments.c.id,
                    experiments.c.status,
                    experiments.c.name,
                    experiments.c.parent_id,
                    experiments.c.started_at,
                    experiments.c.finished_at,
                )
            ).all()
            flags = conn.execute(sa.select(experiment_flags.c.experiment_id, experiment_flags.c.keep)).all()
            named = conn.execute(
                sa.select(sa.func.count(), sa.func.max(artifact_names.c.set_at)).select_from(artifact_names)
            ).first()
            ended = conn.execute(sa.select(sa.func.max(artifact_name_history.c.id))).scalar()
            runs = conn.execute(
                sa.select(
                    node_runs.c.experiment_id,
                    node_runs.c.status,
                    sa.func.count(),
                    sa.func.max(
                        sa.func.coalesce(node_runs.c.finished_at, node_runs.c.started_at, node_runs.c.queued_at)
                    ),
                ).group_by(node_runs.c.experiment_id, node_runs.c.status)
            ).all()
            prog = conn.execute(
                sa.select(node_runs.c.experiment_id, sa.func.max(execution_progress.c.updated_at))
                .join(execution_progress, execution_progress.c.execution_id == node_runs.c.execution_id)
                .group_by(node_runs.c.experiment_id)
            ).all()
            act = conn.execute(
                sa.select(node_runs.c.experiment_id, sa.func.max(execution_activity.c.updated_at))
                .join(execution_activity, execution_activity.c.execution_id == node_runs.c.execution_id)
                .group_by(node_runs.c.experiment_id)
            ).all()
        per: dict[str, list] = {}
        for row in exps:
            per.setdefault(row[0], []).append(tuple(row))
        for row in runs:
            per.setdefault(row[0], []).append(tuple(row))
        for row in (*prog, *act):
            per.setdefault(row[0], []).append(tuple(row))
        tree = (
            repr(sorted((tuple(r) for r in exps), key=repr))
            + repr(sorted(map(tuple, flags)))
            + repr((tuple(named or ()), ended))
        )
        return tree, {k: repr(sorted(v, key=repr)) for k, v in per.items()}

    def _upsert_node_run(self, conn: sa.Connection, exp_id: str, node: str, **values: Any) -> None:
        res = conn.execute(
            node_runs.update().where(node_runs.c.experiment_id == exp_id, node_runs.c.node == node).values(**values)
        )
        if res.rowcount == 0:
            seq = conn.execute(
                sa.select(sa.func.count()).select_from(node_runs).where(node_runs.c.experiment_id == exp_id)
            ).scalar_one()
            try:
                with conn.begin_nested():
                    conn.execute(node_runs.insert().values(experiment_id=exp_id, node=node, seq=seq, **values))
            except IntegrityError:  # inserted concurrently since the update above: update it instead
                conn.execute(
                    node_runs.update()
                    .where(node_runs.c.experiment_id == exp_id, node_runs.c.node == node)
                    .values(**values)
                )

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
            exps = (
                conn.execute(
                    sa.select(experiments.c.id, experiments.c.name, experiments.c.inputs).order_by(
                        experiments.c.created_at
                    )
                )
                .mappings()
                .all()
            )
            nrs = (
                conn.execute(
                    sa.select(node_runs.c.experiment_id, node_runs.c.node, node_runs.c.inputs, node_runs.c.outputs)
                    .where(
                        sa.or_(
                            sa.cast(node_runs.c.outputs, sa.Text).like(pat),
                            sa.cast(node_runs.c.inputs, sa.Text).like(pat),
                        )
                    )
                    .order_by(node_runs.c.seq)
                )
                .mappings()
                .all()
            )
        roles: dict[str, list[dict]] = {}
        for e in exps:
            for name, v in (e["inputs"] or {}).items():
                if v == h:
                    roles.setdefault(e["id"], []).append({"kind": "input", "node": None, "name": name})
        for nr in nrs:
            for name, v in (nr["outputs"] or {}).items():
                if v == h:
                    roles.setdefault(nr["experiment_id"], []).append(
                        {"kind": "produced", "node": nr["node"], "name": name}
                    )
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
            rows = (
                conn.execute(
                    sa.select(
                        node_runs.c.experiment_id,
                        node_runs.c.node,
                        node_runs.c.status,
                        node_runs.c.execution_id,
                        node_runs.c.outputs,
                        node_runs.c.finished_at,
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def all_artifacts(self) -> dict[str, dict]:
        with self.ro() as conn:
            rows = conn.execute(sa.select(artifacts)).mappings().all()
        return {r["hash"]: dict(r) for r in rows}

    def successful_executions(self) -> list[dict]:
        """Successful executions with their output hashes (``outputs``: name -> hash)."""
        with self.ro() as conn:
            rows = (
                conn.execute(
                    sa.select(
                        executions.c.id,
                        executions.c.node,
                        executions.c.experiment_id,
                        executions.c.started_at,
                        executions.c.finished_at,
                    ).where(executions.c.status == "success")
                )
                .mappings()
                .all()
            )
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
            rows = (
                conn.execute(
                    sa.select(executions.c.id, executions.c.signature, executions.c.started_at).where(
                        executions.c.status == "running"
                    )
                )
                .mappings()
                .all()
            )
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
            self._write_metrics(conn, exec_id, metric_values)
            conn.execute(
                executions.update().where(executions.c.id == exec_id).values(status="success", finished_at=time.time())
            )
            self._upsert_node_run(conn, experiment_id, node, **node_run)
            if lease:
                conn.execute(leases.delete().where(leases.c.signature == lease[0], leases.c.owner == lease[1]))

    def record_source(self, exec_id: str, snap: dict[str, Any]) -> None:
        """Link an execution to its node function's source snapshot (stored once per text)."""
        with self.tx() as conn:
            self._insert_snapshot(conn, snap)
            conn.execute(execution_sources.insert().values(execution_id=exec_id, source_hash=snap["hash"]))

    @staticmethod
    def _insert_snapshot(conn: sa.Connection, snap: dict[str, Any]) -> None:
        # Parallel executions of one step store the same text at once; the loser's insert is a no-op.
        if conn.execute(sa.select(code_snapshots.c.hash).where(code_snapshots.c.hash == snap["hash"])).first():
            return
        try:
            with conn.begin_nested():
                conn.execute(code_snapshots.insert().values(created_at=time.time(), **snap))
        except IntegrityError:
            pass

    def save_snapshot(self, snap: dict[str, Any]) -> None:
        """Store a source snapshot (once per text) without linking it to an execution."""
        with self.tx() as conn:
            self._insert_snapshot(conn, snap)

    def snapshot(self, source_hash: str) -> dict | None:
        with self.ro() as conn:
            row = conn.execute(sa.select(code_snapshots).where(code_snapshots.c.hash == source_hash)).mappings().first()
        return dict(row) if row else None

    def source_for_execution(self, exec_id: str) -> dict | None:
        """Snapshot metadata (no text) for an execution, or None for runs recorded before snapshots."""
        with self.ro() as conn:
            row = (
                conn.execute(
                    sa.select(
                        code_snapshots.c.hash,
                        code_snapshots.c.file,
                        code_snapshots.c.line,
                        code_snapshots.c.module,
                        code_snapshots.c.qualname,
                    )
                    .join(execution_sources, execution_sources.c.source_hash == code_snapshots.c.hash)
                    .where(execution_sources.c.execution_id == exec_id)
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    # -- artifact names (metadata only) ------------------------------------------------
    _NAME_COLS = ("name", "hash", "experiment_id", "node", "output", "note", "set_by", "set_at")

    def set_name(
        self,
        name: str,
        hash: str,
        *,
        experiment_id: str | None = None,
        node: str | None = None,
        output: str | None = None,
        note: str | None = None,
        set_by: str | None = None,
        move: bool = False,
        expect: str | None = None,
    ) -> dict:
        """Point ``name`` at ``hash``. Moving an existing name needs ``move``; ``expect`` is a
        compare-and-set on its current target. The outgoing target goes to the history."""
        check_name(name)
        now = time.time()
        with self.tx() as conn:
            if conn.execute(sa.select(artifacts.c.hash).where(artifacts.c.hash == hash)).first() is None:
                raise KeyError(f"unknown artifact {hash[:12]}")
            cur = conn.execute(sa.select(artifact_names).where(artifact_names.c.name == name)).mappings().first()
            if expect is not None and (cur is None or cur["hash"] != expect):
                raise NameConflict(f"{name!r} no longer points at {expect[:12]}", "name_moved")
            values = dict(
                hash=hash, experiment_id=experiment_id, node=node, output=output, note=note, set_by=set_by, set_at=now
            )
            if cur is None:
                try:
                    with conn.begin_nested():
                        conn.execute(artifact_names.insert().values(name=name, **values))
                except IntegrityError:
                    raise NameConflict(f"{name!r} was just set by someone else", "name_moved") from None
            elif cur["hash"] == hash:
                if note is not None:
                    conn.execute(artifact_names.update().where(artifact_names.c.name == name).values(note=note))
            else:
                if not move:
                    raise NameConflict(f"{name!r} already points at {cur['hash'][:12]}; move it explicitly")
                self._end_name(conn, dict(cur), set_by, now, "moved")
                res = conn.execute(
                    artifact_names.update()
                    .where(artifact_names.c.name == name, artifact_names.c.hash == cur["hash"])
                    .values(**values)
                )
                if res.rowcount != 1:
                    raise NameConflict(f"{name!r} was moved concurrently", "name_moved")
            row = conn.execute(sa.select(artifact_names).where(artifact_names.c.name == name)).mappings().first()
        return dict(row)

    @staticmethod
    def _end_name(conn: sa.Connection, cur: dict, by: str | None, now: float, action: str) -> None:
        conn.execute(
            artifact_name_history.insert().values(
                **{k: cur[k] for k in Metadata._NAME_COLS}, ended_by=by, ended_at=now, action=action
            )
        )

    def remove_name(self, name: str, *, expect: str | None = None, by: str | None = None) -> bool:
        with self.tx() as conn:
            cur = conn.execute(sa.select(artifact_names).where(artifact_names.c.name == name)).mappings().first()
            if cur is None:
                return False
            if expect is not None and cur["hash"] != expect:
                raise NameConflict(f"{name!r} no longer points at {expect[:12]}", "name_moved")
            self._end_name(conn, dict(cur), by, time.time(), "removed")
            conn.execute(
                artifact_names.delete().where(artifact_names.c.name == name, artifact_names.c.hash == cur["hash"])
            )
        return True

    def name(self, name: str) -> dict | None:
        with self.ro() as conn:
            row = conn.execute(sa.select(artifact_names).where(artifact_names.c.name == name)).mappings().first()
        return dict(row) if row else None

    def names(self) -> list[dict]:
        """Every current name with its artifact's kind, size and filename, most recent first."""
        with self.ro() as conn:
            rows = (
                conn.execute(
                    sa.select(artifact_names, artifacts.c.kind, artifacts.c.size, artifacts.c.filename)
                    .join(artifacts, artifacts.c.hash == artifact_names.c.hash, isouter=True)
                    .order_by(artifact_names.c.set_at.desc())
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def names_for(self, hashes: Iterable[str]) -> dict[str, list[str]]:
        hs = [h for h in set(hashes) if h]
        out: dict[str, list[str]] = {}
        if not hs:
            return out
        with self.ro() as conn:
            for i in range(0, len(hs), 500):
                rows = conn.execute(
                    sa.select(artifact_names.c.hash, artifact_names.c.name)
                    .where(artifact_names.c.hash.in_(hs[i : i + 500]))
                    .order_by(artifact_names.c.name)
                ).all()
                for h, n in rows:
                    out.setdefault(h, []).append(n)
        return out

    def name_history(self, name: str) -> list[dict]:
        with self.ro() as conn:
            rows = (
                conn.execute(
                    sa.select(artifact_name_history)
                    .where(artifact_name_history.c.name == name)
                    .order_by(artifact_name_history.c.ended_at.desc(), artifact_name_history.c.id.desc())
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def named_hashes(self) -> set[str]:
        with self.ro() as conn:
            return {r[0] for r in conn.execute(sa.select(artifact_names.c.hash))}

    # -- telemetry (display only: never part of a signature) ---------------------------
    @staticmethod
    def _write_metrics(conn: sa.Connection, exec_id: str, values: Iterable[tuple[str, int, float]]) -> None:
        """Upsert metric points; a repeated (key, step) keeps the last value."""
        latest: dict[tuple[str, int], float] = {}
        for key, step, value in values:
            latest[(key, step)] = value
        by_key: dict[str, list[int]] = {}
        for key, step in latest:
            by_key.setdefault(key, []).append(step)
        for key, steps in by_key.items():
            for i in range(0, len(steps), 500):
                conn.execute(
                    metrics.delete().where(
                        metrics.c.execution_id == exec_id, metrics.c.key == key, metrics.c.step.in_(steps[i : i + 500])
                    )
                )
        if latest:
            conn.execute(
                metrics.insert(),
                [{"execution_id": exec_id, "key": k, "step": s, "value": v} for (k, s), v in latest.items()],
            )

    @staticmethod
    def _touch(conn: sa.Connection, exec_id: str) -> None:
        now = time.time()
        res = conn.execute(
            execution_activity.update().where(execution_activity.c.execution_id == exec_id).values(updated_at=now)
        )
        if res.rowcount == 0:
            conn.execute(execution_activity.insert().values(execution_id=exec_id, updated_at=now))

    def write_telemetry(
        self,
        exec_id: str,
        *,
        logs: list[dict] | None = None,
        tail: str | None = None,
        metric_values: list[tuple[str, int, float]] | None = None,
        times: list[tuple[str, int, float]] | None = None,
    ) -> None:
        """Append log chunks (``{seq, stream, t, text}``), replace the rolling tail, upsert metrics
        and when each was logged (``(key, step, t)``)."""
        if not (logs or tail is not None or metric_values):
            return
        with self.tx() as conn:
            if logs:
                conn.execute(execution_logs.insert(), [{"execution_id": exec_id, **c} for c in logs])
            if tail is not None:
                conn.execute(
                    execution_logs.delete().where(execution_logs.c.execution_id == exec_id, execution_logs.c.seq == -1)
                )
                if tail:
                    conn.execute(
                        execution_logs.insert().values(
                            execution_id=exec_id, seq=-1, stream="tail", t=time.time(), text=tail
                        )
                    )
            if metric_values:
                self._write_metrics(conn, exec_id, metric_values)
            if times:
                latest = {(k, st): t for k, st, t in times}
                by_key: dict[str, list[int]] = {}
                for k, st in latest:
                    by_key.setdefault(k, []).append(st)
                for k, steps in by_key.items():
                    for i in range(0, len(steps), 500):
                        conn.execute(
                            metric_times.delete().where(
                                metric_times.c.execution_id == exec_id,
                                metric_times.c.key == k,
                                metric_times.c.step.in_(steps[i : i + 500]),
                            )
                        )
                conn.execute(
                    metric_times.insert(),
                    [{"execution_id": exec_id, "key": k, "step": st, "t": t} for (k, st), t in latest.items()],
                )
            self._touch(conn, exec_id)

    def attach_log(self, exec_id: str, ref: ArtifactRef) -> None:
        """Point an execution's log at a stored file, dropping the chunks it replaces."""
        with self.tx() as conn:
            self._register_artifacts(conn, [ref])
            conn.execute(execution_log_blobs.delete().where(execution_log_blobs.c.execution_id == exec_id))
            conn.execute(execution_log_blobs.insert().values(execution_id=exec_id, hash=ref.hash, size=ref.size))
            conn.execute(execution_logs.delete().where(execution_logs.c.execution_id == exec_id))
            self._touch(conn, exec_id)

    def log_blob(self, exec_id: str) -> dict | None:
        with self.ro() as conn:
            row = (
                conn.execute(sa.select(execution_log_blobs).where(execution_log_blobs.c.execution_id == exec_id))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def logs(self, exec_id: str, after: int = -1, limit: int = 2000) -> list[dict]:
        """Log chunks with ``seq > after`` in order; the rolling tail (seq -1) comes last, if any."""
        with self.ro() as conn:
            rows = (
                conn.execute(
                    sa.select(execution_logs.c.seq, execution_logs.c.stream, execution_logs.c.t, execution_logs.c.text)
                    .where(execution_logs.c.execution_id == exec_id, execution_logs.c.seq > after)
                    .order_by(execution_logs.c.seq)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            tail = (
                conn.execute(
                    sa.select(
                        execution_logs.c.seq, execution_logs.c.stream, execution_logs.c.t, execution_logs.c.text
                    ).where(execution_logs.c.execution_id == exec_id, execution_logs.c.seq == -1)
                )
                .mappings()
                .first()
            )
        out = [dict(r) for r in rows]
        if tail and len(out) < limit:
            out.append(dict(tail))
        return out

    def scalars(self, exec_ids: Iterable[str]) -> dict[str, list[dict]]:
        """Every metric point per execution: ``{exec_id: [{key, step, value, t}]}`` ordered by key, step.
        ``t`` is when it was logged, or ``None`` for points recorded without a time."""
        ids = [i for i in set(exec_ids) if i]
        if not ids:
            return {}
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(metrics.c.execution_id, metrics.c.key, metrics.c.step, metrics.c.value, metric_times.c.t)
                .join(
                    metric_times,
                    sa.and_(
                        metric_times.c.execution_id == metrics.c.execution_id,
                        metric_times.c.key == metrics.c.key,
                        metric_times.c.step == metrics.c.step,
                    ),
                    isouter=True,
                )
                .where(metrics.c.execution_id.in_(ids))
                .order_by(metrics.c.key, metrics.c.step)
            ).all()
        out: dict[str, list[dict]] = {i: [] for i in ids}
        for eid, key, step, value, t in rows:
            out[eid].append({"key": key, "step": step, "value": value, "t": t})
        return out

    def record_sample(
        self, exec_id: str, *, title: str, series: str, step: int, ref: ArtifactRef, history: int
    ) -> None:
        """Record a logged sample (its blob already stored) and drop steps beyond ``history``."""
        with self.tx() as conn:
            self._register_artifacts(conn, [ref])
            key = (
                execution_samples.c.execution_id == exec_id,
                execution_samples.c.title == title,
                execution_samples.c.series == series,
            )
            conn.execute(execution_samples.delete().where(*key, execution_samples.c.step == step))
            conn.execute(
                execution_samples.insert().values(
                    execution_id=exec_id, title=title, series=series, step=step, hash=ref.hash, t=time.time()
                )
            )
            if history > 0:
                keep = (
                    sa.select(execution_samples.c.step)
                    .where(*key)
                    .order_by(execution_samples.c.step.desc())
                    .limit(history)
                    .subquery()
                )
                conn.execute(execution_samples.delete().where(*key, execution_samples.c.step.not_in(sa.select(keep))))
            self._touch(conn, exec_id)

    def samples(self, exec_id: str) -> list[dict]:
        with self.ro() as conn:
            rows = (
                conn.execute(
                    sa.select(
                        execution_samples.c.title,
                        execution_samples.c.series,
                        execution_samples.c.step,
                        execution_samples.c.hash,
                        execution_samples.c.t,
                        artifacts.c.kind,
                        artifacts.c.size,
                        artifacts.c.filename,
                    )
                    .join(artifacts, artifacts.c.hash == execution_samples.c.hash, isouter=True)
                    .where(execution_samples.c.execution_id == exec_id)
                    .order_by(execution_samples.c.title, execution_samples.c.series, execution_samples.c.step)
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def attachment_hashes(self) -> list[tuple[str, str, str | None, str, str]]:
        """``(execution, hash, experiment that ran it, node, "sample" | "log")`` of every blob a run
        attached besides its outputs: logged samples and stored logs (storage accounting)."""
        with self.ro() as conn:
            rows = [
                (*r, "sample")
                for r in conn.execute(
                    sa.select(
                        execution_samples.c.execution_id,
                        execution_samples.c.hash,
                        executions.c.experiment_id,
                        executions.c.node,
                    ).join(executions, executions.c.id == execution_samples.c.execution_id)
                )
            ]
            rows += [
                (*r, "log")
                for r in conn.execute(
                    sa.select(
                        execution_log_blobs.c.execution_id,
                        execution_log_blobs.c.hash,
                        executions.c.experiment_id,
                        executions.c.node,
                    ).join(executions, executions.c.id == execution_log_blobs.c.execution_id)
                )
            ]
        return rows

    def sample_hashes(self) -> list[tuple[str, str, str | None, str]]:
        """``(execution_id, hash, experiment that ran it, node)`` of every logged sample."""
        with self.ro() as conn:
            rows = conn.execute(
                sa.select(
                    execution_samples.c.execution_id,
                    execution_samples.c.hash,
                    executions.c.experiment_id,
                    executions.c.node,
                ).join(executions, executions.c.id == execution_samples.c.execution_id)
            ).all()
        return [tuple(r) for r in rows]

    def record_environment(self, exec_id: str, env_hash: str, data: dict[str, Any] | None) -> None:
        """Link an execution to a snapshot; ``data`` may be ``None`` when it was stored before."""
        with self.tx() as conn:
            known = conn.execute(sa.select(environments.c.hash).where(environments.c.hash == env_hash)).first()
            if known is None and data is not None:
                try:
                    with conn.begin_nested():
                        conn.execute(environments.insert().values(hash=env_hash, data=data))
                except IntegrityError:  # stored concurrently by another step
                    pass
            conn.execute(execution_environments.delete().where(execution_environments.c.execution_id == exec_id))
            conn.execute(execution_environments.insert().values(execution_id=exec_id, env_hash=env_hash))

    def environment(self, exec_id: str) -> dict | None:
        with self.ro() as conn:
            row = conn.execute(
                sa.select(environments.c.hash, environments.c.data)
                .join(execution_environments, execution_environments.c.env_hash == environments.c.hash)
                .where(execution_environments.c.execution_id == exec_id)
            ).first()
        return {"hash": row[0], **row[1]} if row else None

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
            rows = (
                conn.execute(
                    sa.select(
                        executions.c.id, executions.c.started_at, executions.c.finished_at, executions.c.experiment_id
                    ).where(executions.c.id.in_(ids))
                )
                .mappings()
                .all()
            )
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

    # -- moving experiments between labs (``dendrite push``) -------------------------
    def export_bundle(self, exp_ids: Iterable[str]) -> dict[str, Any]:
        """Everything needed to recreate these experiments elsewhere: their rows, node runs and
        stages, the executions they reference with lineage, metrics and sources, and the
        artifacts all of that names. Parents come first."""
        ids = list(dict.fromkeys(exp_ids))
        with self.ro() as conn:

            def rows(table, cond) -> list[dict]:
                return [dict(r) for r in conn.execute(sa.select(table).where(cond)).mappings().all()]

            exps = rows(experiments, experiments.c.id.in_(ids))
            by_id = {e["id"]: e for e in exps}
            order: list[dict] = []

            def visit(e: dict) -> None:  # parents before forks
                if e in order:
                    return
                if e["parent_id"] in by_id:
                    visit(by_id[e["parent_id"]])
                order.append(e)

            for e in exps:
                visit(e)
            runs = rows(node_runs, node_runs.c.experiment_id.in_(ids))
            exec_ids = sorted({r["execution_id"] for r in runs if r["execution_id"]})
            out = {
                "experiments": order,
                "node_runs": runs,
                "node_stages": rows(node_stages, node_stages.c.experiment_id.in_(ids)),
                "pipeline_reads": rows(pipeline_reads, pipeline_reads.c.experiment_id.in_(ids)),
                "executions": rows(executions, executions.c.id.in_(exec_ids)),
                "execution_inputs": rows(execution_inputs, execution_inputs.c.execution_id.in_(exec_ids)),
                "execution_outputs": rows(execution_outputs, execution_outputs.c.execution_id.in_(exec_ids)),
                "metrics": rows(metrics, metrics.c.execution_id.in_(exec_ids)),
                "metric_times": rows(metric_times, metric_times.c.execution_id.in_(exec_ids)),
                "execution_sources": rows(execution_sources, execution_sources.c.execution_id.in_(exec_ids)),
            }
            src_hashes = {r["source_hash"] for r in out["execution_sources"]}
            out["code_snapshots"] = rows(code_snapshots, code_snapshots.c.hash.in_(src_hashes))
            hashes: set[str] = set()
            for e in order:
                hashes |= set(e["inputs"].values())
                for outs in e["pins"].values():
                    hashes |= set(outs.values())
            for r in runs:
                hashes |= set((r["inputs"] or {}).values()) | set((r["outputs"] or {}).values())
            hashes |= {r["artifact_hash"] for r in out["execution_inputs"] + out["execution_outputs"]}
            hashes |= {r["artifact_hash"] for r in out["pipeline_reads"]}
            out["names"] = rows(artifact_names, artifact_names.c.hash.in_(hashes))  # current names, no history
            out["artifacts"] = rows(artifacts, artifacts.c.hash.in_(hashes))
        return out

    def import_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Recreate exported experiments here, in one transaction.

        Experiments that already exist are skipped (pushing twice is harmless). An execution
        whose id exists is reused, and so is a successful cacheable execution this lab already
        has for the same signature, so the copy joins the existing cache instead of duplicating
        it. Returns the ids created and how executions were matched.
        """
        created, skipped, inserted, reused = [], [], [], {}
        with self.tx() as conn:
            have = {
                r[0]
                for r in conn.execute(
                    sa.select(experiments.c.id).where(experiments.c.id.in_([e["id"] for e in bundle["experiments"]]))
                )
            }
            new_exps = [e for e in bundle["experiments"] if e["id"] not in have]
            skipped = sorted(have)
            new_ids = {e["id"] for e in new_exps}
            self._register_artifacts(
                conn, [ArtifactRef(a["hash"], a["kind"], a["size"], a["filename"]) for a in bundle["artifacts"]]
            )
            for snap in bundle["code_snapshots"]:
                self._insert_snapshot(conn, {k: v for k, v in snap.items() if k != "created_at"})
            # Executions: reuse what's here, insert the rest with their original ids.
            exec_map: dict[str, str] = {}
            wanted = {r["execution_id"] for r in bundle["node_runs"] if r["experiment_id"] in new_ids}
            for ex in [e for e in bundle["executions"] if e["id"] in wanted]:
                if conn.execute(sa.select(executions.c.id).where(executions.c.id == ex["id"])).first():
                    exec_map[ex["id"]] = ex["id"]
                    continue
                if ex["status"] == "success" and ex["cacheable"]:
                    hit = conn.execute(
                        sa.select(executions.c.id).where(
                            executions.c.signature == ex["signature"], executions.c.status == "success"
                        )
                    ).first()
                    if hit:
                        exec_map[ex["id"]] = reused[ex["id"]] = hit[0]
                        continue
                conn.execute(executions.insert().values(**ex))
                exec_map[ex["id"]] = ex["id"]
                inserted.append(ex["id"])
            fresh = set(inserted)
            for table in (execution_inputs, execution_outputs, metrics, metric_times, execution_sources):
                key = table.name
                for r in bundle.get(key, []):
                    if r["execution_id"] in fresh:
                        conn.execute(table.insert().values(**r))
            # Experiments, parents first; a parent that isn't here or in the bundle is dropped.
            for e in new_exps:
                row = dict(e)
                if row["parent_id"] and row["parent_id"] not in new_ids and row["parent_id"] not in have:
                    row["parent_id"] = None
                conn.execute(experiments.insert().values(**row))
            for r in bundle["node_runs"]:
                if r["experiment_id"] in new_ids:
                    row = dict(r)
                    if row["execution_id"]:
                        row["execution_id"] = exec_map.get(row["execution_id"])
                    conn.execute(node_runs.insert().values(**row))
            for table in (node_stages, pipeline_reads):
                for r in bundle[table.name]:
                    if r["experiment_id"] in new_ids:
                        conn.execute(table.insert().values(**r))
            created = [e["id"] for e in new_exps]
            # Names: add the missing ones; one that exists here with another target is never overwritten.
            names_added, names_skipped = [], []
            known_exps = new_ids | have
            bundled = {a["hash"] for a in bundle["artifacts"]}
            for n in bundle.get("names", []):
                if n["hash"] not in bundled:  # its blob didn't travel (e.g. evicted locally)
                    continue
                cur = conn.execute(sa.select(artifact_names.c.hash).where(artifact_names.c.name == n["name"])).first()
                if cur is None:
                    row = dict(n)
                    if row.get("experiment_id") not in known_exps:
                        row["experiment_id"] = None
                    conn.execute(artifact_names.insert().values(**row))
                    names_added.append(n["name"])
                elif cur[0] != n["hash"]:
                    names_skipped.append(n["name"])
        return {
            "created": created,
            "skipped": skipped,
            "executions": inserted,
            "reused": reused,
            "names": names_added,
            "names_skipped": names_skipped,
        }

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
