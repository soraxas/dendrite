import { useState } from "react";
import { RUN_STATUSES, type TreeEntry } from "../api";
import { EXPERIMENT_STATUS, STATUS } from "../copy";
import { fmtAgo, fmtTime } from "../format";

/** Plain-language outcome, e.g. "Reused 2 of 4 steps" — the number users care about. */
export function outcome(e: TreeEntry): string {
  const c = e.counts;
  const total = Object.values(c).reduce((a, b) => a + (b ?? 0), 0);
  const reused = (c.cached ?? 0) + (c.pinned ?? 0);
  const done = reused + (c.computed ?? 0);
  if (e.status === "created") return "Not run yet";
  if (e.status === "failed") return `Failed after ${done} of ${total} steps`;
  if (e.status === "running") return `Running · ${done} of ${total} done`;
  if (!total) return "No steps";
  return reused ? `Reused ${reused} of ${total} steps` : `Ran all ${total} steps`;
}

/** When it last ran (or was created, if never run), for the right edge of the row. */
function lastRun(e: TreeEntry): { text: string; title: string } {
  if (e.status === "running") return { text: "now", title: `started ${fmtTime(e.started_at)}` };
  const t = e.finished_at ?? e.started_at;
  if (t) return { text: fmtAgo(t), title: `last ran ${fmtTime(t)}` };
  return { text: fmtAgo(e.created_at), title: `created ${fmtTime(e.created_at)}, never run` };
}

function Glyph({ status }: { status: TreeEntry["status"] }) {
  // Only states that need attention get a mark; "done" is the quiet default. It sits in the
  // row's left padding so it never pushes the text (or the indentation) right.
  if (status === "completed") return null;
  return <span className={`glyph ${status}`} title={EXPERIMENT_STATUS[status]} aria-label={EXPERIMENT_STATUS[status]} />;
}

/** Step outcomes at a glance, coloured like the graph legend; the tooltip spells it out. */
function OutcomeBar({ e }: { e: TreeEntry }) {
  const parts = RUN_STATUSES.filter((s) => e.counts[s]);
  const total = parts.reduce((a, s) => a + (e.counts[s] ?? 0), 0);
  if (!total) return null;
  const label = parts.map((s) => `${e.counts[s]} ${STATUS[s].label.toLowerCase()}`).join(" · ");
  return (
    <div className="bar" title={label} aria-label={label}>
      {parts.map((s) => (
        <span key={s} style={{ width: `${(100 * (e.counts[s] ?? 0)) / total}%`, background: `var(--${s})` }} />
      ))}
    </div>
  );
}

interface Props {
  tree: TreeEntry[];
  selected?: string;
  onOpen: (id: string) => void;
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ tree, selected, onOpen, collapsed, onToggle }: Props) {
  const [filter, setFilter] = useState("");
  if (collapsed) {
    return (
      <aside className="sidebar collapsed">
        <button className="rail-toggle" onClick={onToggle} title="Show experiments" aria-label="Show experiments">»</button>
        <div className="rail-label">Experiments · {tree.length}</div>
      </aside>
    );
  }
  const ids = new Set(tree.map((e) => e.id));
  const byParent = new Map<string | null, TreeEntry[]>();
  for (const e of tree) {
    const p = e.parent_id && ids.has(e.parent_id) ? e.parent_id : null;
    byParent.set(p, [...(byParent.get(p) ?? []), e]);
  }
  const f = filter.toLowerCase();
  const matches = (e: TreeEntry): boolean =>
    !f || (e.name ?? "").toLowerCase().includes(f) || e.id.includes(f) || (byParent.get(e.id) ?? []).some(matches);

  const item = (e: TreeEntry) => {
    const kids = (byParent.get(e.id) ?? []).filter(matches);
    const when = lastRun(e);
    return (
      <li key={e.id}>
        <div
          className={`exp ${e.id === selected ? "sel" : ""}`}
          onClick={() => onOpen(e.id)}
          role="button"
          tabIndex={0}
          title={`${e.name || e.id} (${e.id}) · ${when.title}`}
          onKeyDown={(k) => k.key === "Enter" && onOpen(e.id)}
        >
          <Glyph status={e.status} />
          <div className="txt">
            <div className="nm-row">
              <span className="nm">{e.name || e.id}</span>
              <span className="when">{when.text}</span>
            </div>
            <div className="sub">{outcome(e)}</div>
            <OutcomeBar e={e} />
          </div>
        </div>
        {kids.length > 0 && <ul className="tree children">{kids.map(item)}</ul>}
      </li>
    );
  };

  const roots = (byParent.get(null) ?? []).filter(matches).slice().reverse();
  return (
    <aside className="sidebar">
      <div className="side-head">
        <div className="side-title">
          <span>
            Experiments <span className="muted">{tree.length}</span>{" "}
            <span className="info" title="Indented experiments are forks of the one above. Newest first.">ⓘ</span>
          </span>
          <button className="rail-toggle" onClick={onToggle} title="Hide experiments" aria-label="Hide experiments">«</button>
        </div>
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter…" aria-label="Filter experiments" />
      </div>
      <ul className="tree">{roots.length ? roots.map(item) : <li className="empty">No experiments yet.</li>}</ul>
    </aside>
  );
}
