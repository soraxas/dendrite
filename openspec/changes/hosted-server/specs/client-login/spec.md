## ADDED Requirements

### Requirement: Project config file

`dendrite init --server URL --project NAME` SHALL write a `dendrite.toml` holding the server URL and the project name. The file SHALL never contain credentials, so it is safe to commit.

Configuration SHALL resolve in this order, first match wins:

1. explicit `Lab` arguments
2. `DENDRITE_DB_URL` / `DENDRITE_STORE_URL`, which select direct mode
3. `DENDRITE_SERVER` with `DENDRITE_PROJECT`
4. the nearest `dendrite.toml`, searching upward from the working directory
5. local mode

#### Scenario: A cloned repo points at the team server

- **WHEN** a user runs a pipeline in a repo whose `dendrite.toml` names `https://lineage.team`, project `recon`
- **THEN** the run uses that server and project, with no further configuration

#### Scenario: No configuration keeps local mode

- **WHEN** no arguments, environment variables or `dendrite.toml` are present
- **THEN** `Lab()` uses local SQLite and a local store, exactly as before this change

#### Scenario: Environment overrides the file

- **WHEN** `DENDRITE_DB_URL` is set inside a repo with a `dendrite.toml`
- **THEN** direct mode is used, and the server is not contacted

### Requirement: Device-flow login

`dendrite login` SHALL sign the user in through the dendrite server:

1. The server issues a user code and a verification URL on itself.
2. The user opens the URL, signs in with the configured OIDC provider, and confirms the code.
3. The CLI polls until the server issues a dendrite token, or the code expires.

The CLI SHALL never talk to the OIDC provider directly.

#### Scenario: Successful login

- **WHEN** the user runs `dendrite login`, opens the printed URL, signs in and confirms the code
- **THEN** the CLI stores a token for that server and prints the signed-in identity

#### Scenario: Expired code

- **WHEN** the code is not confirmed before it expires
- **THEN** the CLI stops polling and exits non-zero with a message to retry

### Requirement: Stored credentials

Tokens SHALL be stored in `~/.config/dendrite/credentials.toml`, readable only by the user (mode 0600), and keyed by server URL.

- `DENDRITE_TOKEN` SHALL take precedence over the file.
- `dendrite logout` SHALL revoke the token on the server and remove it locally.
- `dendrite whoami` SHALL print the user, server, project and role.

#### Scenario: Logout revokes

- **WHEN** a user logs out, and the old token is later presented
- **THEN** the server answers 401

### Requirement: Automatic login prompt

When the resolved configuration names a server and no token is available, the first operation that needs the server SHALL handle it as follows:

- **Interactive** (a TTY, or an IPython/Jupyter kernel): it SHALL start the device-flow login inline, show the server URL and project, and continue once signed in.
- **Otherwise:** it SHALL raise `NotLoggedIn` with instructions to run `dendrite login` or set `DENDRITE_TOKEN`.

Constructing a `Lab` SHALL NOT prompt.

#### Scenario: First run in a terminal

- **WHEN** a user with no stored token runs `python run.py` in a terminal, in a repo configured for a server
- **THEN** they are shown the server URL, project, verification URL and code, and the run proceeds after they confirm

#### Scenario: CI without a token

- **WHEN** the same pipeline runs in CI with no `DENDRITE_TOKEN`
- **THEN** it fails immediately with `NotLoggedIn`, and does not wait for input

### Requirement: Service tokens

Admins SHALL be able to create named tokens scoped to one project and one role: `dendrite token create --project P --role runner --name ci`. They SHALL also be able to list and revoke them. The server SHALL store only a hash of each token.

#### Scenario: Scoped CI token

- **WHEN** CI uses a runner token for project `recon`
- **THEN** it can run experiments in `recon`, and gets 403 in every other project
