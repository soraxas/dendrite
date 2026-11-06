# Adding dendrite to an existing project

You don't need to restructure anything to start. Put `@dendrite.step` on one slow function, keep calling it the way you already do, and it's cached, logged and visible in the UI. Move calls into a pipeline later, when you want forks and drafts.

## Step 1: one decorator

```python
import dendrite

@dendrite.step
def extract_frames(video: Path, fps: int = 2) -> Path:
    out = dendrite.workdir() / "frames"          # scratch space; anything returned is stored
    ...
    return out

frames = extract_frames(Path("scan.mp4"), fps=4)  # a plain call, a plain Path back
frames = extract_frames(Path("scan.mp4"), fps=4)  # instant: the cached result
```

How the arguments are treated:

- **`Path` arguments** (also inside lists and dicts) are inputs, identified by their content. Change the file and the step runs again; rename it and it doesn't.
- **JSON values** (numbers, strings, lists, dicts) are parameters, hashed exactly, so `2` and `2.0` differ.
- **Anything else** (arrays, dataframes, sets…) becomes an input stored by content.

The return value is what your code already expects: a `Path` for a file or directory, the object for anything else, or a tuple for several outputs. Pass one step's returned `Path` to the next, and dendrite links the two in lineage without hashing the file again.

### Functions you can't edit

```python
from somelib import heavy_transform

heavy_transform = dendrite.step(heavy_transform)   # same function, now cached
```

This works for compiled and builtin functions too. They're identified by their name and the installed version of the package providing them, so upgrading the package recomputes them.

### Where it's recorded

Eager calls go to the default lab: `.dendrite/` in the current directory, or whatever `dendrite.toml` and `DENDRITE_*` variables say, including a team server. `dendrite.use(dendrite.Lab(...))` picks another.

Each process records its calls as a **session**, an experiment named after the script and the time it started, such as `train.py · 14:03`. Open `dendrite ui` and it's there, with the graph of what called what, each step's console, charts, samples and environment, and lineage. Calling a step several times shows `step`, `step[2]`, `step[3]`…

## Step 2: move calls into a pipeline

When you want to try variations, put the calls in a `@pipeline` function. The steps don't change:

```python
@dendrite.pipeline
def reconstruction(video):
    frames = extract_frames(video)
    return train(frames)

lab = dendrite.Lab()
base = lab.create(reconstruction, inputs={"video": "scan.mp4"}).run()
base.fork({"train.iterations": 60_000}).run()    # only train runs again
```

Now you get forks, drafts you can edit in the UI, pins, plans and comparisons.

## What to watch for

The cache is only as good as what the signature sees: a step's own code, its inputs and the parameters it uses. Code you're adopting often reads things the cache can't see:

- **Config files or data read inside the function.** Pass them in as `Path` arguments. dendrite warns when a step reads project files it wasn't given.
- **Globals and environment variables.** Make them parameters, or put a fingerprint in `DENDRITE_ENV`.
- **Helper functions.** Only the step's own source is fingerprinted. When a helper changes, bump `@dendrite.step(version="2")`, or rerun.
- **Unseeded randomness.** Add a `seed` parameter. A rerun that gives different outputs is reported (see below).

## Controlling the cache

| You want | Do |
|---|---|
| Never cache this step (e.g. fetches "latest" data) | `@dendrite.step(cache=False)` |
| Invalidate after changing a helper | `@dendrite.step(version="2")` |
| Recompute one call now | `step.rerun(...)` |
| Recompute steps of an experiment | `exp.run(rerun=["train"])`, `dendrite run EXP --rerun train`, or `--rerun-all` |
| Recompute everything while debugging | `DENDRITE_CACHE=off` |
| Reuse what exists but add nothing | `DENDRITE_CACHE=readonly` |
| Turn dendrite off entirely | `DENDRITE_CACHE=bypass` (eager calls run the plain function and record nothing) |

A rerun keeps its new result, so later calls reuse it. If the new output differs from the result it replaced, dendrite issues a `NondeterministicStep` warning and notes it in the step's log. The step depends on something its signature doesn't include. In the UI, every reused step shows its rerun command, ready to copy.
