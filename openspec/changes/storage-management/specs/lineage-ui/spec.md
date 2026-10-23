## ADDED Requirements

### Requirement: Storage endpoints
The HTTP API SHALL provide these endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /api/storage` | the inventory, cached briefly; `?refresh=1` recomputes |
| `DELETE /api/experiments/{id}` | delete any non-running experiment; `?forks=delete` or `?forks=keep` decide what happens to its forks; drafts behave as before |
| `POST /api/experiments/delete` | body `{ids, forks, force?}`: delete several in one transaction |
| `POST /api/storage/estimate` | body `{ids, forks}`: what deleting them frees after clean-up (read-only) |
| `POST /api/experiments/{id}/stop` | mark a stuck running experiment as failed |
| `POST /api/artifacts/{hash}/evict` | like execution evict, for one artifact |
| `POST /api/experiments/{id}/keep` | body `{keep}` |
| `POST /api/executions/{id}/evict` | body `{force?, apply?}`; a dry run unless `apply` |
| `POST /api/gc` | body `{older_than_days?, force?, apply?, hashes?, include_recent?}`; a dry run unless `apply`; with `hashes`, delete only those; `include_recent` skips the grace period |
| `POST /api/storage/clean` | body `{force?}`: clear checkouts and scratch |

Every endpoint that changes something SHALL require editing to be enabled.

#### Scenario: Editing disabled
- **WHEN** editing is disabled and a client posts to `/api/gc` with `apply`
- **THEN** the response is 403 and nothing is deleted
