## ADDED Requirements

### Requirement: Step contract

The system SHALL document a step contract with three parts:

- The pipeline body SHALL only wire steps together and branch on params.
- A step's outputs SHALL depend only on what goes into its signature: code, consumed params, artifact inputs, env and packages.
- Params SHALL be JSON-serialisable values and SHALL NOT require type hints.

The system SHALL reject a param that isn't JSON-serialisable, with an error that suggests passing it as an artifact.

#### Scenario: Untyped param

- **WHEN** a step declares `def train(cameras, iterations=30000)` without annotations
- **THEN** `iterations` is a param, hashed by exact value, and editable as an integer

#### Scenario: Non-serialisable param

- **WHEN** a step is called with a param value that can't be encoded as JSON, such as an open file handle
- **THEN** the call fails with an error suggesting the value be passed as an artifact

### Requirement: Undeclared read guard

While a step function executes, the system SHALL detect Python-level opens for reading of files under the project root. Paths under any of these SHALL be excluded:

- the step's workdir
- the checkout path of a declared input
- `DENDRITE_HOME`
- a virtualenv or site-packages
- a code file (`.py`, `.pyc`, `.pyi`, `.so`, `.pyd`, `.pth`, `.dylib`)

`DENDRITE_CHECK_READS` SHALL choose the response:

- `warn` (the default): emit one `UndeclaredReadWarning` per step and path
- `error`: fail the execution
- `off`: no check

The guard SHALL NOT apply during planning.

#### Scenario: Hidden config file

- **WHEN** a step reads `./config.yaml` from the project directory without declaring it as an input
- **THEN** an `UndeclaredReadWarning` names the step and path, and suggests passing it as an artifact

#### Scenario: Declared input is silent

- **WHEN** a step reads files of an artifact input it declared, or files in its own workdir
- **THEN** no warning is emitted

#### Scenario: Error mode

- **WHEN** `DENDRITE_CHECK_READS=error` and a step reads an undeclared project file
- **THEN** the execution fails, and no result is published for its signature
