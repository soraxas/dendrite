"""dendrite: artifact-first experiment lineage and global caching on Prefect 3."""

import os

# Opt out of Prefect SDK telemetry. Prefect reads this once during `import prefect`, so it
# must be set before the submodules below import it. setdefault keeps an explicit override.
os.environ.setdefault("DO_NOT_TRACK", "1")

from .experiment import Experiment, Lab, Pipeline, pipeline
from .runtime import log_metric, workdir
from .store import ArtifactRef, ArtifactStore, FsspecStore, LocalStore
from .task import ArtifactTask, artifact_task

__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "ArtifactTask",
    "Experiment",
    "FsspecStore",
    "Lab",
    "LocalStore",
    "Pipeline",
    "artifact_task",
    "log_metric",
    "pipeline",
    "workdir",
]
