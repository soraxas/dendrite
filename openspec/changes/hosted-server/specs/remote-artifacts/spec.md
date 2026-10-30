## ADDED Requirements

### Requirement: Brokered blob access

In hosted mode, clients SHALL read and write blobs directly in the bucket, through URLs the server signs. Blob bytes SHALL NOT pass through the server, except while the server verifies a staged upload. URLs SHALL be confined to the caller's project prefix and SHALL expire within 15 minutes.

#### Scenario: Cache-hit input downloads from the bucket

- **WHEN** a step's input was computed earlier by another project member, and isn't in the local checkout cache
- **THEN** the client fetches it with a presigned URL straight from the bucket, and stores it in the local checkout cache

#### Scenario: Already-cached input

- **WHEN** the input is already in the local checkout cache
- **THEN** no URL is requested, and nothing is downloaded

### Requirement: Upload only what is missing

Before uploading, the client SHALL ask the server, in batches, which hashes the project lacks. It SHALL upload only those.

#### Scenario: Re-running a forked experiment

- **WHEN** a fork produces outputs identical to blobs already in the project
- **THEN** no bytes are uploaded for them

### Requirement: Committed uploads

A blob SHALL become usable only after the server commits it.

- **Uploads:** every upload SHALL be conditional, so an existing object is never overwritten. It SHALL carry the strongest checksum the store can enforce: SHA-256 for single-part S3 uploads, SHA-256 per part for S3 multipart, and CRC32C on GCS.
- **Commit:** the server SHALL confirm the object exists with the declared size, and with the stored checksum where one is kept.
- **With `verify_uploads = "hash"`:** any upload whose SHA-256 the store has not enforced SHALL go to a staging key. The server SHALL hash it before moving it to its content address.
- **Publish** SHALL refuse outputs that are not committed.

#### Scenario: Mismatched bytes are rejected

- **WHEN** `verify_uploads = "hash"` and a client uploads bytes that don't hash to the declared hash
- **THEN** the commit fails, nothing is stored under that hash, and publishing an execution that names it is refused

#### Scenario: Large output

- **WHEN** a step produces a 12 GB file
- **THEN** it is uploaded in parts with per-part checksums, and committed under its hash before publish succeeds; with `verify_uploads = "hash"`, the server also hashes it from a staging key first

#### Scenario: Existing blobs are not uploaded

- **WHEN** a step's 12 GB output is byte-identical to a blob the project already has
- **THEN** nothing is uploaded, at any size

### Requirement: Batched presence checks

Verifying a cache hit SHALL check blob presence in batches. A directory manifest of N members SHALL take no more than ceil(N/1000) presence requests.

#### Scenario: Large directory hit

- **WHEN** a cache hit has a 5000-file directory output
- **THEN** its verification makes at most 5 presence requests

### Requirement: Values are never unpickled by the server

The hosted server SHALL NOT unpickle value artifacts. The producing client SHALL record a truncated text preview of each value at publish time, and the UI SHALL show that text.

#### Scenario: Value preview in hosted mode

- **WHEN** a viewer opens a value artifact in the hosted UI
- **THEN** they see the producer-recorded preview text, and the server process never unpickles the blob
