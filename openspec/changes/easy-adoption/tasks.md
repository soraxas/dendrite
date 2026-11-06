## 1. Eager steps and sessions

- [x] 1.1 `runtime`: default lab (`dendrite.use`), lazily created session experiment per lab, session status per call
- [x] 1.2 `ArtifactTask.__call__` eager path: argument conversion (paths with memo, JSON params, value objects), repeated-call naming, `_enqueue` and `_execute` without Prefect, plain return values with checkout → ref memo
- [x] 1.3 `dendrite.step` alias; builtin fingerprints
- [x] 1.4 Session inputs on the experiment so the graph shows them

## 2. Rerun and cache modes

- [x] 2.1 `RunState.rerun`, `Experiment.run(rerun=...)`, `step.rerun(...)`
- [x] 2.2 `_resolve`: skip lookup for reruns, nondeterminism check and warning
- [x] 2.3 `Settings.cache_mode` (`DENDRITE_CACHE`): off, readonly, bypass
- [x] 2.4 `dendrite run --rerun/--rerun-all` on completed experiments

## 3. UI and docs

- [x] 3.1 "Rerun anyway" command in the node panel for reused and frozen steps
- [x] 3.2 README: "Add dendrite to an existing project" and "Controlling the cache"; `docs/adopting.md`

## 4. Tests

- [x] 4.1 Eager: one-decorator scenario, chaining lineage, object args, repeated names, builtins, session in the UI API
- [x] 4.2 Rerun: one step, all, eager rerun, nondeterminism warning and log line, CLI
- [x] 4.3 Cache modes: off, readonly, bypass
