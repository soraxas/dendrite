# artifact-store Specification

## Purpose

TBD - created by archiving change add-artifact-lineage. Update Purpose after archive.

## Requirements

### Requirement: Content-addressed immutable blobs

The system SHALL store every artifact under its sha256 content hash and MUST NOT overwrite an existing object.

#### Scenario: Storing identical content twice

- **WHEN** two files with identical bytes are stored
- **THEN** both return the same hash and only one object exists in the store

### Requirement: File, directory and value artifacts

The system SHALL support three artifact kinds:

- `file`, a single file
- `dir`, a manifest of relative paths and file hashes, whose hash is the hash of the canonical manifest
- `value`, a pickled Python object

#### Scenario: Directory hash is content-derived

- **WHEN** two directories with the same relative paths and file contents are stored
- **THEN** they receive the same hash regardless of file modification times

#### Scenario: Value round trip

- **WHEN** a node returns a non-path Python value
- **THEN** it is stored as a `value` artifact, and a downstream node receives an equal object

### Requirement: Pluggable backends

The system SHALL provide two backends:

- a local filesystem backend
- an fsspec object-store backend, selected by URL (`file://`, `s3://`, `memory://`, …), which is compatible with S3 and MinIO

#### Scenario: Object store backend

- **WHEN** `DENDRITE_STORE_URL` is an `s3://bucket/prefix` URL
- **THEN** blobs are written to that bucket, and inputs are materialised to a local cache before node execution

### Requirement: Integrity verification

The system SHALL be able to verify that an artifact, including every file of a directory manifest, is present in the store.

#### Scenario: Missing blob detected

- **WHEN** a blob referenced by an artifact is deleted
- **THEN** verification of that artifact returns false
