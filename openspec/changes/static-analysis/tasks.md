## 1. Analysis core

- [x] 1.1 `analysis.py`: `Diagnostic`, source retrieval with a per-hash cache, and the `StepDefinitionWarning` category
- [x] 1.2 Step checks: `return-shape`, `no-return`, `bad-output-name`, `default-not-json`, and the hidden-input heuristics; `ignore=` on `artifact_task`
- [x] 1.3 Run step checks when the decorator runs (warnings only, skipped when the source is unavailable)
- [x] 1.4 `pipeline_shape(source, resolve)`: inputs, step calls (`submit`, `with_options(name=)`, `.result()`), def-use edges, conditional/repeated context, dynamic nodes, one-level helper expansion
- [x] 1.5 Body diagnostics: `unpack-mismatch`, `unknown-arg`, `missing-arg`, `duplicate-call` (aware of exclusive branches), `call-in-loop`, `undefined-name`, `syntax`
- [x] 1.6 `Pipeline.shape()` and `Pipeline.source_snapshot()`; `Experiment.check()` (`unknown-step`, `unknown-param`)
- [x] 1.7 Tests for every diagnostic code, both positive and negative, plus the shape of `examples/recon.py` and a conditional/loop/dynamic fixture

## 2. Typed declarations

- [x] 2.1 Infer outputs from a `NamedTuple`/`TypedDict` return annotation; normalise NamedTuple returns; `unresolved-return-type`
- [x] 2.2 `ArtifactTask.override(**params)` with close-match suggestions
- [x] 2.3 Tests: NamedTuple and TypedDict steps run and store every output; override typos

## 3. API and CLI

- [x] 3.1 `db.save_snapshot`; `GET`/`POST /api/pipelines/{name}/shape` (snapshot hash, 404 hint); `GET /api/experiments/{id}/check`
- [x] 3.2 `dendrite check [--import] [--ignore]` with exit status
- [x] 3.3 API and CLI tests (posting unsaved text leaves the file untouched; syntax errors; exit codes)

## 4. UI: Code mode

- [x] 4.1 API types and queries for the shape, analysis of edited text, and check
- [x] 4.2 Graph | Code toggle in the Pipeline view; editor with debounced re-analysis; Ace gutter annotations
- [x] 4.3 Shape graph rendering: conditional dashed with an `if` chip, `⟳` for repeated, dynamic box; "from the source" note; plan tones for drafts
- [x] 4.4 Diagnostics list in the side panel, jumping to the line; Save through `saveSource`, with the confirm, conflict and unsaved-changes guards
- [x] 4.5 Vitest tests for annotation mapping, shape-node rendering and the save/unsaved guards

## 5. Docs and verification

- [x] 5.1 README (checks, `override`, NamedTuple outputs, Code mode) and CLAUDE.md (`analysis.py`, invariants)
- [x] 5.2 `uv run pytest`, `npm test`, `npm run build`, CDP screenshots of Code mode, `openspec validate static-analysis --strict`
