## ADDED Requirements

### Requirement: Hosted-mode UI

When served by a dendrite server, the UI SHALL:

- require sign-in through the server's OIDC login
- offer a switcher between the projects the user belongs to
- show who created each experiment and each execution
- hide controls the user's role doesn't permit
- disable source editing

Mutating requests SHALL use the session cookie together with a CSRF header.

#### Scenario: Signed-out visitor

- **WHEN** someone without a session opens the server's UI
- **THEN** they are sent to sign in, and no project data is returned before they do

#### Scenario: Viewer sees no destructive controls

- **WHEN** a viewer opens the storage page
- **THEN** the inventory is shown, without delete, evict or clean-up buttons

#### Scenario: Cross-site request

- **WHEN** a mutating request arrives with a valid session cookie but no CSRF header
- **THEN** the server answers 403
