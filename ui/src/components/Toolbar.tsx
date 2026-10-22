import { useState } from "react";
import type { ExperimentSummary, TreeEntry } from "../api";
import type { Route } from "../router";
import { expColor } from "./Sidebar";

export function Toolbar({ exp, tree, route, go, nameOf }: { exp: ExperimentSummary; tree: TreeEntry[]; route: Route; go: (r: Route) => void; nameOf: (id: string) => string }) {
  const others = tree.filter((e) => e.id !== exp.id);
  const [other, setOther] = useState<string>(route.other || exp.parent_id || others[0]?.id || "");
  const pick = others.some((o) => o.id === other) ? other : others[0]?.id ?? "";
  const tab = (view: Route["view"], label: string) => (
    <button className={route.view === view ? "active" : ""} onClick={() => go({ view, exp: exp.id })}>{label}</button>
  );
  return (
    <div className="toolbar">
      <h2>{exp.name || exp.id}</h2>
      <span className="badge" style={{ color: `var(--${expColor(exp.status)})` }}>{exp.status}</span>
      <span className="mono muted">{exp.id}</span>
      {exp.parent_id && (
        <span className="muted">forked from <a onClick={() => go({ view: "dag", exp: exp.parent_id! })}>{nameOf(exp.parent_id)}</a></span>
      )}
      <span className="spacer" />
      {tab("dag", "DAG")}
      {tab("params", "Params")}
      {tab("metrics", "Metrics")}
      {others.length > 0 && (
        <span style={{ display: "inline-flex", gap: 4 }}>
          <select value={pick} onChange={(e) => setOther(e.target.value)} aria-label="Compare with">
            {others.map((o) => <option key={o.id} value={o.id}>{o.name || o.id} ({o.id})</option>)}
          </select>
          <button className={route.view === "diff" ? "active" : ""} onClick={() => go({ view: "diff", exp: exp.id, other: pick })}>Compare</button>
        </span>
      )}
    </div>
  );
}
