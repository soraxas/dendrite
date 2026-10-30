## ADDED Requirements

### Requirement: Remote metadata

`Lab` SHALL be able to use a remote implementation of `Metadata` that talks to a dendrite server project. The remote implementation SHALL behave the same as the SQLAlchemy one for every operation a client uses, including:

- atomic publish
- compare-and-set draft transitions
- lease acquisition, renewal and expiry
- change tokens for live updates

Retried writes SHALL take effect once.

#### Scenario: The existing suite passes remotely

- **WHEN** the test suite runs with `DENDRITE_TEST_REMOTE=1`, which backs the shared `lab` fixture with an in-process server project
- **THEN** every test that passes against local metadata also passes

#### Scenario: Retried publish

- **WHEN** a publish request reaches the server, but its response is lost and the client retries
- **THEN** the execution is published once, and the retry returns the same result
