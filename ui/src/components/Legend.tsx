import { RUN_STATUSES } from "../api";

/** DAG view: what the run-status colours mean. */
export function DagLegend() {
  return (
    <div className="legend" aria-label="Legend">
      <span><i className="swatch pill" style={{ borderColor: "var(--input)", background: "var(--input-bg)" }} />input</span>
      {RUN_STATUSES.map((s) => (
        <span key={s}><i className="swatch" style={{ borderColor: `var(--${s})`, background: `var(--${s}-bg)` }} />{s}</span>
      ))}
    </div>
  );
}

/** Lineage view: data vs computation; run statuses deliberately absent. */
export function LineageLegend() {
  return (
    <div className="legend" aria-label="Legend">
      <span><i className="swatch pill" style={{ borderColor: "var(--data)", background: "var(--data-bg)" }} />data (artifact)</span>
      <span><i className="swatch" style={{ borderColor: "var(--exec)", background: "var(--exec-bg)" }} />computation (execution)</span>
      <span><i className="swatch pill" style={{ borderColor: "var(--accent)", background: "var(--accent-bg)" }} />traced artifact</span>
    </div>
  );
}
