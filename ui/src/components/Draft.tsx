// Draft mode: the bar above the graph, and the per-step editor in the detail panel.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type DraftEdit, type ExperimentSummary, type ParamField, type Plan, type PlanNode } from "../api";
import { PLAN, REASON, UPSTREAM_NOTE } from "../copy";
import { fmtDur, fmtVal } from "../format";

/** Save an edit to a draft, then re-plan (the old plan stays on screen meanwhile). */
export function useEditDraft(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (edit: DraftEdit) => api.editDraft(id, edit),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plan", id] });
      qc.invalidateQueries({ queryKey: ["experiments"] });
    },
  });
}

/** "Will run 2 of 4 steps · about 14 min" */
export function planSummary(plan: Plan): string {
  const t = plan.totals;
  if (!t.steps) return "No steps planned yet";
  if (!t.run) return `Nothing to run — ${!t.frozen ? "every step is reused" : !t.reuse ? "every step is frozen" : `${t.reuse} reused, ${t.frozen} frozen`}`;
  const est = t.estimate ? ` · about ${fmtDur(t.estimate)}${t.unestimated ? "+" : ""}` : "";
  return `Will run ${t.run} of ${t.steps} steps${est}`;
}

export function runCommand(exp: ExperimentSummary, module: string | null | undefined): string {
  return `dendrite run ${exp.id}${module ? ` --import ${module}` : ""}`;
}

export function DraftBar({ exp, plan, planError, canEdit, onDiscarded }: {
  exp: ExperimentSummary;
  plan?: Plan;
  planError?: unknown;
  canEdit: boolean;
  onDiscarded: () => void;
}) {
  const qc = useQueryClient();
  const edit = useEditDraft(exp.id);
  const [name, setName] = useState(exp.name ?? "");
  useEffect(() => setName(exp.name ?? ""), [exp.name]);
  const discard = useMutation({
    mutationFn: () => api.discard(exp.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["experiments"] });
      onDiscarded();
    },
  });
  const [copied, setCopied] = useState(false);
  const cmd = runCommand(exp, plan?.module);
  const notImported = planError instanceof ApiError && planError.code === "pipeline_not_imported";
  const commitName = () => name !== (exp.name ?? "") && edit.mutate({ name });
  return (
    <div className="draft-bar" role="region" aria-label="Draft">
      <span className="mode-chip draft">Draft</span>
      {canEdit ? (
        <input
          className="name"
          aria-label="Draft name"
          value={name}
          placeholder="name this draft"
          onChange={(e) => setName(e.target.value)}
          onBlur={commitName}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        />
      ) : null}
      <span className="muted">Edits save as you go. Nothing runs until you run it:</span>
      <code title="Run this in a terminal; the graph turns live">{cmd}</code>
      <button
        onClick={() => {
          navigator.clipboard?.writeText(cmd).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          });
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
      <span className="spacer" />
      {canEdit && (
        <button
          onClick={() => confirm(`Discard draft ${exp.name || exp.id}? This can't be undone.`) && discard.mutate()}
          disabled={discard.isPending}
        >
          Discard
        </button>
      )}
      {discard.error && <span className="warn">{String((discard.error as Error).message)}</span>}
      {edit.error && <span className="warn">{String((edit.error as Error).message)}</span>}
      {notImported && (
        <span className="warn" style={{ flexBasis: "100%" }}>
          Can't preview this draft: the UI server hasn't loaded the pipeline code. Restart it with <code>dendrite ui --import &lt;module&gt;</code>.
        </span>
      )}
      {plan && !plan.complete && (
        <span className="warn" style={{ flexBasis: "100%" }}>
          {plan.blocked_by
            ? `The preview stops at ${plan.blocked_by}: the pipeline reads that step's output to decide what to do next.`
            : `The preview failed: ${plan.error}`}
        </span>
      )}
      {plan?.complete && plan.error && <span className="warn" style={{ flexBasis: "100%" }}>{plan.error}</span>}
    </div>
  );
}

const key = (node: string, p: ParamField) => `${node}.${p.name}`;

/** Parse an edited field into the JSON value it means, or an error message. */
export function parseParam(p: ParamField, text: string): { value?: unknown; error?: string } {
  const t = text.trim();
  switch (p.json_type) {
    case "integer":
      return /^-?\d+$/.test(t) ? { value: Number(t) } : { error: "a whole number" };
    case "number":
      return t !== "" && Number.isFinite(Number(t)) ? { value: Number(t) } : { error: "a number" };
    case "string":
      return { value: text };
    default:
      try {
        return { value: JSON.parse(t) };
      } catch {
        return { error: "valid JSON" };
      }
  }
}

const show = (p: ParamField, v: unknown) => (p.json_type === "string" ? String(v ?? "") : p.json_type === "integer" || p.json_type === "number" ? String(v) : JSON.stringify(v));

export function ParamRow({ node, p, onSave, onReset, disabled }: {
  node: string;
  p: ParamField;
  onSave: (k: string, v: unknown, float: boolean) => void;
  onReset: (k: string) => void;
  disabled: boolean;
}) {
  const [text, setText] = useState(show(p, p.value));
  const [error, setError] = useState<string>();
  const editing = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  // Follow the server unless the user is mid-edit (polls and re-plans must not clobber typing).
  useEffect(() => {
    if (!editing.current) setText(show(p, p.value));
  }, [p]);
  const k = key(node, p);
  const commit = (raw: string) => {
    const r = parseParam(p, raw);
    setError(r.error && `Enter ${r.error}`);
    if (r.error) return;
    if (JSON.stringify(r.value) === JSON.stringify(p.value)) return;
    onSave(k, r.value, p.json_type === "number");
  };
  const schedule = (raw: string) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => commit(raw), 600);
  };
  const id = `param-${k}`;
  return (
    <div className={`param-row ${p.changed ? "changed" : ""}`}>
      <label htmlFor={id}>
        {p.name}
        {p.changed && <span className="changed-mark" title="Changed from the parent">✎</span>}
        {p.annotation && <span className="muted">{p.annotation}</span>}
      </label>
      <div className="ctl">
        {p.json_type === "boolean" ? (
          <input id={id} type="checkbox" checked={!!p.value} disabled={disabled} onChange={(e) => onSave(k, e.target.checked, false)} />
        ) : p.json_type === "array" || p.json_type === "object" || p.json_type === "any" ? (
          <textarea
            id={id}
            rows={Math.min(6, Math.max(1, text.split("\n").length))}
            value={text}
            disabled={disabled}
            onFocus={() => (editing.current = true)}
            onBlur={() => {
              editing.current = false;
              commit(text);
            }}
            onChange={(e) => setText(e.target.value)}
          />
        ) : (
          <input
            id={id}
            type="text"
            inputMode={p.json_type === "integer" ? "numeric" : p.json_type === "number" ? "decimal" : undefined}
            value={text}
            disabled={disabled}
            onFocus={() => (editing.current = true)}
            onBlur={() => {
              editing.current = false;
              window.clearTimeout(timer.current);
              commit(text);
            }}
            onChange={(e) => {
              setText(e.target.value);
              schedule(e.target.value);
            }}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          />
        )}
        {p.changed && !disabled && (
          <button className="reset" title={`Back to ${fmtVal(p.inherited)}`} aria-label={`Reset ${p.name}`} onClick={() => onReset(k)}>↺</button>
        )}
      </div>
      {error ? (
        <div className="bad">{error}</div>
      ) : (
        <div className="hint">
          {p.changed ? `was ${fmtVal(p.inherited)}` : p.overridden ? "set by this experiment" : "default"}
          {p.default != null && p.changed ? ` · default ${fmtVal(p.default)}` : ""}
        </div>
      )}
    </div>
  );
}

/** Draft-mode step panel: planned outcome, parameter form and freeze toggle. */
export function DraftStepEditor({ n, plan, canEdit, nameOf, openStep }: {
  n: PlanNode;
  plan: Plan;
  canEdit: boolean;
  nameOf: (id: string) => string;
  openStep: (exp: string, node: string) => void;
}) {
  const exp = plan.experiment;
  const edit = useEditDraft(exp.id);
  const outcome = n.plan ?? "run";
  const copy = PLAN[outcome];
  const frozen = !!exp.pins?.[n.name];
  const origin = n.reuse_from?.experiment_id;
  const fields = n.param_schema ?? [];
  const save = (k: string, v: unknown, float: boolean) => edit.mutate({ params: { [k]: v }, ...(float ? { floats: [k] } : {}) } as DraftEdit);
  return (
    <>
      <div className="selected" aria-live="polite">
        <span className="muted">Selected</span> · <span>step</span> · <b>{n.name}</b>
      </div>
      <section>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <span className="badge" style={{ color: `var(--${copy.tone})`, borderStyle: n.reason === "upstream" ? "dashed" : "solid" }}>{outcome === "run" ? "Will run" : copy.label}</span>
          {n.estimate != null && <span className="muted">{outcome === "run" ? "about" : "took"} {fmtDur(n.estimate)}</span>}
          {edit.isPending && <span className="muted">saving…</span>}
        </div>
        <p className="explain">
          {outcome === "run" ? `It will run because ${REASON[n.reason ?? "new"] ?? n.reason}.` : copy.explain}
          {n.reason === "upstream" ? ` ${UPSTREAM_NOTE}` : ""}
        </p>
        {origin && origin !== exp.id && outcome !== "run" && (
          <p className="explain" style={{ marginTop: 6 }}>
            Result from <a onClick={() => openStep(origin, n.name)}>{nameOf(origin)}</a>.
          </p>
        )}
      </section>
      {exp.parent_id && (
        <section>
          <label className="freeze">
            <input
              type="checkbox"
              checked={frozen}
              disabled={!canEdit || edit.isPending}
              onChange={(e) => edit.mutate({ pins: { [n.name]: e.target.checked ? "parent" : null } })}
            />
            Freeze to {nameOf(exp.parent_id)}'s result
          </label>
          <div className="muted" style={{ fontSize: 12, marginTop: 3 }}>Skips this step and uses exactly what the parent produced, whatever its parameters say.</div>
        </section>
      )}
      <section>
        <h3>Parameters</h3>
        {fields.length ? (
          <div className="param-form">
            {fields.map((p) => (
              <ParamRow key={key(n.name, p)} node={n.name} p={p} disabled={!canEdit || frozen} onSave={save} onReset={(k) => edit.mutate({ reset: [k] })} />
            ))}
          </div>
        ) : (
          <span className="muted">{frozen ? "Frozen steps don't use parameters." : "This step has no parameters."}</span>
        )}
        {!canEdit && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Editing is off; start <code>dendrite ui</code> on localhost or with --allow-edit.</div>}
        {edit.error && <div className="err" style={{ fontSize: 12, marginTop: 6 }}>{String((edit.error as Error).message)}</div>}
      </section>
    </>
  );
}
