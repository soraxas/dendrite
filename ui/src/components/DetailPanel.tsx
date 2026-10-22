// Detail panel: always describes exactly the selected graph item (selection lives in the URL).
import type { ReactNode } from "react";
import { api, type ArtifactRef, type Dag, type DagNode, type Usage } from "../api";
import { describeRoles, flatten, fmtDur, fmtSize, fmtTime, fmtVal, short } from "../format";
import { useArtifact, useExecution } from "../queries";

export interface Nav {
  openExp: (id: string) => void;
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

export function ExperimentDetail({ dag, tree, nav }: { dag: Dag; tree: { id: string; name: string | null; parent_id: string | null }[]; nav: Nav }) {
  const e = dag.experiment;
  const nodes = dag.nodes.filter((n) => n.kind === "node");
  const counts: Record<string, number> = {};
  for (const n of nodes) counts[n.status] = (counts[n.status] ?? 0) + 1;
  const saved = nodes.filter((n) => n.status === "cached").reduce((a, n) => a + (n.duration ?? 0), 0);
  const children = tree.filter((x) => x.parent_id === e.id);
  const overrides = Object.keys(flatten(e.params)).length;
  return (
    <>
      <Selected kind="experiment" label={e.name || e.id} />
      <section>
        <dl className="kv">
          <dt>id</dt><dd className="mono">{e.id}</dd>
          <dt>pipeline</dt><dd>{e.pipeline}</dd>
          <dt>status</dt><dd>{e.status}</dd>
          <dt>created</dt><dd>{fmtTime(e.created_at)}</dd>
          {e.started_at != null && (<><dt>wall time</dt><dd>{fmtDur((e.finished_at ?? Date.now() / 1000) - e.started_at)}</dd></>)}
          <dt>nodes</dt><dd>{Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(", ") || "—"}</dd>
          <dt>compute reused</dt><dd>{fmtDur(saved) || "0s"}</dd>
          {e.flow_run_id && (<><dt>prefect run</dt><dd className="mono">{e.flow_run_id}</dd></>)}
          {e.parent_id && (<><dt>parent</dt><dd><a onClick={() => nav.openExp(e.parent_id!)}>{nav.nameOf(e.parent_id)}</a></dd></>)}
          {children.length > 0 && (
            <><dt>forks</dt><dd>{children.map((c, i) => <span key={c.id}>{i > 0 && ", "}<a onClick={() => nav.openExp(c.id)}>{c.name || c.id}</a></span>)}</dd></>
          )}
        </dl>
        {e.error && <pre className="err" style={{ marginTop: 10 }}>{e.error}</pre>}
      </section>
      <section>
        <h3>Overrides</h3>
        {overrides ? <pre>{JSON.stringify(e.params, null, 2)}</pre> : <Muted>none (all defaults)</Muted>}
      </section>
      <section className="muted" style={{ fontSize: 13 }}>Click a node to inspect it, or an output to trace its lineage.</section>
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

export function NodeDetail({ n, dag, nav }: { n: DagNode; dag: Dag; nav: Nav }) {
  const metrics = Object.entries(n.metrics ?? {});
  const fromOther = n.execution_experiment && n.execution_experiment !== dag.experiment.id;
  const pending = n.status === "queued" || n.status === "running" || n.status === "waiting";
  return (
    <>
      <Selected kind="node" label={n.name} />
      <section>
        <dl className="kv">
          <dt>status</dt><dd><span className="badge" style={{ color: `var(--${n.status})` }}>{n.status}</span></dd>
          {n.duration != null && (<><dt>{n.status === "cached" ? "saved" : "duration"}</dt><dd>{fmtDur(n.duration)}</dd></>)}
          {n.signature && (<><dt>signature</dt><dd className="mono" title={n.signature}>{short(n.signature, 16)}…</dd></>)}
          {n.execution_id && (
            <><dt>execution</dt><dd>
              <a className="mono" onClick={() => nav.selectExec(n.execution_id!)}>{n.execution_id}</a>
              {fromOther && <span className="muted"> (computed by <a onClick={() => nav.openExp(n.execution_experiment!)}>{nav.nameOf(n.execution_experiment!)}</a>)</span>}
            </dd></>
          )}
          {n.started_at && (<><dt>started</dt><dd>{fmtTime(n.started_at)}</dd></>)}
        </dl>
        {n.error && <pre className="err" style={{ marginTop: 10 }}>{n.error}</pre>}
      </section>
      <section>
        <h3>Inputs</h3>
        {Object.keys(n.inputs ?? {}).length ? Object.entries(n.inputs ?? {}).map(([k, r]) => <ArtifactChip key={k} name={k} r={r} nav={nav} />) : <Muted />}
      </section>
      <section>
        <h3>Outputs</h3>
        {Object.keys(n.outputs ?? {}).length ? Object.entries(n.outputs ?? {}).map(([k, r]) => <ArtifactChip key={k} name={k} r={r} nav={nav} />) : <Muted>{pending ? "pending…" : "—"}</Muted>}
      </section>
      <section>
        <h3>Params</h3>
        {n.params && Object.keys(n.params).length ? (
          <table><tbody>{Object.entries(n.params).map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="mono">{fmtVal(v)}</td></tr>)}</tbody></table>
        ) : <Muted />}
      </section>
      {metrics.length > 0 && (
        <section>
          <h3>Metrics</h3>
          <table><tbody>{metrics.map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="num">{fmtVal(v)}</td></tr>)}</tbody></table>
          {n.execution_id && <Sparklines execId={n.execution_id} />}
        </section>
      )}
    </>
  );
}

export function ArtifactDetailPanel({ hash, label, nav, canTrace }: { hash: string; label?: string; nav: Nav; canTrace: boolean }) {
  const { data: a, error } = useArtifact(hash);
  if (error) return <section className="err">{String(error)}</section>;
  if (!a) return <><Selected kind="artifact" label={label ?? short(hash)} /><section className="muted">Loading…</section></>;
  const p = a.preview;
  const name = label ?? a.filename ?? a.kind;
  return (
    <>
      <Selected kind="artifact" label={name} />
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
          {canTrace && <button onClick={() => nav.trace(a.hash)}>Trace lineage</button>}
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
  if (!x) return <><Selected kind="execution" label={short(id, 8)} /><section className="muted">Loading…</section></>;
  const used = x.used_by ?? [];
  return (
    <>
      <Selected kind="execution" label={`${x.node} · ${short(x.id, 8)}`} />
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
        <h3>Reused by {used.length} node run{used.length === 1 ? "" : "s"}</h3>
        {used.map((u) => (
          <div className="usage" key={`${u.experiment_id}.${u.node}`}>
            <a onClick={() => nav.openExp(u.experiment_id)}>{u.name || u.experiment_id}</a> <span className="mono muted">{u.experiment_id}</span>
            <div className="roles">{u.node} · {u.status}</div>
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
