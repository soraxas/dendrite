"""Toy 3D-reconstruction pipeline: frames -> COLMAP -> Gaussian splats -> eval.

    uv run python examples/recon.py          # baseline + two forks
    uv run dendrite ui                       # watch / browse lineage

Set DEMO_SLEEP=2 to make each node take a while so you can watch progress live.
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

import dendrite
from dendrite import artifact_task, log_metric, pipeline, progress, workdir

SLEEP = float(os.environ.get("DEMO_SLEEP", "0.3"))


@artifact_task(outputs=["frames"])
def extract_frames(video: Path, fps: int = 2) -> Path:
    time.sleep(SLEEP)
    out = workdir() / "frames"
    out.mkdir()
    data = video.read_bytes()
    for i in range(0, max(1, len(data)), max(1, len(data) // (fps * 4))):
        (out / f"frame_{i:05d}.txt").write_bytes(data[i : i + 16])
    return out


@artifact_task(outputs=["cameras", "sparse"], version="2")
def colmap(frames: Path, matcher: str = "sequential") -> tuple[Path, Path]:
    time.sleep(SLEEP * 3)
    n = len(list(frames.iterdir()))
    cams = workdir() / "cameras.json"
    cams.write_text(json.dumps({"matcher": matcher, "n_cameras": n}))
    sparse = workdir() / "sparse"
    sparse.mkdir()
    (sparse / "points3D.txt").write_text("\n".join(f"-{i} {i * 0.1:.2f}" for i in range(n * 10)))
    return cams, sparse


@artifact_task(outputs=["model"], packages=["prefect"])
def train_splats(frames: Path, cameras: Path, sparse: Path, iterations: int = 30_000, lr: float = 1e-3, seed: int = 42):
    rng = random.Random(seed)
    for step in range(0, iterations + 1, max(1, iterations // 5)):
        time.sleep(SLEEP * 0.5)
        log_metric("loss", 1.0 / (1 + step * lr) + rng.random() * 1e-3, step=step)
        progress(step, iterations, "iterations")  # shown live on the step's card
    model = workdir() / "splats.ply"
    model.write_text(f"ply\n# iterations={iterations} lr={lr} seed={seed}\n")
    return model


@artifact_task(outputs=["report"])
def evaluate(model: Path, frames: Path) -> dict:
    time.sleep(SLEEP)
    iters = int(model.read_text().split("iterations=")[1].split()[0])
    psnr = 20 + 10 * (1 - 1 / (1 + iters / 20_000))
    log_metric("psnr", psnr)
    return {"psnr": psnr, "n_frames": len(list(frames.iterdir()))}


@pipeline(name="reconstruction")
def reconstruction(video):
    frames = extract_frames(video)
    cameras, sparse = colmap(frames)
    model = train_splats(frames, cameras, sparse)
    return evaluate(model, frames)


def main() -> None:
    lab = dendrite.Lab()
    video = lab.home / "demo_video.bin"
    if not video.exists():
        video.write_bytes(os.urandom(4096))

    base = lab.create(
        reconstruction,
        name="baseline",
        inputs={"video": video},
        params={"colmap": {"matcher": "exhaustive"}, "train_splats": {"iterations": 30_000}},
    ).run()
    longer = base.fork({"train_splats.iterations": 60_000}, name="60k-iters").run()
    # Artifact fork: freeze baseline's COLMAP output and try another learning rate.
    pinned = longer.fork({"train_splats.lr": 5e-4}, pin={"colmap": base}, name="lr-5e-4-pinned").run()

    for exp in (base, longer, pinned):
        statuses = {n["node"]: n["status"] for n in exp.node_runs()}
        print(f"{exp.id} {exp.name:>16}: {statuses}  psnr={exp.metrics()['evaluate']['psnr']:.2f}")


if __name__ == "__main__":
    main()
