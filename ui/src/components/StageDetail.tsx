// Stage details and editing. Edits go to the pipeline's UI view (`/api/pipelines/{name}/view`):
// labels, descriptions, colours and steps assigned to stages. They never change code or caching.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type DagNode, type PipelineView, type PlanNode } from "../api";
import { PLAN, statusLabel } from "../copy";
import { summarize, summarizePlan } from "../fanout";
import { fmtDur } from "../format";
import { STAGE_COLORS, viewAsCode, type StageGroup } from "../stages";

const EDIT_OFF = "Editing is off: start `dendrite ui` on localhost or with --allow-edit to change stages.";

function useSaveView(pipeline: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (view: PipelineView) => api.saveView(pipeline, view),
    onSuccess: (view) => qc.setQueryData(["view", pipeline], view),
  });
}

/** Only this stage's UI assignments, as code to paste. */
export function stageCode(view: PipelineView, stage: string): string {
  return viewAsCode({ assign: Object.fromEntries(Object.entries(view.assign).filter(([, s]) => s === stage)), styles: view.styles });
}

export function StageDetail({ stage, view, pipeline, canEdit, collapsed, onToggle, onOpen }: {
  stage: StageGroup;
  view: PipelineView;
  pipeline: string;
  canEdit: boolean;
  collapsed: boolean;
  onToggle: () => void;
  onOpen: (node: string) => void;
}) {
  const key = stage.path[stage.path.length - 1]; // the name the code (or view) uses
  const style = view.styles[key] ?? {};
  const [label, setLabel] = useState(style.label ?? stage.name);
  const [description, setDescription] = useState(style.description ?? stage.description ?? "");
  const [color, setColor] = useState(stage.color);
  const save = useSaveView(pipeline);
  const planned = stage.members.some((m) => (m as PlanNode).plan);
  const s = planned ? summarizePlan(stage.members as PlanNode[]) : summarize(stage.members);
  const assigned = Object.entries(view.assign).filter(([, st]) => st === key).map(([step]) => step);
  const dirty = label !== (style.label ?? stage.name) || description !== (style.description ?? stage.description ?? "") || color !== stage.color;
  const commit = (next: Partial<PipelineView>) => save.mutate({ assign: next.assign ?? view.assign, styles: next.styles ?? view.styles });
  return (
    <>
      <div className="selected" aria-live="polite">
        <span className="muted">Selected</span> · <span>stage</span> · <b>{stage.name}</b>
      </div>
      <section>
        <dl className="kv">
          <dt>steps</dt><dd>{s.line2 || "—"}</dd>
          {s.line3 && (<><dt>time</dt><dd>{s.line3}</dd></>)}
          <dt>defined</dt><dd>{stage.fromView ? "in the UI (this pipeline's view)" : <>in code (<code>dendrite.stage({JSON.stringify(key)})</code>)</>}</dd>
        </dl>
        <button style={{ marginTop: 10 }} onClick={onToggle}>{collapsed ? "Show its steps" : "Fold into one card"}</button>
      </section>
      <section>
        <h3>Steps</h3>
        <table>
          <tbody>
            {stage.members.map((m: DagNode) => {
              const p = (m as PlanNode).plan;
              return (
                <tr key={m.id}>
                  <td><a onClick={() => onOpen(m.id)}>{m.name}</a></td>
                  <td>{p ? PLAN[p].label : statusLabel(m.status)}</td>
                  <td className="num">{fmtDur(m.duration)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
      <section aria-label="Edit stage">
        <h3>Appearance</h3>
        <p className="explain">Shown in the UI only: changing it never touches the code or reruns anything.</p>
        <div className="param-form" style={{ marginTop: 8 }}>
          <label className="param-row">
            <span>Label</span>
            <input type="text" aria-label="Stage label" value={label} disabled={!canEdit} onChange={(e) => setLabel(e.target.value)} />
          </label>
          <label className="param-row">
            <span>Description</span>
            <input type="text" aria-label="Stage description" value={description} disabled={!canEdit} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <div className="stage-swatches" role="radiogroup" aria-label="Stage colour">
            {STAGE_COLORS.map((c) => (
              <button
                key={c}
                role="radio"
                aria-checked={c === color}
                aria-label={c}
                disabled={!canEdit}
                className={c === color ? "on" : ""}
                style={{ background: `var(--stage-${c})` }}
                onClick={() => setColor(c)}
              />
            ))}
          </div>
          <div>
            <button
              className="primary"
              disabled={!canEdit || !dirty || save.isPending}
              title={canEdit ? undefined : EDIT_OFF}
              onClick={() => commit({ styles: { ...view.styles, [key]: { label: label.trim() || key, description: description.trim() || undefined, color } } })}
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
            {save.error && <span className="err"> {String(save.error)}</span>}
          </div>
        </div>
      </section>
      {assigned.length > 0 && (
        <section>
          <h3>Grouped in the UI</h3>
          {assigned.map((step) => (
            <div key={step} className="usage">
              {step}{" "}
              {canEdit && (
                <a onClick={() => commit({ assign: Object.fromEntries(Object.entries(view.assign).filter(([st]) => st !== step)) })}>remove</a>
              )}
            </div>
          ))}
          <p className="explain" style={{ marginTop: 8 }}>To make it part of the pipeline, put the calls inside a stage in code:</p>
          <pre className="snippet" aria-label="Stage as code">{stageCode(view, key)}</pre>
          <button onClick={() => navigator.clipboard?.writeText(stageCode(view, key))}>Copy as code</button>
        </section>
      )}
    </>
  );
}

/** For a step the code leaves outside any stage: put it in one (via the pipeline's view). */
export function StageAssign({ step, view, pipeline, stages, canEdit }: {
  step: string;
  view: PipelineView;
  pipeline: string;
  stages: string[];
  canEdit: boolean;
}) {
  const save = useSaveView(pipeline);
  const current = view.assign[step] ?? "";
  const [name, setName] = useState("");
  const set = (stage: string | null) => {
    const assign = { ...view.assign };
    if (stage) assign[step] = stage;
    else delete assign[step];
    save.mutate({ assign, styles: view.styles });
  };
  return (
    <section aria-label="Stage">
      <h3>Stage</h3>
      {canEdit ? (
        <div className="freeze" style={{ flexWrap: "wrap" }}>
          <select aria-label="Put in a stage" value={current} onChange={(e) => set(e.target.value || null)}>
            <option value="">Not in a stage</option>
            {stages.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <input type="text" aria-label="New stage name" placeholder="or a new stage…" value={name} onChange={(e) => setName(e.target.value)} />
          <button disabled={!name.trim() || save.isPending} onClick={() => (set(name.trim()), setName(""))}>Add</button>
        </div>
      ) : (
        <span className="muted">{current || "Not in a stage."}</span>
      )}
      {save.error && <div className="err">{String(save.error)}</div>}
    </section>
  );
}
