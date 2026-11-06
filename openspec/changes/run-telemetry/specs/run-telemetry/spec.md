## ADDED Requirements

### Requirement: Console capture per execution

While a step function runs, the system SHALL tee everything written to `sys.stdout`, `sys.stderr` and the `logging` module from that step's thread into the execution's log, while still passing it to the original streams. Each chunk SHALL record its stream (`stdout`, `stderr`, `log`), a sequence number and a timestamp. Chunks SHALL be flushed at least every `LOG_INTERVAL` seconds while running and completely before the execution is published or failed. Output written by other concurrently running steps MUST NOT appear in this execution's log.

#### Scenario: Print is captured and still shown

- **WHEN** a step calls `print("hello")`
- **THEN** "hello" appears in the terminal and in that execution's log on the stdout stream

#### Scenario: Concurrent steps keep separate logs

- **WHEN** two submitted steps print at the same time
- **THEN** each execution's log contains only its own lines

#### Scenario: Live while running

- **WHEN** a step has printed and is still running
- **THEN** the log endpoint returns the printed lines before the step finishes

#### Scenario: Failure keeps the traceback

- **WHEN** a step raises
- **THEN** its log ends with the traceback on the stderr stream and the execution is failed

### Requirement: Subprocess output through dendrite.run

`dendrite.run(cmd, **kwargs)` SHALL start a subprocess, stream its combined stdout and stderr into the current execution's log line by line (and to the terminal), return a completed-process result, and raise `subprocess.CalledProcessError` on a non-zero exit unless `check=False`. Outside an executing step it SHALL behave like `subprocess.run` with streaming to the terminal.

#### Scenario: Capture a tool's output

- **WHEN** a step calls `dendrite.run(["colmap", "feature_extractor", ...])`
- **THEN** the tool's output lines appear in the step's log while it runs

#### Scenario: Non-zero exit

- **WHEN** the subprocess exits with status 2
- **THEN** `CalledProcessError` is raised and the output up to that point is in the log

### Requirement: Log size limit

An execution's stored log SHALL NOT exceed `DENDRITE_LOG_MAX` bytes (default 16 MiB). When output exceeds it, the system SHALL keep the first half of the budget, then a single truncation marker stating how many bytes were dropped, then the most recent output within the remaining budget.

#### Scenario: Very chatty step

- **WHEN** a step prints 100 MiB with the default limit
- **THEN** the stored log is at most 16 MiB, starts with the first lines, ends with the last lines and contains one truncation marker

### Requirement: Live scalars with title and series

`log_metric(key, value, step)` SHALL persist values while the step runs, throttled, with every value persisted by the time the execution ends, including when it fails. A key containing `/` SHALL be split at the first `/` into a chart title and a series; a key without `/` is its own title and series. Repeating a key and step SHALL keep the last value.

#### Scenario: Watch training loss

- **WHEN** a step logs `loss/train` at steps 1..1000 and is still running
- **THEN** the scalars endpoint returns the values logged so far under title `loss`, series `train`

#### Scenario: Failed run keeps metrics

- **WHEN** a step logs metrics and then raises
- **THEN** the failed execution's metrics are still returned

### Requirement: Debug samples

`log_image(title, image, step=0, series=None)` SHALL accept a path, a PIL image or a numpy array (HxW, HxWx3 or HxWx4, uint8 or float in [0, 1]) and store it as PNG unless it is already an image file. `log_media(title, path, step=0, series=None)` SHALL store any file. Each sample SHALL be stored as a content-addressed blob, registered as an artifact, and recorded with its execution, title, series, step and time. Samples MUST NOT become node outputs or affect signatures. Per execution, title and series the system SHALL keep only the samples of the most recent `DENDRITE_SAMPLE_HISTORY` steps (default 100).

#### Scenario: Render during training

- **WHEN** a step calls `log_image("render", frame, step=i)` every 500 iterations
- **THEN** the samples endpoint lists one image per logged step under title `render`, each downloadable

#### Scenario: History cap

- **WHEN** a step logs 150 steps of the same title with the default history
- **THEN** only the latest 100 steps remain listed

#### Scenario: Samples never change signatures

- **WHEN** an experiment whose step logs images is forked without changes and run
- **THEN** the step is a cache hit and the fork shows the original samples

### Requirement: Environment capture

When an execution starts, the system SHALL record an environment snapshot: git commit, branch, remote URL with credentials removed, dirty flag and uncommitted diff (capped at 1 MiB, omitted when `DENDRITE_CAPTURE_DIFF=0`), Python version and executable, platform, hostname, GPU names when `nvidia-smi` is available (bounded by a short timeout), installed distributions and versions, the SHA-256 of `uv.lock` when present in the project root, and `DENDRITE_ENV`. It MUST NOT record environment variables. Identical snapshots SHALL be stored once and shared. A failure to capture any field SHALL leave that field empty and MUST NOT fail the execution.

#### Scenario: Dirty tree

- **WHEN** a step runs with uncommitted changes
- **THEN** its environment shows the commit, `dirty: true` and the diff

#### Scenario: Credentials stripped

- **WHEN** the git remote is `https://user:token@github.com/org/repo.git`
- **THEN** the recorded remote is `https://github.com/org/repo.git`

#### Scenario: Not a git checkout

- **WHEN** the project directory is not in a git repository
- **THEN** the git fields are empty and the execution runs normally

### Requirement: Telemetry is display-only and follows reuse

Logs, scalars, samples and environments SHALL belong to the execution that produced them. They MUST NOT enter any signature, plan or cache decision. A node run that reused an execution SHALL show that execution's telemetry, identified by the experiment that ran it. Plans MUST NOT write telemetry.

#### Scenario: Cache hit shows original logs

- **WHEN** a fork reuses `train` from its parent
- **THEN** the fork's `train` node shows the parent run's console, scalars and samples, labelled as coming from the parent experiment

### Requirement: Telemetry API and CLI

The server SHALL provide `GET /api/executions/{id}/logs?after=<seq>&limit=<n>` (chunks after a sequence number, plus whether the execution is still running), `GET /api/executions/{id}/scalars`, `GET /api/executions/{id}/samples`, `GET /api/executions/{id}/environment` and `GET /api/experiments/{id}/scalars` (scalars of every node run's execution, keyed by node). The CLI SHALL provide `dendrite logs <exp> <node> [-f]`, which follows a running execution until it ends, and `dendrite env <exp> <node>`.

#### Scenario: Follow logs

- **WHEN** a user runs `dendrite logs exp3 train -f` while `train` runs
- **THEN** new lines print as they are written and the command exits when the execution ends

### Requirement: Telemetry in hosted mode

In hosted mode the client SHALL batch log chunks and scalars to the server, flushing at the same points as locally. Sample blobs SHALL be committed through the existing upload path before their rows are written. The server SHALL accept log, scalar and sample writes for an execution only from the caller that holds or held its lease, and environment snapshots only for executions that caller started.

#### Scenario: Another user appends to my execution

- **WHEN** a runner sends log chunks for an execution started by someone else
- **THEN** the server refuses with a permission error
