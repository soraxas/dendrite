# Finding and reusing results: names, the Artifacts tab and Compare

Every result is an artifact identified by its content hash. That makes caching exact, but hashes are hard to remember. This guide covers the tools for finding results, comparing them, and reusing the ones that matter.

## The Artifacts tab

Open an experiment and choose **Artifacts**. It lists everything the experiment read and produced:

- **Grouping.** Inputs first, then each step's outputs in pipeline order, grouped by stage and step. A fan-out with many items folds into one row with a count; **show** expands it.
- **Each row** shows a thumbnail (images up to 2 MiB), the output name, its names, its size, and where it came from:
  - **computed**: this experiment ran the step
  - **reused from** *experiment*: the result came from that experiment's run
  - **frozen**: the step was pinned to an earlier result
- **Filters.** Narrow by kind (images, video, 3D, folders, values, other) or by text matched against the step, output, file name and names. Filters are part of the URL, so a filtered view can be shared.
- **Selecting a row** opens the artifact in the side panel, with its preview, lineage, download and naming.

### Compare

**Compare** on any output row shows that output side by side for this experiment, its parent and its forks (up to eight).

- Each column is labelled with its experiment and the parameters that differ, as "`key` its value (here this experiment's value)".
- A fork that produced the same artifact says so instead of loading it again, so you can see at a glance which changes made a difference.

## Names

A name is a human-readable label for one artifact, such as `best-splats` or `paper-fig3`. Use it anywhere a hash is accepted, by writing `@name`.

### Naming something

From code:

```python
lab.name("best-splats", f"{exp.id}:train_splats.model", note="60k iterations")
lab.name("raw-scan", f"{exp.id}:video")        # an experiment input
lab.name("feats", f"{exp.id}:featurize")       # a step with a single output
lab.name("that-one", "3f9a1c")                 # a hash or unique prefix
lab.name("fig3", f"{exp.id}:render.video", tags=["paper", "ablation"])   # tags help find it later
```

From the CLI:

```bash
dendrite name best-splats EXP:train_splats.model --note "60k iterations"
dendrite name fig3 EXP:render.video --tag paper --tag ablation
dendrite names                          # every name, with its artifact, source, tags, note and who set it
dendrite names --tag paper              # only names with that tag
dendrite names --history best-splats    # where it pointed before
dendrite unname best-splats             # the artifact stays
```

In the UI, select an artifact and press **☆ Name…**. It suggests `step-output` as the name. Names appear as ★ chips in the panel, on the graph's output chips and in the Artifacts tab, and the **Names** tab lists them all.

A name uses letters, digits, `.`, `_` and `-`, starts with a letter or digit, and is at most 100 characters. Tags follow the same rule, up to 50 characters. Moving a name keeps its tags unless you give new ones, and `tags=[]` clears them. On the Names page, click a tag (or type `#paper`) to show only names with it.

### Using a name

```python
lab.load("@best-splats")                                    # a Path, or the value
lab.create(recon, inputs={"video": "@raw-scan"})            # as an input
exp.fork(pin={"colmap.cameras": "@good-cameras"})           # as a pin
```

- **Experiments record the artifact, not the name.** An input or pin given as `@name` stores the hash it pointed at when the experiment was created, so moving the name later never changes an existing experiment.
- **A string starting with `@` is always a name**, never a file path. For a file whose name really starts with `@`, pass `Path("@file")`.
- **Unknown names fail clearly:** `KeyError: no artifact named '@typo'`.

### Moving and removing

- **Moving needs intent.** Setting a name that already points elsewhere fails unless you pass `move=True` (`--force` on the CLI, **Move it here** in the UI). The previous target goes into the name's history.
- **Concurrent edits are caught.** The UI moves and removes a name only if it still points where it did when you looked; otherwise you're told someone changed it.
- **Removing** a name keeps the artifact and records the removal in the history.

### Names and storage

- **Named artifacts are never cleaned up**, by garbage collection or by age-based eviction. The Storage page shows how much space names keep, and marks them as protected in the largest-artifacts list.
- **Evicting a named output is refused** unless forced; remove the name first.
- **Removing the name** makes the artifact collectable again once nothing else uses it.

### Names are metadata only

Naming, moving or removing a name never changes a signature, a plan or a cache decision. Running the same experiment before and after naming its results reuses everything.

### On a team server

- **Names are per project.** Everyone in the project can see and use them.
- **The server records who set each name**, by account, so renaming someone doesn't change who owns their names. Anyone with the runner role can create names; only the person who set a name, or a project admin, can move, re-note, re-tag or remove it.
- **`dendrite push`** brings your local names along. A name that already points at a different artifact on the server is kept as it is, and the push reports it.

### Over HTTP

| Endpoint | |
|---|---|
| `GET /api/names` | every name, with its artifact's kind, size and file name |
| `PUT /api/names/{name}` | set or move: `{experiment, node, output}` or `{hash}`, plus `note`, `tags`, `move` and `expect` (the target you expect it to have now) |
| `DELETE /api/names/{name}?expect=HASH` | remove |
| `GET /api/names/{name}/history` | the current target and every earlier one |
| `GET /api/compare?base=EXP&node=N&output=O&exps=A,B` | one output across experiments: each one's artifact and its parameter differences from `base` |

A conflict answers 409 with `code` `name_taken` (it points elsewhere; send `move`) or `name_moved` (it changed since you looked). DAG, artifact and lineage responses include a `names` map from hash to names.
