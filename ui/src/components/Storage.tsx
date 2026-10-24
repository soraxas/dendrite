// Storage: where the space goes, and the actions that reclaim it. Every deletion previews first
// and applies exactly what was previewed; lineage records always stay (see storage.py).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  ApiError,
  api,
  type CacheEntry,
  type ForkMode,
  type Inventory,
  type ReclaimReport,
  type StorageExperiment,
} from "../api";
import { EXPERIMENT_STATUS } from "../copy";
import { fmtAgo, fmtDur, fmtSize, short } from "../format";
import { useStorage } from "../queries";
import type { StorageTab } from "../router";

export const EDIT_OFF = "Editing is off: start `dendrite ui` on localhost or with --allow-edit to delete or clean up.";
const FORCE_RISK = "A step that is running right now may fail to read its inputs. It can't produce a wrong result.";

/** What each deletion reason means, in the order the preview lists them. */
export const REASONS: Record<string, string> = {
  orphan: "Left over from failed or interrupted runs",
  unreferenced: "Used only by deleted experiments or replaced runs",
  "unused-since": "Cache not used within the chosen age",
  evicted: "Outputs of the evicted step",
};

export const SKIPS: Record<string, string> = {
  grace: "written in the last hour (may still be uploading)",
  kept: "used by an experiment marked keep",
  pinned: "frozen into an experiment",
  newest: "used by a pipeline's newest run",
  shared: "also used elsewhere",
  input: "an experiment's input",
};

export function groupReport(r: ReclaimReport): { why: string; label: string; count: number; bytes: number }[] {
  const order = Object.keys(REASONS);
  const rank = (why: string) => (order.includes(why) ? order.indexOf(why) : order.length);
  return Object.entries(r.groups)
    .map(([why, g]) => ({ why, label: REASONS[why] ?? why, ...g }))
    .sort((a, b) => rank(a.why) - rank(b.why));
}

export function countSkips(r: ReclaimReport): { why: string; label: string; count: number }[] {
  const n: Record<string, number> = {};
  for (const s of r.skipped) n[s.why] = (n[s.why] ?? 0) + 1;
  return Object.entries(n).map(([why, count]) => ({ why, label: SKIPS[why] ?? why, count }));
}

/** Every fork below ``id``, recursively: what deleting with forks removes. */
export function descendantsOf(rows: { id: string; parent_id: string | null }[], id: string): string[] {
  return rows.filter((r) => r.parent_id === id).flatMap((r) => [r.id, ...descendantsOf(rows, r.id)]);
}

/** Forks that deleting ``ids`` would leave behind: not selected, but with a selected parent. */
export function orphanedForks(rows: { id: string; parent_id: string | null }[], ids: Set<string>): string[] {
  return rows.filter((r) => !ids.has(r.id) && r.parent_id != null && ids.has(r.parent_id)).map((r) => r.id);
}

/** Experiments to suggest for deletion: finished more than ``days`` ago, not kept, not running. */
export function oldExperiments(rows: StorageExperiment[], days: number, now = Date.now() / 1000): string[] {
  const cutoff = now - days * 86400;
  return rows
    .filter((e) => !e.keep && e.status !== "running" && (e.finished_at ?? e.created_at) < cutoff)
    .map((e) => e.id);
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function useInvalidate() {
  const qc = useQueryClient();
  return () =>
    Promise.all(
      ["storage", "experiments", "dag", "artifact", "lineage", "execution", "estimate"].map((k) =>
        qc.invalidateQueries({ queryKey: [k] }),
      ),
    );
}

/** What deleting ``ids`` frees after clean-up, asked of the server (shared data counted correctly). */
function useEstimate(ids: string[], forks: ForkMode, enabled: boolean) {
  return useQuery({
    queryKey: ["estimate", [...ids].sort().join(","), forks],
    queryFn: () => api.estimate(ids, forks === "refuse" ? "keep" : forks),
    enabled: enabled && ids.length > 0,
  });
}

/** Keep or delete the forks of what's being deleted. */
function ForkChoice({
  forks,
  mode,
  setMode,
  newParent,
  nameOf,
}: {
  forks: string[];
  mode: ForkMode;
  setMode: (m: ForkMode) => void;
  newParent: string | null;
  nameOf: (id: string) => string;
}) {
  if (!forks.length) return null;
  const them = forks.length === 1 ? "it" : "them";
  return (
    <fieldset className="fork-choice">
      <legend>
        {plural(forks.length, "fork")}: {forks.map(nameOf).join(", ")}
      </legend>
      <label>
        <input type="radio" name="forks" checked={mode === "keep"} onChange={() => setMode("keep")} /> Keep {them}
        <span className="muted">
          {" "}
          (
          {newParent
            ? `they become forks of ${nameOf(newParent)}`
            : "each becomes a fork of its nearest ancestor that stays, or top-level"}
          )
        </span>
      </label>
      <label>
        <input type="radio" name="forks" checked={mode === "delete"} onChange={() => setMode("delete")} /> Delete {them}{" "}
        too
      </label>
    </fieldset>
  );
}

/** Delete with an inline confirmation that says what is freed and what happens to forks. */
export function DeleteExperiment({
  exp,
  forks,
  canEdit,
  nameOf,
  onDeleted,
}: {
  exp: Pick<StorageExperiment, "id" | "name" | "status" | "parent_id">;
  forks: string[];
  canEdit: boolean;
  nameOf: (id: string) => string;
  onDeleted: (ids: string[], parent: string | null) => void;
}) {
  const invalidate = useInvalidate();
  const [asking, setAsking] = useState(false);
  const [mode, setMode] = useState<ForkMode>("keep");
  const forkMode: ForkMode = forks.length ? mode : "refuse";
  const est = useEstimate([exp.id], forkMode, asking);
  const del = useMutation({
    mutationFn: () => api.deleteExperiment(exp.id, forkMode),
    onSuccess: async (r) => {
      setAsking(false);
      await invalidate();
      onDeleted(r.ids, r.parent_id);
    },
  });
  const running = exp.status === "running";
  if (!asking)
    return (
      <button
        className="danger"
        disabled={!canEdit || running}
        title={
          !canEdit
            ? EDIT_OFF
            : running
              ? "Can't delete while it runs. If its run crashed, mark it stopped first."
              : undefined
        }
        onClick={() => (del.reset(), setAsking(true))}
      >
        Delete…
      </button>
    );
  const count = forkMode === "delete" ? forks.length + 1 : 1;
  return (
    <div className="confirm storage-confirm" role="alertdialog" aria-label={`Delete ${exp.name || exp.id}`}>
      <span>
        Delete <b>{exp.name || exp.id}</b>? Frees about <b>{est.data ? fmtSize(est.data.bytes) : "…"}</b> after
        clean-up. Results other experiments use stay, and so does the lineage record.
        <ForkChoice forks={forks} mode={mode} setMode={setMode} newParent={exp.parent_id} nameOf={nameOf} />
        {del.error && <span className="err"> {errorText(del.error)}</span>}
      </span>
      <button onClick={() => setAsking(false)}>Cancel</button>
      <button className="danger" disabled={del.isPending} onClick={() => del.mutate()}>
        {del.isPending ? "Deleting…" : count > 1 ? `Delete ${count}` : "Delete"}
      </button>
    </div>
  );
}

/** For an experiment left "running" by a crashed process, which blocks clean-up. */
export function StopButton({ e, canEdit }: { e: Pick<StorageExperiment, "id" | "name">; canEdit: boolean }) {
  const invalidate = useInvalidate();
  const [asking, setAsking] = useState(false);
  const m = useMutation({
    mutationFn: () => api.stop(e.id),
    onSuccess: async () => (setAsking(false), await invalidate()),
  });
  if (!asking)
    return (
      <button
        disabled={!canEdit}
        title={canEdit ? "Use this only if its run crashed or was killed" : EDIT_OFF}
        onClick={() => setAsking(true)}
      >
        Mark {e.name || e.id} stopped…
      </button>
    );
  return (
    <div className="confirm storage-confirm" role="alertdialog" aria-label={`Mark ${e.name || e.id} stopped`}>
      <span>
        Mark <b>{e.name || e.id}</b> as stopped? Do this only if its run is no longer going (the process crashed or was
        killed). It's then shown as failed, and clean-up and delete stop waiting for it.
        {m.error && <span className="err"> {errorText(m.error)}</span>}
      </span>
      <button onClick={() => setAsking(false)}>Cancel</button>
      <button className="danger" disabled={m.isPending} onClick={() => m.mutate()}>
        Mark stopped
      </button>
    </div>
  );
}

function KeepToggle({ e, canEdit }: { e: StorageExperiment; canEdit: boolean }) {
  const invalidate = useInvalidate();
  const m = useMutation({ mutationFn: (keep: boolean) => api.keep(e.id, keep), onSuccess: invalidate });
  return (
    <label
      className="keep"
      title={
        canEdit ? "Kept experiments are spared when cleaning up old cache, and never picked by “select old”." : EDIT_OFF
      }
    >
      <input
        type="checkbox"
        checked={e.keep}
        disabled={!canEdit || m.isPending}
        onChange={(ev) => m.mutate(ev.target.checked)}
      />{" "}
      keep
    </label>
  );
}

/** Delete several experiments at once, with the combined estimate and a fork decision. */
export function BulkDelete({
  rows,
  selected,
  clear,
  nameOf,
}: {
  rows: StorageExperiment[];
  selected: Set<string>;
  clear: () => void;
  nameOf: (id: string) => string;
}) {
  const invalidate = useInvalidate();
  const [mode, setMode] = useState<ForkMode>("keep");
  const [asking, setAsking] = useState(false);
  const ids = [...selected];
  const left = orphanedForks(rows, selected);
  const forkMode: ForkMode = left.length ? mode : "refuse";
  const est = useEstimate(ids, forkMode, true);
  const del = useMutation({
    mutationFn: () => api.deleteMany(ids, forkMode),
    onSuccess: async () => {
      setAsking(false);
      clear();
      await invalidate();
    },
  });
  const extra = forkMode === "delete" && est.data ? est.data.ids.length - ids.length : 0;
  return (
    <div className="bulk-bar" role="region" aria-label="Selected experiments">
      <div className="bulk-row">
        <span>
          <b>{plural(ids.length, "experiment")}</b> selected{extra > 0 ? ` (+${plural(extra, "fork")})` : ""} · frees
          about <b>{est.data ? fmtSize(est.data.bytes) : "…"}</b> after clean-up
        </span>
        <span className="spacer" />
        <button onClick={clear}>Clear selection</button>
        {!asking && (
          <button className="danger" onClick={() => (del.reset(), setAsking(true))}>
            Delete selected…
          </button>
        )}
      </div>
      {asking && (
        <div className="confirm storage-confirm" role="alertdialog" aria-label="Delete selected experiments">
          <span>
            Delete {ids.map(nameOf).join(", ")}? Lineage records stay, and results other experiments use stay.
            <ForkChoice forks={left} mode={mode} setMode={setMode} newParent={null} nameOf={nameOf} />
            {del.error && <span className="err"> {errorText(del.error)}</span>}
          </span>
          <button onClick={() => setAsking(false)}>Cancel</button>
          <button className="danger" disabled={del.isPending} onClick={() => del.mutate()}>
            {del.isPending ? "Deleting…" : `Delete ${ids.length + extra}`}
          </button>
        </div>
      )}
    </div>
  );
}

function ExperimentsTab({
  rows,
  canEdit,
  nameOf,
  openExp,
}: {
  rows: StorageExperiment[];
  canEdit: boolean;
  nameOf: (id: string) => string;
  openExp: (id: string) => void;
}) {
  const [sort, setSort] = useState<"exclusive" | "bytes">("exclusive");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [days, setDays] = useState(30);
  const present = new Set(rows.map((r) => r.id));
  const sel = new Set([...selected].filter((id) => present.has(id))); // drop ones deleted meanwhile
  const sorted = [...rows].sort((a, b) => b[sort] - a[sort]);
  const selectable = rows.filter((r) => r.status !== "running");
  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const th = (key: typeof sort, label: string, hint: string) => (
    <th className="num" title={hint}>
      <a onClick={() => setSort(key)} aria-sort={sort === key ? "descending" : undefined}>
        {label}
        {sort === key ? " ↓" : ""}
      </a>
    </th>
  );
  return (
    <>
      {canEdit && (
        <div className="select-old">
          <span>Select experiments finished more than</span>
          <input
            type="number"
            min={0}
            aria-label="older than days"
            value={days}
            onChange={(e) => setDays(Math.max(0, Number(e.target.value)))}
          />
          <span>days ago</span>
          <button onClick={() => setSelected(new Set(oldExperiments(rows, days)))}>Select old</button>
          <span className="muted">Experiments marked keep are never selected.</span>
        </div>
      )}
      {canEdit && sel.size > 0 && (
        <BulkDelete rows={rows} selected={sel} clear={() => setSelected(new Set())} nameOf={nameOf} />
      )}
      <table className="storage-table">
        <thead>
          <tr>
            {canEdit && (
              <th>
                <input
                  type="checkbox"
                  aria-label="select all"
                  checked={selectable.length > 0 && selectable.every((r) => sel.has(r.id))}
                  onChange={(e) => setSelected(e.target.checked ? new Set(selectable.map((r) => r.id)) : new Set())}
                />
              </th>
            )}
            <th>experiment</th>
            <th>status</th>
            {th("bytes", "size", "Everything it uses, including results shared with other experiments")}
            {th("exclusive", "frees", "Used by no other experiment: deleting it and cleaning up frees this")}
            <th></th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((e) => (
            <tr key={e.id} className={sel.has(e.id) ? "picked" : ""}>
              {canEdit && (
                <td>
                  <input
                    type="checkbox"
                    aria-label={`select ${e.name || e.id}`}
                    disabled={e.status === "running"}
                    checked={sel.has(e.id)}
                    onChange={() => toggle(e.id)}
                  />
                </td>
              )}
              <td>
                <a onClick={() => openExp(e.id)}>{e.name || e.id}</a> <span className="mono muted">{e.id}</span>
                {e.parent_id && <span className="muted"> · fork of {nameOf(e.parent_id)}</span>}
              </td>
              <td>{EXPERIMENT_STATUS[e.status] ?? e.status}</td>
              <td className="num">{fmtSize(e.bytes)}</td>
              <td className="num">
                <b>{fmtSize(e.exclusive)}</b>
              </td>
              <td>
                <KeepToggle e={e} canEdit={canEdit} />
              </td>
              <td className="actions">
                {e.status === "running" ? (
                  <StopButton e={e} canEdit={canEdit} />
                ) : (
                  <DeleteExperiment
                    exp={e}
                    forks={descendantsOf(rows, e.id)}
                    canEdit={canEdit}
                    nameOf={nameOf}
                    onDeleted={() => {}}
                  />
                )}
              </td>
            </tr>
          ))}
          {!sorted.length && (
            <tr>
              <td colSpan={7} className="muted">
                No experiments.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}

/** Evict one step's (or one artifact's) stored outputs after previewing what goes. */
function EvictButton({
  label,
  note,
  present,
  canEdit,
  run,
}: {
  label: string;
  note: string;
  present: boolean;
  canEdit: boolean;
  run: (apply: boolean, force: boolean) => Promise<ReclaimReport>;
}) {
  const invalidate = useInvalidate();
  const [preview, setPreview] = useState<ReclaimReport | null>(null);
  const [force, setForce] = useState(false);
  const ask = useMutation({ mutationFn: () => run(false, false), onSuccess: setPreview });
  const apply = useMutation({
    mutationFn: () => run(true, force),
    onSuccess: async () => {
      setPreview(null);
      await invalidate();
    },
  });
  const refused = apply.error instanceof ApiError && (apply.error.code === "pinned" || apply.error.code === "busy");
  if (!present) return <span className="muted">evicted</span>;
  if (!preview)
    return (
      <>
        <button
          disabled={!canEdit || ask.isPending}
          title={canEdit ? "Delete these stored outputs; they're recomputed when next needed." : EDIT_OFF}
          onClick={() => ask.mutate()}
        >
          Evict…
        </button>
        {ask.error && <div className="err">{errorText(ask.error)}</div>}
      </>
    );
  return (
    <div className="confirm storage-confirm" role="alertdialog" aria-label={`Evict ${label}`}>
      <span>
        Evict {plural(preview.deleted.length, "object")} ({fmtSize(preview.bytes)})? {note}
        {countSkips(preview).map((s) => (
          <span key={s.why} className="muted">
            {" "}
            {s.count} kept: {s.label}.
          </span>
        ))}
        {apply.error && <span className="err"> {errorText(apply.error)}</span>}
      </span>
      {refused && (
        <label title={FORCE_RISK}>
          <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> evict anyway
        </label>
      )}
      <button onClick={() => (setPreview(null), apply.reset(), setForce(false))}>Cancel</button>
      <button className="danger" disabled={apply.isPending || !preview.deleted.length} onClick={() => apply.mutate()}>
        Evict
      </button>
    </div>
  );
}

function CacheTab({ rows, canEdit, nameOf }: { rows: CacheEntry[]; canEdit: boolean; nameOf: (id: string) => string }) {
  return (
    <table className="storage-table">
      <thead>
        <tr>
          <th>step</th>
          <th>first run by</th>
          <th className="num">size</th>
          <th className="num" title="How many experiments reused this result instead of recomputing it">
            reused
          </th>
          <th>last used</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => (
          <tr key={c.execution_id} className={c.live ? "" : "muted"}>
            <td>
              {c.node} <span className="mono muted">{short(c.execution_id, 8)}</span>
              {!c.live && <span className="muted"> · unused</span>}
            </td>
            <td>{c.experiment_id ? nameOf(c.experiment_id) : "—"}</td>
            <td className="num">{fmtSize(c.bytes)}</td>
            <td className="num">{c.reused}×</td>
            <td>{c.last_used ? fmtAgo(c.last_used) : "never"}</td>
            <td className="actions">
              <EvictButton
                label={c.node}
                note={`${c.node} recomputes${c.duration ? ` (took ${fmtDur(c.duration)})` : ""} the next time a run needs it.`}
                present={c.present}
                canEdit={canEdit}
                run={(apply, force) => api.evict(c.execution_id, apply, force)}
              />
            </td>
          </tr>
        ))}
        {!rows.length && (
          <tr>
            <td colSpan={6} className="muted">
              Nothing cached yet.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function LargestTab({
  rows,
  canEdit,
  openArtifact,
}: {
  rows: Inventory["largest"];
  canEdit: boolean;
  openArtifact: (h: string) => void;
}) {
  return (
    <table className="storage-table">
      <thead>
        <tr>
          <th>artifact</th>
          <th>made by</th>
          <th className="num">size</th>
          <th className="num">experiments</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((a) => (
          <tr key={a.hash} className={a.live ? "" : "muted"}>
            <td>
              <a onClick={() => openArtifact(a.hash)}>{a.filename || a.kind}</a>{" "}
              <span className="mono muted">{short(a.hash, 10)}</span>
              {!a.live && <span className="muted"> · unused</span>}
            </td>
            <td>{a.producer_node ?? "input"}</td>
            <td className="num">{fmtSize(a.size)}</td>
            <td className="num">{a.experiments}</td>
            <td className="actions">
              {a.producer_node ? (
                <EvictButton
                  label={a.filename || a.kind}
                  note={`${a.producer_node} recomputes it the next time a run needs it.`}
                  present={a.present}
                  canEdit={canEdit}
                  run={(apply, force) => api.evictArtifact(a.hash, apply, force)}
                />
              ) : (
                <span className="muted" title="Experiment inputs are your data, not cache, so they're never evicted.">
                  input
                </span>
              )}
            </td>
          </tr>
        ))}
        {!rows.length && (
          <tr>
            <td colSpan={5} className="muted">
              No artifacts stored.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

/** Clean-up: options, then a dry-run preview grouped by reason, then confirm exactly that. */
export function CleanupPanel({
  canEdit,
  collectable,
  busy = [],
}: {
  canEdit: boolean;
  collectable: number;
  busy?: string[];
}) {
  const invalidate = useInvalidate();
  const [age, setAge] = useState(false);
  const [days, setDays] = useState(30);
  const [force, setForce] = useState(false);
  const [recent, setRecent] = useState(false);
  const [preview, setPreview] = useState<ReclaimReport | null>(null);
  const [done, setDone] = useState<ReclaimReport | null>(null);
  const older = age ? days : null;
  const opts = { older_than_days: older, ...(recent ? { include_recent: true } : {}) };
  const ask = useMutation({
    mutationFn: (o: typeof opts) => api.gc(o),
    onSuccess: (r) => (setPreview(r), setDone(null)),
  });
  const apply = useMutation({
    mutationFn: () =>
      api.gc({ ...opts, apply: true, hashes: preview!.deleted.map((d) => d.hash), ...(force ? { force: true } : {}) }),
    onSuccess: async (r) => {
      setPreview(null);
      setDone(r);
      setForce(false);
      await invalidate();
    },
  });
  const waiting = busy.length > 0 || (apply.error instanceof ApiError && apply.error.code === "busy");
  return (
    <section className="cleanup" aria-label="Clean up">
      <h3>Clean up</h3>
      <p className="explain">
        Deletes stored data no experiment uses any more ({fmtSize(collectable)} now). Nothing is deleted until you
        confirm the preview, and lineage records stay.
      </p>
      <div className="cleanup-opts">
        <label>
          <input type="checkbox" checked={age} onChange={(e) => (setAge(e.target.checked), setPreview(null))} /> also
          evict cache unused for
        </label>
        <input
          type="number"
          min={0}
          aria-label="days"
          value={days}
          disabled={!age}
          onChange={(e) => (setDays(Math.max(0, Number(e.target.value))), setPreview(null))}
        />
        <span>days</span>
        <span className="muted">(keeps kept experiments, frozen results and each pipeline's newest run)</span>
        <span className="spacer" />
        <button onClick={() => ask.mutate(opts)} disabled={ask.isPending}>
          {ask.isPending ? "Checking…" : "Preview"}
        </button>
      </div>
      {ask.error && <div className="err">{errorText(ask.error)}</div>}
      {preview && (
        <div className="preview">
          {preview.deleted.length ? (
            <table>
              <tbody>
                {groupReport(preview).map((g) => (
                  <tr key={g.why}>
                    <td>{g.label}</td>
                    <td className="num">{plural(g.count, "object")}</td>
                    <td className="num">{fmtSize(g.bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="muted">Nothing to delete.</div>
          )}
          {countSkips(preview).map((s) => (
            <div key={s.why} className="muted">
              Skipping {s.count}: {s.label}
            </div>
          ))}
          {(recent || preview.skipped.some((s) => s.why === "grace")) && (
            <label
              className="force"
              title={busy.length ? "Not while something runs: its uploads may be among them." : undefined}
            >
              <input
                type="checkbox"
                checked={recent}
                disabled={busy.length > 0}
                onChange={(e) => {
                  const on = e.target.checked;
                  setRecent(on);
                  ask.mutate({ older_than_days: older, ...(on ? { include_recent: true } : {}) });
                }}
              />{" "}
              Include data written in the last hour{" "}
              <span className="muted">(safe while nothing is running, e.g. right after deleting experiments)</span>
            </label>
          )}
          {preview.deleted.length > 0 && (
            <div className="confirm">
              <span>
                {waiting && (
                  <label className="force">
                    <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Something is
                    running; clean up anyway
                    <span className="muted"> ({FORCE_RISK})</span>
                  </label>
                )}
              </span>
              <button onClick={() => setPreview(null)}>Cancel</button>
              <button
                className="danger"
                disabled={!canEdit || apply.isPending || (waiting && !force)}
                title={canEdit ? undefined : EDIT_OFF}
                onClick={() => apply.mutate()}
              >
                {apply.isPending
                  ? "Deleting…"
                  : `Delete ${plural(preview.deleted.length, "item")} (${fmtSize(preview.bytes)})`}
              </button>
            </div>
          )}
          {apply.error && <div className="err">{errorText(apply.error)}</div>}
        </div>
      )}
      {done && (
        <div className="ok">
          Deleted {plural(done.deleted.length, "item")}, freed {fmtSize(done.bytes)}.
        </div>
      )}
    </section>
  );
}

function ScratchTile({ bytes, canEdit, busy }: { bytes: number; canEdit: boolean; busy: boolean }) {
  const invalidate = useInvalidate();
  const [asking, setAsking] = useState(false);
  const m = useMutation({
    mutationFn: (force: boolean) => api.clean(force),
    onSuccess: async () => (setAsking(false), await invalidate()),
  });
  return (
    <div
      className="tile"
      title="Local copies of artifacts and leftover step folders. Safe to clear: they're recreated on demand."
    >
      <div className="tile-label">Local scratch</div>
      <div className="tile-value">{fmtSize(bytes)}</div>
      {!asking ? (
        <button
          disabled={!canEdit || m.isPending || !bytes}
          title={canEdit ? undefined : EDIT_OFF}
          onClick={() => (busy ? setAsking(true) : m.mutate(false))}
        >
          {m.isPending ? "Clearing…" : "Clear"}
        </button>
      ) : (
        <div className="tile-ask">
          <span>Something is running. {FORCE_RISK}</span>
          <button onClick={() => setAsking(false)}>Cancel</button>
          <button className="danger" onClick={() => m.mutate(true)}>
            Clear anyway
          </button>
        </div>
      )}
      {m.error && <div className="err">{errorText(m.error)}</div>}
    </div>
  );
}

export function StoragePage({
  tab,
  go,
  canEdit,
  nameOf,
  openExp,
  openArtifact,
}: {
  tab: StorageTab;
  go: (tab: StorageTab) => void;
  canEdit: boolean;
  nameOf: (id: string) => string;
  openExp: (id: string) => void;
  openArtifact: (hash: string) => void;
}) {
  const qc = useQueryClient();
  const { data: inv, error, isFetching } = useStorage();
  if (error) return <div className="empty err">{errorText(error)}</div>;
  if (!inv) return <div className="empty">Measuring storage…</div>;
  const st = inv.store;
  const scratch = Object.values(inv.scratch).reduce((a, b) => a + b, 0);
  const running = inv.experiments.filter((e) => e.status === "running");
  const tile = (label: string, bytes: number, hint: string, sub?: string) => (
    <div className="tile" title={hint}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{fmtSize(bytes)}</div>
      {sub && <div className="muted">{sub}</div>}
    </div>
  );
  const tabBtn = (t: StorageTab, label: string) => (
    <button role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => go(t)}>
      {label}
    </button>
  );
  return (
    <div className="pad storage">
      <div className="tiles">
        {tile("Stored", st.bytes, `Everything in ${st.url}`, plural(st.objects, "object"))}
        {tile("In use", st.live.bytes, "Reached by an existing experiment's inputs, frozen results or step outputs")}
        {tile(
          "Shared cache",
          st.shared.bytes,
          "Used by more than one experiment: counted once, freed only when none use it",
        )}
        {tile(
          "Can be cleaned up",
          st.collectable.bytes,
          "Used by no existing experiment",
          plural(st.collectable.objects, "object"),
        )}
        <ScratchTile bytes={scratch} canEdit={canEdit} busy={inv.busy.length > 0} />
      </div>
      <div className="storage-note muted">
        Measured {fmtAgo(inv.computed_at)}{" "}
        <a onClick={() => qc.fetchQuery({ queryKey: ["storage"], queryFn: () => api.storage(true) })}>
          {isFetching ? "measuring…" : "refresh"}
        </a>
        {!canEdit && <> · {EDIT_OFF}</>}
      </div>
      {inv.busy.length > 0 && (
        <div className="busy-note" role="status">
          <div>
            <b>Clean-up waits while work runs:</b> {inv.busy.join("; ")}.
          </div>
          {running.length > 0 && (
            <div className="busy-actions">
              <span className="muted">If a run crashed or was killed:</span>
              {running.map((e) => (
                <StopButton key={e.id} e={e} canEdit={canEdit} />
              ))}
            </div>
          )}
        </div>
      )}
      <CleanupPanel canEdit={canEdit} collectable={st.collectable.bytes} busy={inv.busy} />
      <div className="tabs" role="tablist" style={{ margin: "18px 0 8px" }}>
        {tabBtn("experiments", "Experiments")}
        {tabBtn("cache", "Cache")}
        {tabBtn("largest", "Largest artifacts")}
      </div>
      {tab === "experiments" && (
        <ExperimentsTab rows={inv.experiments} canEdit={canEdit} nameOf={nameOf} openExp={openExp} />
      )}
      {tab === "cache" && <CacheTab rows={inv.cache} canEdit={canEdit} nameOf={nameOf} />}
      {tab === "largest" && <LargestTab rows={inv.largest} canEdit={canEdit} openArtifact={openArtifact} />}
    </div>
  );
}
