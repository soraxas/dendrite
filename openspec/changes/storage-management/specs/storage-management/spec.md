## ADDED Requirements

### Requirement: Storage inventory
`Lab.storage()` SHALL report:
- store object count and bytes, split into live (reachable from an existing experiment's inputs, pins or node-run outputs, including dir members) and collectable
- per experiment: referenced bytes, exclusive bytes (reachable from no other experiment) and its keep flag
- cache entries (successful executions): output bytes, reuse count, last use, and whether the blobs are present
- the largest artifacts
- local checkout and scratch sizes

Each blob SHALL be counted once.

#### Scenario: Shared cache counted once
- **WHEN** a fork reuses its parent's first three steps and recomputes the fourth
- **THEN** the shared outputs count toward neither experiment's exclusive bytes, and the fork's exclusive bytes equal its fourth step's outputs

### Requirement: Deleting experiments
`Lab.delete(ids, forks="refuse"|"delete"|"keep")` SHALL remove the experiments, their node runs and their flags in one transaction. `Experiment.delete(forks=False, keep_forks=False)` SHALL delete one experiment the same way.

Forks of a deleted experiment that weren't selected SHALL be handled by `forks`:
- `refuse`: the deletion is refused
- `delete`: they are deleted too
- `keep`: each becomes a fork of its nearest ancestor that stays, or has no parent

Running experiments SHALL be refused unless forced. Executions, artifacts, metrics and lineage SHALL remain.

`Lab.deletion_estimate(ids, forks)` SHALL report the bytes clean-up would free after deleting them: blobs that only those experiments reach.

#### Scenario: Keep the winning fork
- **WHEN** an old baseline is deleted with `forks="keep"`
- **THEN** its fork stays, becomes a fork of the baseline's parent (or top-level), and still loads every output

#### Scenario: Estimate matches clean-up
- **WHEN** experiments are deleted and clean-up then runs
- **THEN** the bytes freed equal the estimate made before deleting

#### Scenario: Delete keeps shared cache usable
- **WHEN** a parent is deleted after a fork reused its steps
- **THEN** the fork still loads every output, and a new identical experiment still reuses them

#### Scenario: Running experiment
- **WHEN** `delete()` is called on a running experiment
- **THEN** it is refused and nothing changes

### Requirement: Stuck runs
`Experiment.mark_stopped()` SHALL mark a `running` experiment as failed, so an experiment left running by a crashed process no longer blocks clean-up. It SHALL refuse experiments that aren't running.

#### Scenario: Crashed run unblocks clean-up
- **WHEN** an experiment is stuck as running and is marked stopped
- **THEN** applying clean-up is no longer refused

### Requirement: Keep flag
`Experiment.keep(flag=True)` SHALL persist a flag that protects everything the experiment reaches from age-based eviction. The flag SHALL appear in the experiment summary and the fork tree.

#### Scenario: Kept experiment survives age-based GC
- **WHEN** `gc(older_than=0)` runs on a lab where one experiment is kept
- **THEN** none of the kept experiment's outputs are deleted

### Requirement: Eviction
`lab.evict(target)` SHALL delete the output blobs of an execution (or one artifact), except blobs still reachable from other live artifacts. It SHALL keep every metadata row. Without `force`, it SHALL refuse when a living experiment pins those outputs, or while any experiment is running.

#### Scenario: Evicted step recomputes
- **WHEN** an execution's outputs are evicted and an experiment needing that step runs
- **THEN** the step recomputes, and its lineage now shows the new execution

### Requirement: Garbage collection
`lab.gc(older_than=None, dry_run=True, grace=…)` SHALL delete collectable blobs. With `older_than`, it SHALL also delete the outputs of executions whose last use is older than the cutoff, except those reachable from a kept experiment, any pin, or the newest completed experiment of each pipeline. It SHALL:
- skip blobs modified within the grace period
- refuse while any experiment is running, unless forced
- return a report of what it deleted, or would delete, with a reason per blob, plus skipped blobs with the reason

A dry run SHALL delete nothing.

#### Scenario: Orphan from a failed run
- **WHEN** a blob exists in the store with no artifact record, and is older than the grace period
- **THEN** `gc()` reports it as `orphan`, and `gc(dry_run=False)` deletes it

#### Scenario: In-flight upload
- **WHEN** an unregistered blob was written a minute ago
- **THEN** `gc()` skips it with reason `grace`

#### Scenario: Apply exactly the preview
- **WHEN** a client applies a dry-run report's hashes
- **THEN** only those hashes are deleted, even if more became collectable in between

### Requirement: Scratch clean-up
`lab.clean()` SHALL remove the local checkout cache and step workdirs of executions that aren't running, without touching stored objects.

#### Scenario: Checkouts re-materialise
- **WHEN** `clean()` runs and an artifact is loaded afterwards
- **THEN** the load succeeds from the store

### Requirement: CLI
The CLI SHALL provide:
- `dendrite du [--json]`
- `dendrite rm EXP [--forks] [--yes]`
- `dendrite keep EXP [--off]`
- `dendrite evict EXEC_OR_ARTIFACT [--force] [--yes]`
- `dendrite gc [--older-than DAYS] [--force] [--yes]`
- `dendrite clean`

The commands that delete SHALL only print a preview without `--yes`.

#### Scenario: Preview by default
- **WHEN** `dendrite gc` is run without `--yes`
- **THEN** it prints what would be deleted and the bytes freed, and deletes nothing
