// Artifact names: a ★ control to name an artifact, and the Names page listing every name.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, type ArtifactName, api, direct, type NameRequest } from "../api";
import { fmtSize, fmtTime, short } from "../format";
import { useNames } from "../queries";
import { ArtifactIcon } from "./ArtifactIcon";

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

/** A suggestion for a new name: ``<node>-<output>``, cleaned to the allowed characters. */
export function suggestName(node?: string | null, output?: string | null, filename?: string | null): string {
  const raw = [node, output].filter(Boolean).join("-") || (filename ?? "").replace(/\.[^.]+$/, "") || "artifact";
  const s = raw
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[^A-Za-z0-9]+|-+$/g, "")
    .slice(0, 100);
  return s || "artifact";
}

function useRefreshNames() {
  const qc = useQueryClient();
  return () => {
    for (const k of ["names", "dag", "artifact", "lineage", "storage"]) qc.invalidateQueries({ queryKey: [k] });
  };
}

/** Names on an artifact, and (when editing is allowed) a ★ button to add one. */
export function NameControl({
  hash,
  names,
  source,
  filename,
  canEdit,
}: {
  hash: string;
  names: string[];
  source?: { experiment?: string; node?: string | null; output?: string | null };
  filename?: string | null;
  canEdit: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [taken, setTaken] = useState<string | null>(null); // a name pointing elsewhere: offer to move it
  const [busy, setBusy] = useState(false);
  const refresh = useRefreshNames();

  const start = () => {
    setName(suggestName(source?.node, source?.output, filename));
    setNote("");
    setError(null);
    setTaken(null);
    setOpen(true);
  };
  const save = async (move = false) => {
    if (!NAME_RE.test(name)) {
      setError("Use letters, digits, '.', '_' or '-', starting with a letter or digit.");
      return;
    }
    const req: NameRequest =
      source?.experiment && source.node
        ? { experiment: source.experiment, node: source.node, output: source.output ?? undefined }
        : source?.experiment && source.output
          ? { experiment: source.experiment, output: source.output }
          : { hash };
    if (note) req.note = note;
    if (move) {
      req.move = true;
      if (taken) req.expect = taken;
    }
    setBusy(true);
    try {
      await api.setName(name, req);
      setOpen(false);
      refresh();
    } catch (e) {
      if (e instanceof ApiError && e.code === "name_taken") {
        const cur = await api.nameHistory(name).catch(() => null);
        setTaken(cur?.name?.hash ?? null);
        setError(`@${name} already names another artifact${cur?.name ? ` (${short(cur.name.hash)})` : ""}.`);
      } else if (e instanceof ApiError && e.code === "name_moved") {
        setTaken(null);
        setError(`@${name} was just changed by someone else; check the Names page.`);
      } else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (n: string) => {
    if (
      !window.confirm(
        `Remove the name @${n}? The artifact stays; garbage collection may reclaim it if nothing else uses it.`,
      )
    )
      return;
    await api.removeName(n, hash).catch((e) => window.alert(e instanceof Error ? e.message : String(e)));
    refresh();
  };

  return (
    <div className="names">
      {names.map((n) => (
        <span
          key={n}
          className="name-chip"
          title="Use it as @name in code, inputs and pins; kept by garbage collection"
        >
          ★ @{n}
          {canEdit && (
            <button type="button" className="link" aria-label={`Remove @${n}`} onClick={() => remove(n)}>
              ×
            </button>
          )}
        </span>
      ))}
      {canEdit && !open && (
        <button
          type="button"
          onClick={start}
          title="Give this artifact a name, usable as @name; named artifacts are kept"
        >
          ☆ Name…
        </button>
      )}
      {open && (
        <form
          className="name-form"
          onSubmit={(e) => {
            e.preventDefault();
            save(false);
          }}
        >
          <label>
            <span className="muted">@</span>
            <input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </label>
          <input
            aria-label="Note"
            placeholder="note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="name-actions">
            <button type="submit" className="primary" disabled={busy}>
              Save
            </button>
            {taken && (
              <button type="button" onClick={() => save(true)} disabled={busy}>
                Move it here
              </button>
            )}
            <button type="button" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
          {error && <div className="err">{error}</div>}
        </form>
      )}
    </div>
  );
}

function History({ name }: { name: string }) {
  const { data } = useQuery({ queryKey: ["name-history", name], queryFn: () => api.nameHistory(name) });
  if (!data) return <div className="muted">Loading…</div>;
  const rows = data.history;
  if (!rows.length) return <div className="muted">Never moved.</div>;
  return (
    <ul className="name-history">
      {rows.map((h) => (
        <li key={h.id}>
          <span className="mono">{short(h.hash)}</span> <span className="muted">{h.action}</span> {fmtTime(h.ended_at)}
          {h.ended_by ? ` by ${h.ended_by}` : ""}
        </li>
      ))}
    </ul>
  );
}

const THUMB_MAX_BYTES = 2 * 1024 * 1024; // larger images show an icon, so the list never pulls big blobs
const isImage = (n: ArtifactName) =>
  (n.size ?? Infinity) <= THUMB_MAX_BYTES && /\.(png|jpe?g|gif|webp|svg)$/i.test(n.filename ?? "");

/** Every name in the lab or project. */
export function NamesPage({
  canEdit,
  nameOf,
  openArtifact,
  openExp,
}: {
  canEdit: boolean;
  nameOf: (id: string) => string;
  openArtifact: (hash: string, exp?: string | null) => void;
  openExp: (id: string) => void;
}) {
  const { data, error } = useNames();
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState<string | null>(null);
  const refresh = useRefreshNames();
  if (error) return <div className="pad err">{String(error)}</div>;
  if (!data) return <div className="pad muted">Loading…</div>;
  const q = query.toLowerCase();
  const rows = data.names.filter(
    (n) => !q || [n.name, n.note, n.node, n.output, n.set_by].some((v) => v?.toLowerCase().includes(q)),
  );
  if (!data.names.length)
    return (
      <div className="pad">
        <p className="muted">
          No names yet. Select a step output and press <b>☆ Name…</b>, or run{" "}
          <code>dendrite name best-splat EXP:train.model</code>. Use names as <code>@best-splat</code> in{" "}
          <code>lab.load</code>, experiment inputs and pins; named artifacts are kept by garbage collection.
        </p>
      </div>
    );
  return (
    <div className="pad">
      <input
        type="search"
        className="item-search"
        placeholder="Find a name, note, step or person"
        aria-label="Find a name"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        style={{ maxWidth: 360, marginBottom: 12 }}
      />
      <table className="names-table">
        <thead>
          <tr>
            <th />
            <th>name</th>
            <th>artifact</th>
            <th className="num">size</th>
            <th>from</th>
            <th>note</th>
            <th>set</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((n) => (
            <tr key={n.name}>
              <td className="thumb">
                {isImage(n) ? (
                  <img src={api.downloadUrl(n.hash)} alt="" loading="lazy" />
                ) : (
                  <ArtifactIcon kind={(n.kind ?? "file") as "file" | "dir" | "value"} filename={n.filename} />
                )}
              </td>
              <td>
                <a className="mono" onClick={() => openArtifact(n.hash, n.experiment_id)}>
                  @{n.name}
                </a>
              </td>
              <td className="mono muted">
                {n.filename ?? n.kind ?? ""} {short(n.hash, 8)}
              </td>
              <td className="num">{fmtSize(n.size)}</td>
              <td>
                {n.experiment_id ? (
                  <>
                    <a onClick={() => openExp(n.experiment_id!)}>{nameOf(n.experiment_id)}</a>
                    {n.node || n.output ? (
                      <span className="mono muted">
                        {" "}
                        {n.node ? `${n.node}.` : ""}
                        {n.output}
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td>{n.note ?? ""}</td>
              <td className="muted">
                {fmtTime(n.set_at)}
                {n.set_by ? ` · ${n.set_by}` : ""}
                <div>
                  <a onClick={() => setShown(shown === n.name ? null : n.name)}>
                    {shown === n.name ? "hide history" : "history"}
                  </a>
                </div>
                {shown === n.name && <History name={n.name} />}
              </td>
              <td>
                {n.kind === "file" && (
                  <a href={direct(api.downloadUrl(n.hash))}>
                    <button type="button">Download</button>
                  </a>
                )}{" "}
                {canEdit && (
                  <button
                    type="button"
                    onClick={async () => {
                      if (!window.confirm(`Remove the name @${n.name}? The artifact stays.`)) return;
                      await api.removeName(n.name, n.hash).catch((e) => window.alert(String(e)));
                      refresh();
                    }}
                  >
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
