// Detail panel: always describes exactly the selected graph item (selection lives in the URL).
import { useState, type ReactNode } from "react";
import { api, type ArtifactRef, type Dag, type DagNode, type PlanNode, type Usage } from "../api";
import { EXPERIMENT_STATUS, PLAN, STATUS, statusLabel } from "../copy";
import { describeRoles, flatten, fmtDur, fmtSize, fmtTime, fmtVal, short } from "../format";
import { useArtifact, useDiff, useExecution, useSource } from "../queries";
import { CodeModal } from "./CodeModal";

export interface Nav {
  openExp: (id: string) => void;
  openStep: (expId: string, node: string) => void;
  selectArtifact: (hash: string) => void;
  selectExec: (id: string) => void;
  trace: (hash: string) => void;
  nameOf: (id: string) => string;
}

function Selected({ kind, label }: { kind: string; label: string }) {
  return (
    <div className="selected" aria-live="polite">
      <span className="muted">Selected</span> · <span>{kind}</span> · <b>{label}</b>
    </div>
  );
}

const Muted = ({ children = "—" }: { children?: ReactNode }) => <span className="muted">{children}</span>;

function ArtifactChip({ name, r, nav }: { name: string; r: ArtifactRef | null | undefined; nav: Nav }) {
  if (!r) return null;
  return (
    <a className="chip" title={r.hash} onClick={() => nav.selectArtifact(r.hash)}>
      <b>{name}</b> {r.kind} · {short(r.hash, 8)} · {fmtSize(r.size)}
    </a>
  );
}

function UsedBy({ usages, nav }: { usages: Usage[]; nav: Nav }) {
  if (!usages.length) return <Muted />;
  return (
    <>
      {usages.map((u) => (
        <div className="usage" key={u.id}>
          <a onClick={() => nav.openExp(u.id)}>{u.name || u.id}</a> <span className="mono muted">{u.id}</span>
          <div className="roles">{describeRoles(u.roles)}</div>
        </div>
      ))}
    </>
  );
}

function ChangesFromParent({ id, parent, nav }: { id: string; parent: string; nav: Nav }) {
  const { data: diff } = useDiff(id, parent);
  if (!diff) return <Muted>…</Muted>;
  if (!diff.params.length && !diff.inputs.length) return <Muted>Same parameters as {nav.nameOf(parent)}.</Muted>;
  return (
    <table>
      <thead><tr><th>parameter</th><th>{nav.nameOf(parent)}</th><th>this</th></tr></thead>
      <tbody>
        {diff.params.map((p) => (
          <tr key={p.key}>
            <td className="mono">{p.key}</td>
            <td className="mono muted">{p.b == null ? "default" : fmtVal(p.b)}</td>
            <td className="mono"><b>{p.a == null ? "default" : fmtVal(p.a)}</b></td>
          </tr>
        ))}
        {diff.inputs.map((i) => (
          <tr key={`in:${i.name}`}><td className="mono">input {i.name}</td><td className="mono muted">{short(i.b)}</td><td className="mono"><b>{short(i.a)}</b></td></tr>
        ))}
      </tbody>
    </table>
  );
}

function Technical({ children }: { children: ReactNode }) {
  return (
    <details className="technical">
      <summary>Technical details</summary>
      <dl className="kv" style={{ marginTop: 8 }}>{children}</dl>
    </details>
  );
}

export function ExperimentDetail({ dag, tree, nav }: { dag: Dag; tree: { id: string; name: string | null; parent_id: string | null }[]; nav: Nav }) {
  const e = dag.experiment;
  const steps = dag.nodes.filter((n) => n.kind === "node");
  const counts: Record<string, number> = {};
  const outcome = (n: PlanNode) => (n.plan ? (n.plan === "run" ? "will run" : PLAN[n.plan].label.toLowerCase()) : statusLabel(n.status).toLowerCase());
  for (const n of steps) counts[outcome(n)] = (counts[outcome(n)] ?? 0) + 1;
  const children = tree.filter((x) => x.parent_id === e.id);
  const own = flatten(e.params);
  return (
    <>
      <Selected kind="experiment" label={e.name || e.id} />
      <section>
        <dl className="kv">
          <dt>status</dt><dd>{EXPERIMENT_STATUS[e.status] ?? e.status}</dd>
          <dt>steps</dt><dd>{Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(", ") || "—"}</dd>
          {e.started_at != null && (<><dt>took</dt><dd>{fmtDur((e.finished_at ?? Date.now() / 1000) - e.started_at)}</dd></>)}
          {e.parent_id && (<><dt>fork of</dt><dd><a onClick={() => nav.openExp(e.parent_id!)}>{nav.nameOf(e.parent_id)}</a></dd></>)}
          {children.length > 0 && (
            <><dt>forks</dt><dd>{children.map((c, i) => <span key={c.id}>{i > 0 && ", "}<a onClick={() => nav.openExp(c.id)}>{c.name || c.id}</a></span>)}</dd></>
          )}
        </dl>
        {e.error && <pre className="err" style={{ marginTop: 10 }}>{e.error}</pre>}
      </section>
      <section>
        <h3>{e.parent_id ? `Changed from ${nav.nameOf(e.parent_id)}` : "Parameters set"}</h3>
        {e.parent_id ? (
          <ChangesFromParent id={e.id} parent={e.parent_id} nav={nav} />
        ) : Object.keys(own).length ? (
          <table><tbody>{Object.entries(own).map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="mono">{fmtVal(v)}</td></tr>)}</tbody></table>
        ) : (
          <Muted>All defaults.</Muted>
        )}
      </section>
      <section>
        <Technical>
          <dt>id</dt><dd className="mono">{e.id}</dd>
          <dt>pipeline</dt><dd>{e.pipeline}</dd>
          <dt>created</dt><dd>{fmtTime(e.created_at)}</dd>
          {e.flow_run_id && (<><dt>Prefect run</dt><dd className="mono">{e.flow_run_id}</dd></>)}
        </Technical>
      </section>
      <section className="muted" style={{ fontSize: 13 }}>Hover a step to see what happened; click it for details.</section>
    </>
  );
}

function Sparklines({ execId }: { execId: string }) {
  const { data } = useExecution(execId);
  if (!data) return null;
  const series = new Map<string, { step: number; value: number }[]>();
  for (const m of data.metrics) series.set(m.key, [...(series.get(m.key) ?? []), m]);
  return (
    <>
      {[...series].filter(([, s]) => s.length > 1).map(([k, s]) => {
        const xs = s.map((m) => m.step), ys = s.map((m) => m.value);
        const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
        const pts = s.map((m) => `${(8 + ((m.step - x0) / (x1 - x0 || 1)) * 300).toFixed(1)},${(42 - ((m.value - y0) / (y1 - y0 || 1)) * 34).toFixed(1)}`).join(" ");
        return (
          <div key={k} style={{ marginTop: 10 }}>
            <div className="mono muted">{k} over {s.length} steps</div>
            <svg viewBox="0 0 316 48" width="100%" height="48" role="img" aria-label={`${k} trend`}>
              <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth={2} />
            </svg>
          </div>
        );
      })}
    </>
  );
}

/** Which run's result a reused/frozen step took, with links to that experiment and run. */
function ReusedFrom({ execId, step, self, frozen, nav }: { execId: string; step: string; self: string; frozen: boolean; nav: Nav }) {
  const { data: ex } = useExecution(execId);
  if (!ex) return null;
  const origin = ex.experiment_id;
  const took = ex.finished_at != null ? fmtDur(ex.finished_at - ex.started_at) : null;
  return (
    <section className="reused-from">
      <h3>{frozen ? "Frozen to the result of" : "Result reused from"}</h3>
      {origin && origin !== self ? (
        <div>
          <a onClick={() => nav.openStep(origin, step)}>{nav.nameOf(origin)}</a>
          <span className="muted"> · its {step} run</span>
        </div>
      ) : (
        <div className="muted">an earlier run of this experiment</div>
      )}
      <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
        ran {fmtTime(ex.started_at)}{took ? ` · took ${took}` : ""} · run <a className="mono" onClick={() => nav.selectExec(ex.id)}>{short(ex.id, 8)}</a>
      </div>
      {(ex.used_by?.length ?? 0) > 1 && <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>shared by {ex.used_by!.length} experiment steps</div>}
    </section>
  );
}

/** The other direction: experiments whose steps took this run's result. */
function ReusedBy({ execId, self, nav }: { execId: string; self: string; nav: Nav }) {
  const { data: ex } = useExecution(execId);
  const others = (ex?.used_by ?? []).filter((u) => u.experiment_id !== self);
  if (!others.length) return null;
  return (
    <section className="reused-from">
      <h3>Result reused by</h3>
      {others.map((u) => (
        <div key={`${u.experiment_id}.${u.node}`} className="usage">
          <a onClick={() => nav.openStep(u.experiment_id, u.node)}>{u.name || u.experiment_id}</a>{" "}
          <span className="mono muted">{u.experiment_id}</span>{" "}
          <span className="muted" style={{ fontSize: 12 }}>· {statusLabel(u.status).toLowerCase()}</span>
        </div>
      ))}
    </section>
  );
}

function CodeSection({ execId, step }: { execId: string; step: string }) {
  const { data: ex } = useExecution(execId);
  const src = ex?.source;
  const { data: snap } = useSource(src?.hash);
  const [open, setOpen] = useState(false);
  if (!ex) return null;
  if (!src) return <section><h3>Code</h3><Muted>Not recorded for this run (it predates code snapshots).</Muted></section>;
  const where = `${src.display ?? src.file ?? "unknown file"}${src.line ? `:${src.line}` : ""}`;
  const label = snap?.editable ? "Edit code" : "View code";
  const state = !snap ? "" : snap.status === "same" ? "matches the file" : snap.status === "changed" ? "file changed since this ran" : "file no longer exists";
  return (
    <section>
      <h3>Code</h3>
      <div className="mono" style={{ overflowWrap: "anywhere" }} title={src.file ?? undefined}>{where}</div>
      <div className="muted" style={{ fontSize: 12, margin: "2px 0 8px" }}>{src.qualname}{state ? ` · ${state}` : ""}</div>
      <button onClick={() => setOpen(true)}>{label}</button>
      {open && <CodeModal hash={src.hash} step={step} onClose={() => setOpen(false)} />}
    </section>
  );
}

export function NodeDetail({ n, dag, nav }: { n: DagNode; dag: Dag; nav: Nav }) {
  const metrics = Object.entries(n.metrics ?? {});
  const pending = n.status === "queued" || n.status === "running" || n.status === "waiting";
  const s = STATUS[n.status as keyof typeof STATUS];
  return (
    <>
      <Selected kind="step" label={n.name} />
      <section>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <span className="badge" style={{ color: `var(--${n.status})` }}>{statusLabel(n.status)}</span>
          {n.duration != null && <span className="muted">took {fmtDur(n.duration)}</span>}
        </div>
        <p className="explain">{s?.explain}</p>
        {n.error && <pre className="err" style={{ marginTop: 10 }}>{n.error}</pre>}
      </section>
      {n.execution_id && (n.status === "cached" || n.status === "pinned") && (
        <ReusedFrom execId={n.execution_id} step={n.name} self={dag.experiment.id} frozen={n.status === "pinned"} nav={nav} />
      )}
      {n.execution_id && n.status === "computed" && <ReusedBy execId={n.execution_id} self={dag.experiment.id} nav={nav} />}
      <section>
        <h3>Inputs</h3>
        {Object.keys(n.inputs ?? {}).length ? Object.entries(n.inputs ?? {}).map(([k, r]) => <ArtifactChip key={k} name={k} r={r} nav={nav} />) : <Muted />}
      </section>
      <section>
        <h3>Outputs</h3>
        {Object.keys(n.outputs ?? {}).length ? Object.entries(n.outputs ?? {}).map(([k, r]) => <ArtifactChip key={k} name={k} r={r} nav={nav} />) : <Muted>{pending ? "not produced yet" : "—"}</Muted>}
        {Object.keys(n.outputs ?? {}).length > 0 && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Click an output to preview it and trace where it came from.</div>}
      </section>
      {n.execution_id && <CodeSection execId={n.execution_id} step={n.name} />}
      <section>
        <h3>Parameters</h3>
        {n.params && Object.keys(n.params).length ? (
          <table><tbody>{Object.entries(n.params).map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="mono">{fmtVal(v)}</td></tr>)}</tbody></table>
        ) : <Muted>{pending ? "resolved when the step starts" : "none"}</Muted>}
      </section>
      {metrics.length > 0 && (
        <section>
          <h3>Metrics</h3>
          <table><tbody>{metrics.map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="num">{fmtVal(v)}</td></tr>)}</tbody></table>
          {n.execution_id && <Sparklines execId={n.execution_id} />}
        </section>
      )}
      <section>
        <Technical>
          {n.signature && (<><dt>signature</dt><dd className="mono" title="Hash of code, inputs and parameters; identical signatures are reused.">{n.signature}</dd></>)}
          {n.execution_id && (<><dt>run</dt><dd><a className="mono" onClick={() => nav.selectExec(n.execution_id!)}>{n.execution_id}</a></dd></>)}
          {n.started_at && (<><dt>started</dt><dd>{fmtTime(n.started_at)}</dd></>)}
        </Technical>
      </section>
    </>
  );
}

export function ArtifactDetailPanel({ hash, label, nav, canTrace }: { hash: string; label?: string; nav: Nav; canTrace: boolean }) {
  const { data: a, error } = useArtifact(hash);
  if (error) return <section className="err">{String(error)}</section>;
  if (!a) return <><Selected kind="data" label={label ?? short(hash)} /><section className="muted">Loading…</section></>;
  const p = a.preview;
  const name = label ?? a.filename ?? a.kind;
  return (
    <>
      <Selected kind="data" label={name} />
      <section>
        <h4>{name}</h4>
        <dl className="kv">
          <dt>hash</dt><dd className="mono">{a.hash}</dd>
          <dt>kind</dt><dd>{a.kind}{a.filename && a.filename !== name ? ` · ${a.filename}` : ""}</dd>
          <dt>size</dt><dd>{fmtSize(a.size)}</dd>
          <dt>in store</dt><dd className={a.present ? "" : "err"}>{a.present ? "yes" : "missing"}</dd>
          <dt>produced by</dt>
          <dd>{a.producer ? <><a onClick={() => nav.selectExec(a.producer!.id)}>{a.producer.node}</a> <span className="mono muted">{short(a.producer.id, 8)}</span></> : "source input"}</dd>
        </dl>
        <div style={{ marginTop: 10, display: "flex", gap: 6 }}>
          {canTrace && <button className="primary" onClick={() => nav.trace(a.hash)} title="Show everything that produced this, and everything built from it, across all experiments">Trace lineage</button>}
          {a.kind === "file" && a.present && <a href={api.downloadUrl(a.hash)}><button>Download</button></a>}
        </div>
      </section>
      <section>
        <h3>Used by experiments</h3>
        <UsedBy usages={a.experiments} nav={nav} />
      </section>
      <section>
        <h3>Preview</h3>
        {p.type === "text" && <pre>{p.text}</pre>}
        {p.type === "value" && <pre>{p.repr}</pre>}
        {p.type === "manifest" && (
          <>
            <table>
              <thead><tr><th>path ({p.count})</th><th className="num">size</th></tr></thead>
              <tbody>{p.entries.slice(0, 80).map((e) => <tr key={e.path}><td className="mono">{e.path}</td><td className="num">{fmtSize(e.size)}</td></tr>)}</tbody>
            </table>
            {p.count > 80 && <Muted>…and {p.count - 80} more</Muted>}
          </>
        )}
        {p.type === "missing" && <span className="err">Blob missing from store!</span>}
        {p.type === "too_large" && <Muted>Too large to preview.</Muted>}
        {p.type === "binary" && <Muted>Binary content.</Muted>}
      </section>
    </>
  );
}

export function ExecutionDetailPanel({ id, nav }: { id: string; nav: Nav }) {
  const { data: x, error } = useExecution(id);
  if (error) return <section className="err">{String(error)}</section>;
  if (!x) return <><Selected kind="computation" label={short(id, 8)} /><section className="muted">Loading…</section></>;
  const used = x.used_by ?? [];
  return (
    <>
      <Selected kind="computation" label={`${x.node} · ${short(x.id, 8)}`} />
      <section>
        <dl className="kv">
          <dt>id</dt><dd className="mono">{x.id}</dd>
          <dt>status</dt><dd>{x.status}</dd>
          <dt>signature</dt><dd className="mono">{x.signature}</dd>
          <dt>cacheable</dt><dd>{x.cacheable ? "yes" : "no"}</dd>
          <dt>duration</dt><dd>{fmtDur(x.finished_at != null ? x.finished_at - x.started_at : null)}</dd>
          <dt>started</dt><dd>{fmtTime(x.started_at)}</dd>
          {x.task_run_id && (<><dt>prefect task</dt><dd className="mono">{x.task_run_id}</dd></>)}
        </dl>
        {x.error && <pre className="err" style={{ marginTop: 10 }}>{x.error}</pre>}
      </section>
      <section>
        <h3>Inputs</h3>
        {Object.entries(x.inputs).map(([k, h]) => <a key={k} className="chip" onClick={() => nav.selectArtifact(h)}><b>{k}</b> {short(h, 8)}</a>)}
        {!Object.keys(x.inputs).length && <Muted />}
      </section>
      <section>
        <h3>Outputs</h3>
        {Object.entries(x.outputs).map(([k, h]) => <a key={k} className="chip" onClick={() => nav.selectArtifact(h)}><b>{k}</b> {short(h, 8)}</a>)}
        {!Object.keys(x.outputs).length && <Muted />}
      </section>
      <section>
        <h3>Used by {used.length} experiment step{used.length === 1 ? "" : "s"}</h3>
        {used.map((u) => (
          <div className="usage" key={`${u.experiment_id}.${u.node}`}>
            <a onClick={() => nav.openExp(u.experiment_id)}>{u.name || u.experiment_id}</a> <span className="mono muted">{u.experiment_id}</span>
            <div className="roles">{u.node} · {statusLabel(u.status).toLowerCase()}</div>
          </div>
        ))}
      </section>
      <section>
        <h3>Signature spec</h3>
        <pre>{JSON.stringify(x.spec, null, 2)}</pre>
      </section>
    </>
  );
}
