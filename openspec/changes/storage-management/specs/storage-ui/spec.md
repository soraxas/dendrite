## ADDED Requirements

### Requirement: Storage page

The SPA SHALL provide a Storage page, reachable from the top bar, with:

- totals for the store, live data, shared cache, collectable data and scratch
- an Experiments tab with size, exclusive size, keep toggle and delete
- a Cache tab with size, reuse count, last use, presence and evict
- a Largest artifacts tab linking to lineage

Mutating controls SHALL be disabled, with the reason, when editing is off.

#### Scenario: See what deleting frees

- **WHEN** a user opens the Experiments tab
- **THEN** each experiment shows how much deleting it would free after clean-up

### Requirement: Clean-up preview

Clean-up SHALL always show a dry-run report first: counts and bytes grouped by reason, plus skipped items with reasons. It SHALL offer an "also evict cache unused for N days" option, and apply exactly the previewed items only after confirmation.

#### Scenario: Confirm what was previewed

- **WHEN** a user previews clean-up and confirms
- **THEN** exactly the previewed blobs are deleted, and the totals refresh

### Requirement: Delete and size on experiments

Experiment details SHALL show the experiment's size and exclusive size, plus a Delete action. The confirmation SHALL state what is freed after clean-up and list any forks that will also be deleted. After deleting, the UI SHALL open the parent experiment.

#### Scenario: Delete with forks

- **WHEN** a user deletes an experiment that has forks
- **THEN** the confirmation lists the forks and deletes them only if the user agrees

### Requirement: Everything from the UI

Every storage action SHALL be available in the UI, with no CLI or Python needed:

- delete one experiment, choosing to keep its forks (the default) or delete them
- select several experiments, or those finished more than N days ago and not kept, and delete them together, with the combined estimate
- mark an experiment stuck as running stopped
- evict a cache entry or a large artifact (never an experiment input)
- clean up, including data written within the grace period when nothing runs
- override the "work is running" refusal, after a warning

#### Scenario: Remove old experiments, keep the winner

- **WHEN** a user selects experiments older than 30 days and deletes them, keeping forks
- **THEN** their newer forks stay and re-attach to the nearest remaining ancestor, and clean-up then frees the estimated bytes

#### Scenario: Crashed run

- **WHEN** an experiment was left running by a crashed process
- **THEN** the Storage page explains that clean-up waits for it and offers to mark it stopped
