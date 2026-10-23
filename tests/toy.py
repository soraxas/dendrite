"""Toy pipeline used by the tests; CALLS counts real executions per node."""

import threading
import time
from collections import Counter
from pathlib import Path

from dendrite import artifact_task, log_metric, pipeline, workdir

CALLS: Counter = Counter()
SLEEP = {"train": 0.0}
FAIL = {"train": False}
_lock = threading.Lock()


def _count(name: str) -> None:
    with _lock:
        CALLS[name] += 1


@artifact_task(outputs=["rows"])
def extract(src: Path, limit: int = 100) -> Path:
    _count("extract")
    out = workdir() / "rows"
    out.mkdir()
    for i, line in enumerate(src.read_text().splitlines()[1 : 1 + limit]):
        (out / f"{i:03d}.txt").write_text(line)
    return out


@artifact_task(outputs=["features"])
def featurize(rows: Path, scale: int = 1) -> list:
    _count("featurize")
    return sorted(sum(int(x) for x in f.read_text().split(",")) * scale for f in rows.iterdir())


@artifact_task(outputs=["model", "report"])
def train(features: list, iterations: int = 10, seed: int = 0):
    _count("train")
    if SLEEP["train"]:
        time.sleep(SLEEP["train"])
    if FAIL["train"]:
        raise RuntimeError("boom")
    for step in range(0, iterations + 1, max(1, iterations // 2)):
        log_metric("loss", 1 / (1 + step), step=step)
    log_metric("score", sum(features) * iterations)
    model = workdir() / "model.txt"
    model.write_text(f"{features} x{iterations} seed={seed}")
    return model, {"iterations": iterations}


@artifact_task(outputs=["sample"], cache=False)
def sample(features: list, n: int = 1) -> list:
    _count("sample")
    return features[:n]


@pipeline(name="toy")
def toy(src):
    rows = extract(src)
    feats = featurize(rows)
    model, report = train(feats)
    return model


@pipeline(name="toy_parallel")
def toy_parallel(src):
    rows = extract(src)
    feats = featurize.submit(rows)
    s = sample.submit(feats)
    trained = train.submit(feats)
    return trained, s


@pipeline(name="toy_sample")
def toy_sample(src):
    return sample(featurize(extract(src)))


@pipeline(name="toy_branch")
def toy_branch(src):
    from dendrite import runtime

    feats = featurize(extract(src))
    if runtime.active().lab.load(feats):  # branches on a result's contents
        train(feats)


@artifact_task(outputs=["text"])
def read_extra(src: Path, extra: str = "", swallow: bool = False) -> str:
    """Reads ``extra`` without declaring it (the guard should notice) and its declared input."""
    text = src.read_text()
    if extra:
        try:
            text += Path(extra).read_text()
        except Exception:
            if not swallow:
                raise
    (workdir() / "scratch.txt").write_text("x")
    return text + (workdir() / "scratch.txt").read_text()


@pipeline(name="toy_reads")
def toy_reads(src):
    return read_extra(src)
