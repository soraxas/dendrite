## ADDED Requirements

### Requirement: Outputs from the return annotation

When `outputs=` is omitted, the system SHALL derive output names from a `NamedTuple` or `TypedDict` return annotation, in declaration order. If the annotation can't be resolved, outputs SHALL default to `("output",)` with an `unresolved-return-type` warning.

#### Scenario: NamedTuple return

- **WHEN** a step is annotated `-> Colmap`, where `class Colmap(NamedTuple): cameras: Path; sparse: Path`
- **THEN** its outputs are `("cameras", "sparse")`, and returning `Colmap(cams, sparse)` stores both

### Requirement: Validated param overrides

`step.override(**params)` SHALL return `{"<step>.<param>": value}` for use in `fork`, `update` and `create`. It SHALL raise `TypeError` for unknown parameter names, with a suggestion when a close match exists. Whether a parameter is an artifact input is only known when the step is called, so that case is still rejected at execution.

#### Scenario: Typo caught before running

- **WHEN** `train.override(iteratons=5)` is called
- **THEN** a `TypeError` mentions `iterations` as the likely intended name
