## 1. Draft lifecycle (core)

- [ ] 1.1 Add `draft=True` to `Lab.create` and `Experiment.fork`, giving status `draft`
- [ ] 1.2 Add a compare-and-set update to `Metadata` (`UPDATE … WHERE id=? AND status=?`) and the `NotADraft` error
- [ ] 1.3 Implement `Experiment.update(params, pin, inputs, name)`, reusing the fork override and pin resolution helpers
- [ ] 1.4 Implement `Experiment.discard()` (drafts only, refused when the draft has children)
- [ ] 1.5 Make `run()` move `draft`/`created` → `running` with a compare-and-set before reading the config
- [ ] 1.6 Tests: fork into a draft, edit, edit a frozen experiment, discard rules, edit racing run

## 2. Run plan (core)

- [ ] 2.1 Add `plan: bool` to `RunState`; add `PlanFuture`, placeholder refs and `PlanIncomplete` (raised by store/`Lab.load` on placeholders)
- [ ] 2.2 Branch `ArtifactTask.__call__`/`submit` to `_plan` before `_enqueue`; unwrap `PlanFuture`s in arguments
- [ ] 2.3 Implement `_plan`: frozen / upstream / signature with `_valid_hit` → reuse or run with a reason (compared with the parent's node_run), plus `missing_blob`
- [ ] 2.4 Estimates: same signature → parent → any execution of the op; `param_schema` from `inspect.signature`, effective and inherited values
- [ ] 2.5 `Experiment.plan()`: call `pipe.fn` directly with `_active_run` set; collect nodes and edges in the DAG shape; `complete`, `blocked_by`, `error`, `env`
- [ ] 2.6 Reload imported pipeline modules whose source mtime changed; keep the old module and report an error if the reload fails
- [ ] 2.7 Tests: an unchanged fork plans all reuse; a param change gives run, reuse upstream and upstream downstream; pin → frozen; missing blob; table counts unchanged and CALLS unchanged, including a `.submit()` pipeline; branching on content → incomplete; code edited on disk → `code`; re-saving params unchanged → all reuse

## 3. API and CLI

- [ ] 3.1 `POST /api/experiments/{id}/fork`, `PATCH /api/experiments/{id}`, `DELETE /api/experiments/{id}`, all gated by `allow_edit`, with 409 for non-drafts
- [ ] 3.2 `GET /api/experiments/{id}/plan`, with 409 `pipeline_not_imported`; `/api/info` gains `plan`
- [ ] 3.3 `dendrite run <experiment> [--import MODULE]`
- [ ] 3.4 API tests: 403 with editing off, 409 for non-drafts, typed values round-trip, plan shape
- [ ] 3.5 Update CLAUDE.md (invariant: frozen at first run; plan mode; `--import` needed for plan) and README

## 4. UI: draft mode

- [ ] 4.1 `api.ts`/`queries.ts`: draft and plan types, mutations, a plan query refetched after each save
- [ ] 4.2 `copy.ts`: `draft` status and the planned-outcome vocabulary; Sidebar outcome "Draft" and a dashed-ring glyph
- [ ] 4.3 "Fork…" in the Toolbar (disabled with a reason when editing is off), opening the new draft
- [ ] 4.4 Draft banner and tint with the copyable `dendrite run` command and Discard
- [ ] 4.5 DagView renders `/plan` for drafts: outcome lines, a dashed style for upstream, legend entries shown only when present, and an incomplete/error notice
- [ ] 4.6 NodeDetail param form (typed inputs, inherited value, changed marker, reset) and a Freeze toggle; debounced PATCH that keeps the previous plan visible
- [ ] 4.7 Toolbar plan summary ("Will run 2 of 4 steps · about 14 min" / "Nothing to run")

## 5. Step contract and read guard

- [x] 5.1 README "The step contract" section (pipeline body vs steps, hidden inputs and their fixes, params as JSON values)
- [ ] 5.2 `guard.py`: install the audit hook once, read the `_reads_guard` contextvar, apply the exclusions (workdir, input checkouts, DENDRITE_HOME, venv/site-packages, module files), report each path once per step
- [ ] 5.3 Set and clear the guard in `task._compute` around the step function; read `DENDRITE_CHECK_READS` (`warn` by default, `error`, `off`) in `Settings`
- [ ] 5.4 Tests: reading an undeclared project file warns; `error` mode fails the execution; declared inputs, workdir files and reads outside the project are silent; `off` is silent
- [ ] 5.5 CLAUDE.md: add the step contract to the invariants and list the guard's limits under known limitations

## 6. Verify

- [ ] 6.1 `npm run build`, then `uv run pytest`
- [ ] 6.2 CDP screenshots: fork → draft mode, a param edit updating the plan, freeze toggle, `dendrite run` turning the draft live
- [ ] 6.3 `openspec validate draft-experiments --strict`
