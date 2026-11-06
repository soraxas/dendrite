## Why

Using dendrite today means rewriting the call site: write a `@pipeline`, create a `Lab`, turn files into experiment inputs, `run()`, then read results back with `exp.load`. Teams with an existing codebase want to try the cache on one slow function first. Once they do, the first questions are "how do I rerun this anyway?" and "how do I switch the cache off while I debug?", and today neither has a good answer.

## What Changes

- **Steps work outside a pipeline (eager mode).**
  - Calling a decorated step outside a running pipeline runs it at once, in-process and without Prefect, against the default lab (`Lab()`, so `dendrite.toml` and the environment apply), or the one set with `dendrite.use(lab)`.
  - **Arguments.** `Path` arguments, including those nested in lists or dicts, become file or directory inputs. Other non-JSON objects become value inputs, hashed by content. JSON values are params.
  - **Return values are plain:** a `Path` checkout or the value, never a ref, so existing code keeps working. Passing a returned `Path` into the next step links them in lineage, without hashing the file again.
  - **Sessions.** Each process records its eager calls in a *session* experiment named after the script and start time. It shows in the UI with its graph, logs and lineage, and its external files appear as its inputs. Calling a step again in the same session becomes `step[2]`, `step[3]`… instead of failing.
- **`dendrite.step`**, an alias of `artifact_task`. `dendrite.step(fn)` wraps a function you can't edit, such as a library function. C and builtin functions fingerprint by module, qualified name and package version.
- **Rerun anyway.**
  - `exp.run(rerun=["train"])` or `rerun=True` recomputes those steps even when a verified result exists, and `step.rerun(...)` does the same in eager mode. `dendrite run EXP --rerun NODE [--rerun …]` and `--rerun-all` work on experiments that have already run, replacing their node runs as re-running always has.
  - The new result is published, so it becomes the one reused from then on.
  - **Nondeterminism check.** When a rerun's outputs differ from the result it replaced, dendrite warns, both in Python and in the step's log, that the step isn't deterministic.
  - The UI shows reused and frozen steps' "Rerun anyway" command, ready to copy.
- **`DENDRITE_CACHE`:**
  - `on` (the default)
  - `off`: recompute everything, still recording and publishing results
  - `readonly`: reuse what exists, but never add to the cache
  - `bypass`: eager steps call the plain function and record nothing, so removing dendrite is one environment variable
- **Docs.** The README's quickstart gains "Add dendrite to an existing project" (one decorator first), and a new section, "Controlling the cache", collects `cache=False`, `version=`, rerun and `DENDRITE_CACHE`.
- **Non-goals:** forks, drafts or pins of sessions (move the calls into a `@pipeline` for that); eager steps across processes or machines.

## Capabilities

### New Capabilities

- `easy-adoption`: eager steps and sessions, `dendrite.step(fn)`, rerun with a nondeterminism check, `DENDRITE_CACHE` modes, and the onboarding docs.

### Modified Capabilities

None at the requirement level. Pipelines run exactly as before unless `rerun` or `DENDRITE_CACHE` is used.

## Impact

- **`task.py`:** `__call__` branches to an eager path when no run is active; rerun and cache modes in `_resolve`; fingerprints for builtins.
- **`runtime.py`:** the default lab, sessions, and `RunState.rerun`.
- **`experiment.py`:** `run(rerun=...)`, session experiments, and `Settings.cache_mode`.
- **`cli.py`:** `dendrite run --rerun/--rerun-all`.
- **UI:** the rerun hint in the node panel.
- **Docs:** README, plus a new `docs/adopting.md`.
