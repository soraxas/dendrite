"""A pipeline recorded on a team's dendrite server.

Nothing here mentions the server: ``dendrite.toml`` next to this file names it, so ``Lab()``
records there, and the first run asks you to sign in.

    uv run python pipeline.py            # first time: sign in, then it runs
    uv run python pipeline.py --n 5      # a fork: count_words is reused, only top runs
"""

import argparse
import time
from pathlib import Path

from dendrite import Lab, artifact_task, pipeline, progress, workdir


@artifact_task
def count_words(text: Path) -> dict:
    words = text.read_text().split()
    for i in range(10):  # pretend this is expensive
        time.sleep(0.2)
        progress(i + 1, 10, "chunks")
    return {w: words.count(w) for w in words}


@artifact_task
def top(counts: dict, n: int = 3) -> Path:
    out = workdir() / "top.txt"
    out.write_text("\n".join(sorted(counts, key=lambda w: (-counts[w], w))[:n]) + "\n")
    return out


@pipeline
def word_stats(text):
    return top(count_words(text))


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--n", type=int, default=3)
    n = args.parse_args().n

    text = Path(__file__).with_name("speech.txt")
    lab = Lab()
    print(lab)
    exp = lab.create(word_stats, inputs={"text": text}, params={"top.n": n}, name=f"top-{n}").run()
    steps = {r["node"]: r["status"] for r in exp.node_runs()}
    print(f"{exp.name}: {steps}")
    print(lab.load(exp.output("top")).read_text())
