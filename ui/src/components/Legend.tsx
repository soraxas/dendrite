import { RUN_STATUSES, type RunStatus } from "../api";
import { PLAN, STATUS, UPSTREAM_NOTE } from "../copy";

/** Pipeline view: only the outcomes actually on screen, in plain words (hover for meaning). */
export function DagLegend({ present, hasInputs }: { present: Set<string>; hasInputs: boolean }) {
  const shown = RUN_STATUSES.filter((s) => present.has(s));
  return (
    <div className="legend" aria-label="Legend">
      {hasInputs && (
        <span title="A file or folder this experiment started from.">
          <i className="swatch pill" style={{ borderColor: "var(--input)", background: "var(--input-bg)" }} />Input
        </span>
      )}
      {shown.map((s: RunStatus) => (
        <span key={s} title={STATUS[s].explain}>
          <i className="swatch" style={{ borderColor: `var(--${s})`, background: `var(--${s}-bg)` }} />
          {STATUS[s].label}
        </span>
      ))}
    </div>
  );
}

/** Lineage view: data vs computation; run outcomes deliberately absent. */
export function LineageLegend() {
  return (
    <div className="legend" aria-label="Legend">
      <span title="A stored result: a file, a folder or a value.">
        <i className="swatch pill" style={{ borderColor: "var(--data)", background: "var(--data-bg)" }} />Data
      </span>
      <span title="One actual run of a pipeline step.">
        <i className="swatch" style={{ borderColor: "var(--exec)", background: "var(--exec-bg)" }} />Computation
      </span>
      <span title="The result you are tracing.">
        <i className="swatch pill" style={{ borderColor: "var(--accent)", background: "var(--accent-bg)" }} />Traced result
      </span>
    </div>
  );
}

/** Draft view: what running the draft would do, only for outcomes on screen. */
export function PlanLegend({ present, hasInputs }: { present: Set<string>; hasInputs: boolean }) {
  const item = (key: string, tone: string, label: string, title: string, dashed = false) =>
    present.has(key) && (
      <span key={key} title={title}>
        <i className="swatch" style={{ borderColor: `var(--${tone})`, background: `var(--${tone}-bg)`, borderStyle: dashed ? "dashed" : "solid" }} />
        {label}
      </span>
    );
  return (
    <div className="legend" aria-label="Legend">
      {hasInputs && (
        <span title="A file or folder this experiment starts from.">
          <i className="swatch pill" style={{ borderColor: "var(--input)", background: "var(--input-bg)" }} />Input
        </span>
      )}
      {item("reuse", PLAN.reuse.tone, PLAN.reuse.label, PLAN.reuse.explain)}
      {item("run", PLAN.run.tone, PLAN.run.label, PLAN.run.explain)}
      {item("upstream", PLAN.run.tone, "Will run, earlier step changed", UPSTREAM_NOTE, true)}
      {item("frozen", PLAN.frozen.tone, PLAN.frozen.label, PLAN.frozen.explain)}
    </div>
  );
}
