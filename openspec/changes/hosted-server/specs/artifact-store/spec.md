## MODIFIED Requirements

### Requirement: Pluggable backends

The system SHALL provide three backends:

- a local filesystem backend
- an fsspec object-store backend, selected by URL (`file://`, `s3://`, `memory://`, …), which is compatible with S3 and MinIO
- a server-brokered backend for hosted mode: reads and writes go directly to the bucket through URLs the dendrite server signs, and the client holds no bucket credentials

#### Scenario: Object store backend

- **WHEN** `DENDRITE_STORE_URL` is an `s3://bucket/prefix` URL
- **THEN** blobs are written to that bucket, and inputs are materialised to a local cache before node execution

#### Scenario: Server-brokered backend

- **WHEN** a `Lab` resolves to a server project
- **THEN** blobs are uploaded and downloaded with presigned URLs, and inputs are materialised to the same local checkout cache before node execution
