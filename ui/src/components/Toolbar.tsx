import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Dag, type Plan, type TreeEntry } from "../api";
import { EXPERIMENT_STATUS } from "../copy";
import { fmtVal } from "../format";
import { useDiff } from "../queries";
import type { Route } from "../router";
import { planSummary } from "./Draft";

const statusTone = (s: string) => (s === "completed" ? "computed" : s === "created" ? "queued" : s);

/** "Fork…": a draft child with the same settings, opened straight away for editing. */
function ForkButton({ dag, canEdit, open }: { dag: Dag; canEdit: boolean; open: (id: string) => void }) {
  const qc = useQueryClient();
  const exp = dag.experiment;
  const fork = useMutation({
    mutationFn: () => api.fork(exp.id, `${exp.name || exp.id}-draft`),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["experiments"] });
      open(d.id);
    },
  });
  const err = fork.error ? (fork.error as Error).message : "";
  return (
    <button
      className={`primary ${err ? "err" : ""}`}
      aria-invalid={!!err}
      disabled={!canEdit || fork.isPending}
      title={err ? `Fork failed: ${err}` : canEdit ? "Copy these settings into a draft you can change before running" : "Editing is off: start `dendrite ui` on localhost or with --allow-edit"}
      onClick={() => fork.mutate()}
    >
      {fork.isPending ? "Forking…" : err ? "Fork failed — retry" : "Fork…"}
    </button>
  );
}

/** "Changed iterations 30000 → 60000 · reused 2 of 4 steps" */
function Story({ dag, nameOf }: { dag: Dag; nameOf: (id: string) => string }) {
  const e = dag.experiment;
  const { data: diff } = useDiff(e.parent_id ? e.id : undefined, e.parent_id ?? undefined);
  const steps = dag.nodes.filter((n) => n.kind === "node");
  const reused = steps.filter((n) => n.status === "cached" || n.status === "pinned");
  const parts: string[] = [];
  if (e.parent_id && diff) {
    const changes = diff.params.map((p) => `${p.key} ${p.b == null ? "default" : fmtVal(p.b)} → ${p.a == null ? "default" : fmtVal(p.a)}`);
    const pins = Object.keys(e.pins ?? {});
    if (changes.length) parts.push(`Changed ${changes.slice(0, 2).join(", ")}${changes.length > 2 ? ` +${changes.length - 2} more` : ""}`);
    if (pins.length) parts.push(`froze ${pins.join(", ")}`);
    if (!changes.length && !pins.length) parts.push(`Same settings as ${nameOf(e.parent_id)}`);
  }
  if (steps.length && e.status !== "created") {
    parts.push(reused.length ? `reused ${reused.length} of ${steps.length} steps` : `ran all ${steps.length} steps`);
  }
  if (!parts.length) return null;
  const text = parts.join(" · ");
  return <div className="story">{text.charAt(0).toUpperCase() + text.slice(1)}</div>;
}

export function Toolbar({ dag, tree, route, go, nameOf, canEdit, plan }: {
  dag: Dag;
  tree: TreeEntry[];
  route: Route;
  go: (r: Route) => void;
  nameOf: (id: string) => string;
  canEdit: boolean;
  plan?: Plan;
}) {
  const exp = dag.experiment;
  const draft = exp.status === "draft";
  const others = tree.filter((e) => e.id !== exp.id);
  const [other, setOther] = useState<string>(route.other || exp.parent_id || others[0]?.id || "");
  const pick = others.some((o) => o.id === other) ? other : others[0]?.id ?? "";
  const tab = (view: Route["view"], label: string) => (
    <button role="tab" aria-selected={route.view === view} className={route.view === view ? "active" : ""} onClick={() => go({ view, exp: exp.id })}>
      {label}
    </button>
  );
  return (
    <div className="toolbar">
      <div className="tb-title">
        <div className="tb-row">
          <h2>{exp.name || exp.id}</h2>
          {!draft && <span className="badge" style={{ color: `var(--${statusTone(exp.status)})` }}>{EXPERIMENT_STATUS[exp.status] ?? exp.status}</span>}
          {exp.parent_id && (
            <span className="muted">
              fork of <a onClick={() => go({ view: "dag", exp: exp.parent_id! })}>{nameOf(exp.parent_id)}</a>
            </span>
          )}
        </div>
        {draft ? <div className="story">{plan ? planSummary(plan) : "Preview unavailable"}</div> : <Story dag={dag} nameOf={nameOf} />}
      </div>
      <span className="spacer" />
      {!draft && <ForkButton dag={dag} canEdit={canEdit} open={(id) => go({ view: "dag", exp: id })} />}
      <div className="tabs" role="tablist">
        {tab("dag", "Pipeline")}
        {tab("params", "Parameters")}
        {tab("metrics", "Metrics")}
      </div>
      {others.length > 0 && (
        <label className="compare">
          <span className="muted">Compare with</span>
          <select value={pick} onChange={(e) => setOther(e.target.value)}>
            {others.map((o) => <option key={o.id} value={o.id}>{o.name || o.id}{others.filter((x) => x.name === o.name).length > 1 ? ` (${o.id})` : ""}</option>)}
          </select>
          <button className={route.view === "diff" ? "active" : ""} onClick={() => go({ view: "diff", exp: exp.id, other: pick })}>Compare</button>
        </label>
      )}
    </div>
  );
}
