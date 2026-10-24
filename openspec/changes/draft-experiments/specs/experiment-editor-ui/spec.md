## ADDED Requirements

### Requirement: Fork into a draft from the UI

When editing is enabled, the SPA SHALL offer "Fork…" on any experiment that isn't a draft. It creates a draft and opens it. When editing is off, the action SHALL be shown disabled, with the reason.

#### Scenario: Fork from the toolbar

- **WHEN** a user clicks "Fork…" on a completed experiment
- **THEN** a draft child is created, selected and shown in draft mode

### Requirement: Distinct draft mode

A draft SHALL render in a visually distinct mode, with a banner and background tint. The banner explains that nothing runs until it is run, shows the copyable run command, and offers Discard.

#### Scenario: Open a draft

- **WHEN** a draft is opened
- **THEN** the banner and tint are visible, and the graph shows planned outcomes instead of run statuses

### Requirement: Planned outcomes on the graph

In draft mode, every step SHALL show its planned outcome in plain language:

- "Reused from <experiment>"
- "Will run · about <estimate>"
- "Will run — <reason>"
- "Frozen"

The legend SHALL list only the outcomes present. The toolbar SHALL summarise the plan, e.g. "Will run 2 of 4 steps · about 14 min". An incomplete plan or an error SHALL be shown, not hidden.

#### Scenario: Param change preview

- **WHEN** a user changes a param on a step
- **THEN** after the edit is saved, that step and the steps downstream of it show as will-run, and the summary updates

### Requirement: Param and freeze editing

Selecting a step in draft mode SHALL show a form of its editable params.

- Each field shows its inherited value, a changed marker and a reset control.
- Values are sent as typed JSON matching the param's type.
- A Freeze toggle freezes the step to the parent's result, or unfreezes it.
- Edits SHALL save automatically, debounced, and the previous plan stays visible while the new one loads.

#### Scenario: Reset a param

- **WHEN** a user resets a changed param
- **THEN** the override is removed and the step's planned outcome returns to its earlier value

### Requirement: Drafts in the sidebar

Drafts SHALL appear in the experiment tree with a distinct draft mark, and the outcome text "Draft".

#### Scenario: Sidebar lists a draft

- **WHEN** a draft exists
- **THEN** it appears under its parent with the draft mark, and not as "No steps"
