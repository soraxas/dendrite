"""The smallest dendrite setup: two steps, one run, and a fork that reuses the first step.

    uv run python examples/minimal.py
    uv run dendrite ui --import examples.minimal
"""

import tempfile
from pathlib import Path

from dendrite import Lab, artifact_task, pipeline


@artifact_task
def count_words(text: Path) -> dict:
    words = text.read_text().split()
    return {w: words.count(w) for w in words}


@artifact_task
def top(counts: dict, n: int = 3) -> list:
    return sorted(counts, key=lambda w: (-counts[w], w))[:n]


@pipeline
def word_stats(text):
    return top(count_words(text))


if __name__ == "__main__":
    src = Path(tempfile.mkdtemp()) / "speech.txt"
    src.write_text("the cat sat on the mat and the dog sat on the cat")

    lab = Lab()
    base = lab.create(word_stats, inputs={"text": src}, name="top-3").run()
    more = base.fork({"top.n": 5}, name="top-5").run()  # count_words is reused; only top runs

    for exp in (base, more):
        steps = {n["node"]: n["status"] for n in exp.node_runs()}
        print(f"{exp.name}: {exp.load('top')}  {steps}")
