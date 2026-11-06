# Field-testing dendrite on a real pipeline

Tests cover dendrite's own behaviour. They can't tell you how it behaves with your tools, data sizes and GPUs. This is a short routine for trying it on a real pipeline and noting what to fix.

## 1. Run the harness

Use a small but real input: a few hundred frames rather than a full capture, and a short training run. The point is to exercise COLMAP, CUDA and friends, not to wait hours.

```bash
uv run python scripts/field_test.py --import my_project.pipelines --pipeline reconstruction \
    --input video=data/small_scan.mp4 --param train_splats.iterations=2000 \
    --rerun-check --out field-test.md
```

The harness:

1. **Runs the pipeline once, cold**, in a fresh lab under `.dendrite-field-test/`.
2. **Runs an identical copy.** Every step should be reused. One that runs again has something in its signature that changes between runs.
3. **With `--rerun-check`, reruns every step** and lists the ones whose outputs differ (nondeterministic steps).
4. **Prints a report per step:** time, reuse, how much console output was captured, metrics, samples, and whether the environment was recorded (and whether git was dirty).

## 2. Walk through the UI

```bash
DENDRITE_HOME=.dendrite-field-test/<run> uv run dendrite ui --import my_project.pipelines
```

Check each of these, and note anything wrong or confusing:

- [ ] **Live console.** Start a longer run in another terminal and watch a heavy step's Console tab while it runs. Do COLMAP's or the trainer's lines appear within a second or two? Are progress bars readable?
- [ ] **Compiled output.** Do pycolmap, CUDA or OpenCV messages show up? If not, was the step running alongside others (see `docs/telemetry.md`)?
- [ ] **Charts.** Log `loss/train` and `loss/val` from your trainer. Try smoothing, the Time axis, log scale and drag-to-zoom on a real curve.
- [ ] **Samples.** `log_image` a render every N iterations. Does the slider make progress visible? Is it fast with full-size renders?
- [ ] **Fork and compare.** Fork, change one training parameter, and run. Is everything upstream reused? Does **Artifacts → Compare** on the render or model show the difference you expect?
- [ ] **Names.** Name the best model, then `lab.load("@name")` it from a notebook.
- [ ] **Storage.** Do the sizes look right for multi-GB outputs? Does clean-up preview sensibly?
- [ ] **Speed.** Is the UI responsive with your graph size (fan-outs of hundreds of frames or tiles)?
- [ ] **Phone.** Open it from a phone on the same network (`--host 0.0.0.0`). Can you check a run's progress?

## 3. Try adoption on existing code

Take one slow function from the existing codebase, add `@dendrite.step`, and call it from your usual script. Note any friction:

- arguments that didn't hash the way you expected
- reads of config files or globals that the cache didn't see
- outputs that came back in a different form

## 4. Report back

Keep the harness report (`--out`) and a list of what surprised you. The most useful notes are:

- steps that recomputed when they shouldn't have, or reused when they shouldn't have
- output missing from the console
- anything slow: UI pages, a fan-out, uploads to a team server
- anything you had to read the docs to understand
