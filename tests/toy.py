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


@pipeline(name="toy_result")
def toy_result(src):
    feats = featurize.submit(extract(src))
    return train(feats.result())  # bodies may resolve futures themselves


FANOUT = {"s0": 1, "s1": 2}  # node key -> featurize scale; tests rewrite it between runs


@pipeline(name="toy_fanout")
def toy_fanout(src):
    rows = extract(src)
    return [featurize.named(key).submit(rows, scale=scale) for key, scale in FANOUT.items()]


@artifact_task
def tidy(src: Path) -> str:
    """Builds and removes a scratch tree: rmtree opens names relative to a directory fd."""
    import shutil

    scratch = workdir() / "scratch"
    (scratch / "deep").mkdir(parents=True)
    (scratch / "deep" / "f.txt").write_text("x")
    shutil.rmtree(scratch)
    return src.read_text()


@pipeline(name="toy_tidy")
def toy_tidy(src):
    return tidy(src)


@artifact_task
def slow_count(features: list, n: int = 4) -> int:
    from dendrite import progress

    for i in range(n):
        progress(i + 1, n, "items")
    return n


@pipeline(name="toy_decides")
def toy_decides(src, config):
    from dendrite import load

    settings = load(config)  # an input the body reads: it configures the run
    feats = featurize(extract(src), scale=settings["scale"])
    plan = load(feats)  # a step output the body reads: it decides the fan-out
    return [slow_count.named(i).submit(feats, n=2 + i) for i in range(len(plan[:2]))]


@artifact_task
def echo(value) -> list:
    return [str(v) for v in value]


@pipeline(name="toy_wait")
def toy_wait(src):
    trained = train.submit(featurize(extract(src)))
    return echo.submit(trained)  # queued until the slow train finishes


from dendrite import stage  # noqa: E402


@stage("Prepare", "Rows and features", color="teal")
def prepare(src):
    return featurize(extract(src))


@artifact_task(stage="Fallback")
def tag(features: list) -> int:
    return len(features)


@pipeline(name="toy_stages")
def toy_stages(src):
    feats = prepare(src)
    with stage("Model"):
        with stage("Train"):
            model, report = train(feats)
    tag(feats)
    return model
