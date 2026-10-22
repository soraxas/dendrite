import { useState } from "react";
import { RUN_STATUSES, type TreeEntry } from "../api";

const expColor = (s: string) => (s === "completed" ? "computed" : s === "created" ? "queued" : s);

export function Sidebar({ tree, selected, onOpen }: { tree: TreeEntry[]; selected?: string; onOpen: (id: string) => void }) {
  const [filter, setFilter] = useState("");
  const ids = new Set(tree.map((e) => e.id));
  const byParent = new Map<string | null, TreeEntry[]>();
  for (const e of tree) {
    const p = e.parent_id && ids.has(e.parent_id) ? e.parent_id : null;
    byParent.set(p, [...(byParent.get(p) ?? []), e]);
  }
  const f = filter.toLowerCase();
  const matches = (e: TreeEntry): boolean =>
    !f || (e.name ?? "").toLowerCase().includes(f) || e.id.includes(f) || (byParent.get(e.id) ?? []).some(matches);

  const Item = ({ e }: { e: TreeEntry }) => {
    const total = Object.values(e.counts).reduce((a, b) => a + (b ?? 0), 0) || 1;
    const kids = (byParent.get(e.id) ?? []).filter(matches);
    return (
      <li>
        <div className={`exp ${e.id === selected ? "sel" : ""}`} onClick={() => onOpen(e.id)} role="button" tabIndex={0} onKeyDown={(k) => k.key === "Enter" && onOpen(e.id)}>
          <span className="dot" style={{ background: `var(--${expColor(e.status)})` }} />
          <div className="txt">
            <div className="nm">{e.name || e.id}</div>
            <div className="sub">{e.id} · {e.status}</div>
            <div className="bar">
              {RUN_STATUSES.filter((s) => e.counts[s]).map((s) => (
                <span key={s} title={`${e.counts[s]} ${s}`} style={{ width: `${(100 * (e.counts[s] ?? 0)) / total}%`, background: `var(--${s})` }} />
              ))}
            </div>
          </div>
        </div>
        {kids.length > 0 && (
          <ul className="tree children">
            {kids.map((k) => <Item key={k.id} e={k} />)}
          </ul>
        )}
      </li>
    );
  };

  const roots = (byParent.get(null) ?? []).filter(matches).slice().reverse();
  return (
    <aside className="sidebar">
      <div className="side-head">
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter experiments…" aria-label="Filter experiments" />
      </div>
      <ul className="tree">
        {roots.length ? roots.map((e) => <Item key={e.id} e={e} />) : <li className="empty">No experiments yet.</li>}
      </ul>
    </aside>
  );
}

export { expColor };
