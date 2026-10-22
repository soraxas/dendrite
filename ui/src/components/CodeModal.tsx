// View or edit a step's source snapshot. Editable only while the file still contains exactly
// what ran; saving is confirmed and guarded server-side by a file-hash check.
import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useState } from "react";
import { ApiError, api, type SourceSnapshot } from "../api";
import { useInfo, useSource } from "../queries";

const AceEditor = lazy(() => import("./AceEditor"));

type Prompt = null | "confirm-save" | "confirm-close" | "conflict";

function statusNote(s: SourceSnapshot, canEdit: boolean): { tone: string; text: string } {
  if (s.status === "missing") return { tone: "failed", text: "The file this step came from no longer exists. Showing the code exactly as it ran." };
  if (s.status === "changed")
    return { tone: "waiting", text: "The file has changed since this ran, so this is a read-only snapshot of the code that produced these results." };
  if (!canEdit) return { tone: "queued", text: "Matches the file on disk. Editing is off — start `dendrite ui` on localhost or with --allow-edit." };
  return { tone: "cached", text: "Matches the file on disk. You can edit it here; saving writes this function back to the file." };
}

export function CodeModal({ hash, step, onClose }: { hash: string; step: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: info } = useInfo();
  const { data: snap, error, refetch } = useSource(hash);
  const [draft, setDraft] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<Prompt>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const editable = !!snap?.editable && !saved;
  const dirty = draft !== null && snap !== undefined && draft !== snap.source;

  const requestClose = () => (dirty && editable ? setPrompt("confirm-close") : onClose());

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") requestClose();
      if ((e.metaKey || e.ctrlKey) && e.key === "s" && editable && dirty) {
        e.preventDefault();
        setPrompt("confirm-save");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  useEffect(() => {
    // Leaving the page with unsaved edits asks first, too.
    if (!dirty || !editable) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, editable]);

  async function save(thenClose: boolean) {
    if (!snap?.file_sha || draft === null) return;
    setSaving(true);
    setFailure(null);
    try {
      await api.saveSource(hash, draft, snap.file_sha);
      setSaved(draft);
      setPrompt(null);
      qc.invalidateQueries({ queryKey: ["source", hash] });
      if (thenClose) onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setPrompt("conflict");
      else setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const note = snap ? statusNote(snap, !!info?.edit) : null;
  const where = snap ? `${snap.display ?? snap.file ?? "unknown file"}${snap.line ? `:${snap.line}` : ""}` : "";

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && requestClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={`Code for ${step}`}>
        <div className="modal-head">
          <div>
            <div className="modal-title">{step} <span className="muted">· code</span></div>
            {snap && <div className="mono muted" title={snap.file ?? undefined}>{where}{snap.qualname ? ` · ${snap.qualname}` : ""}</div>}
          </div>
          <button onClick={requestClose} aria-label="Close">Close</button>
        </div>
        {error && <div className="modal-note failed">{String(error)}</div>}
        {saved ? (
          <div className="modal-note computed">
            Saved to {snap?.display ?? "the file"}. The next run will re-execute <b>{step}</b> and every step that depends on it; until then, results here still come from the old code.
          </div>
        ) : (
          note && <div className={`modal-note ${note.tone}`}>{note.text}</div>
        )}
        {failure && <div className="modal-note failed">Could not save: {failure}</div>}
        <div className="modal-body">
          {snap ? (
            <Suspense fallback={<div className="empty">Loading editor…</div>}>
              <AceEditor value={saved ?? draft ?? snap.source} readOnly={!editable} firstLine={snap.line ?? 1} onChange={setDraft} />
            </Suspense>
          ) : (
            !error && <div className="empty">Loading…</div>
          )}
        </div>
        <div className="modal-foot">
          {prompt === "confirm-save" && (
            <div className="confirm">
              <span>
                Write these changes to <b>{snap?.display}</b>? <b>{step}</b> and everything downstream will re-run next time.
              </span>
              <button onClick={() => setPrompt(null)}>Cancel</button>
              <button className="primary" disabled={saving} onClick={() => save(false)}>{saving ? "Saving…" : "Save to file"}</button>
            </div>
          )}
          {prompt === "confirm-close" && (
            <div className="confirm">
              <span>You have unsaved changes.</span>
              <button onClick={() => setPrompt(null)}>Keep editing</button>
              <button onClick={onClose}>Discard</button>
              <button className="primary" disabled={saving} onClick={() => save(true)}>Save &amp; close</button>
            </div>
          )}
          {prompt === "conflict" && (
            <div className="confirm">
              <span className="err">The file changed on disk since you opened it, so nothing was saved.</span>
              <button onClick={() => (setPrompt(null), setDraft(null), refetch())}>Reload (lose my edits)</button>
            </div>
          )}
          {!prompt && (
            <>
              <span className="muted" style={{ fontSize: 12 }}>
                {editable ? (dirty ? "Unsaved changes · ⌘S to save" : "No changes") : "Read-only"}
              </span>
              <span className="spacer" />
              {editable && dirty && <button onClick={() => setDraft(snap!.source)}>Revert</button>}
              {editable && <button className="primary" disabled={!dirty} onClick={() => setPrompt("confirm-save")}>Save…</button>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
