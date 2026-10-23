## ADDED Requirements

### Requirement: Step progress
A step SHALL be able to report progress with `dendrite.progress(done, total=None, unit=None)`. Progress SHALL be recorded per execution, throttled, and never affect signatures. Calling it outside an executing step SHALL raise.

#### Scenario: Progress on a running step
- **WHEN** a running step reports `progress(40, 85, "frames")`
- **THEN** the experiment's DAG shows that node with `progress` `{done: 40, total: 85, unit: "frames"}`, and the UI shows a bar and the time left

### Requirement: Pushed change events
`GET /api/events` SHALL stream Server-Sent Events naming the experiments whose runs, statuses or progress changed, and whether the experiment list changed, including changes made by other processes. Clients SHALL refetch only what an event names.

#### Scenario: Another process runs an experiment
- **WHEN** an experiment runs in another process while a client is connected
- **THEN** the client receives events naming that experiment as its steps start, report progress and finish
