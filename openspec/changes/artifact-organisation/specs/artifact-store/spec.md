## ADDED Requirements

### Requirement: Named artifacts are kept by reclamation

Storage reclamation SHALL treat every artifact a name currently points at, together with the members of its directory manifest, as a root. Garbage collection, including age-based collection, MUST NOT delete it. Evicting an execution whose outputs include a named artifact SHALL be refused unless forced. A name's history SHALL NOT protect past targets.

#### Scenario: GC spares a named artifact

- **WHEN** the only experiment using an artifact is deleted but the artifact is named, and GC runs
- **THEN** the artifact's blobs remain and `@name` still loads

#### Scenario: Evict refused

- **WHEN** the user evicts an execution whose output is named, without forcing
- **THEN** eviction is refused and the refusal names the artifact's names

#### Scenario: Removed name

- **WHEN** a name is removed and nothing else references its artifact
- **THEN** the next GC may collect the artifact
