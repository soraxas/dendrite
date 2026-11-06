"""Toy 3D-reconstruction pipeline: frames -> COLMAP -> Gaussian splats -> eval.

    uv run python examples/recon.py          # baseline + two forks
    uv run dendrite ui                       # watch / browse lineage

Set DEMO_SLEEP=2 to make each node take a while so you can watch progress live.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

import dendrite
from dendrite import artifact_task, log_image, log_metric, pipeline, progress, workdir

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
    n = len(list(frames.iterdir()))
    # Real pipelines shell out to COLMAP; dendrite.run streams the tool's output into the step's log.
    fake_colmap = (
        "import sys, time\n"
        f"for stage in ('feature_extractor', 'matcher ({matcher})', 'mapper'):\n"
        "    print(f'[colmap] {stage}: processing {sys.argv[1]} images', flush=True)\n"
        f"    time.sleep({SLEEP})\n"
        "print('[colmap] registered all images')"
    )
    dendrite.run([sys.executable, "-c", fake_colmap, str(n)])
    cams = workdir() / "cameras.json"
    cams.write_text(json.dumps({"matcher": matcher, "n_cameras": n}))
    sparse = workdir() / "sparse"
    sparse.mkdir()
    (sparse / "points3D.txt").write_text("\n".join(f"-{i} {i * 0.1:.2f}" for i in range(n * 10)))
    return cams, sparse


@artifact_task(outputs=["model"], packages=["prefect"])
def train_splats(frames: Path, cameras: Path, sparse: Path, iterations: int = 30_000, lr: float = 1e-3, seed: int = 42):
    rng = random.Random(seed)
    every = max(1, iterations // 40)
    for step in range(0, iterations + 1, every):
        time.sleep(SLEEP * 0.05)
        loss = 1.0 / (1 + step * lr)
        log_metric("loss/train", loss + rng.random() * 0.02, step=step)  # one chart, two lines
        log_metric("loss/val", loss * 1.1 + rng.random() * 0.03, step=step)
        progress(step, iterations, "iterations")  # shown live on the step's card
        if step % (every * 8) == 0:
            print(f"iter {step:>6}  loss {loss:.4f}")
            log_image("render", _render(workdir(), step / iterations), step=step)
    model = workdir() / "splats.ply"
    model.write_text(f"ply\n# iterations={iterations} lr={lr} seed={seed}\n")
    return model


def _render(where: Path, done: float) -> Path:
    """A stand-in render that sharpens as training goes on (an SVG, so no imaging library)."""
    blur = 12 * (1 - done) + 0.5
    svg = where / f"render-{done:.3f}.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120">'
        f'<filter id="b"><feGaussianBlur stdDeviation="{blur:.2f}"/></filter>'
        '<rect width="160" height="120" fill="#1d2433"/><g filter="url(#b)">'
        '<circle cx="60" cy="62" r="28" fill="#e8833a"/><circle cx="104" cy="52" r="20" fill="#3aa675"/>'
        "</g></svg>"
    )
    return svg


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
