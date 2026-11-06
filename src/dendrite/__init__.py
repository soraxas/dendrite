"""dendrite: artifact-first experiment lineage and global caching on Prefect 3."""

import os
from importlib.metadata import PackageNotFoundError, version

# Opt out of Prefect SDK telemetry. Prefect reads this once during `import prefect`, so it
# must be set before the submodules below import it. setdefault keeps an explicit override.
os.environ.setdefault("DO_NOT_TRACK", "1")
# gcsfs defaults to an experimental gRPC client for zonal/hierarchical buckets, which asks
# Google's API for each bucket's layout (ignoring endpoint_url, and stalling where gRPC is
# blocked). dendrite needs plain object storage; set it to "true" to opt back in.
os.environ.setdefault("GCSFS_EXPERIMENTAL_ZB_HNS_SUPPORT", "false")

try:
    __version__ = version("dendrite")
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0.0.0"

from .experiment import Experiment, Lab, NotADraft, Pipeline, pipeline
from .runtime import load, log_image, log_media, log_metric, progress, stage, use, workdir
from .store import ArtifactRef, ArtifactStore, FsspecStore, LocalStore
from .task import ArtifactTask, NondeterministicStep, artifact_task
from .telemetry import run

step = artifact_task  # `@dendrite.step`, or `dendrite.step(fn)` for a function you can't edit

__all__ = [
    "ArtifactRef",
    "ArtifactStore",
    "ArtifactTask",
    "Experiment",
    "FsspecStore",
    "Lab",
    "LocalStore",
    "NondeterministicStep",
    "NotADraft",
    "Pipeline",
    "artifact_task",
    "load",
    "log_image",
    "log_media",
    "log_metric",
    "pipeline",
    "progress",
    "run",
    "stage",
    "step",
    "use",
    "workdir",
]
