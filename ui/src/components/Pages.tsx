import type { Dag, Diff } from "../api";
import { flatten, fmtVal, short } from "../format";

export function ParamsPage({ dag, onArtifact }: { dag: Dag; onArtifact: (h: string) => void }) {
  const overrides = flatten(dag.experiment.params);
  const rows = dag.nodes.filter((n) => n.kind === "node").flatMap((n) =>
    Object.entries(n.params ?? {}).map(([k, v]) => {
      const key = `${n.name}.${k}`;
      return (
        <tr key={key}>
          <td className="mono">{key}</td>
          <td className="mono">{fmtVal(v)}</td>
          <td>{key in overrides ? "experiment override" : "default / call site"}</td>
        </tr>
      );
    }),
  );
  const pins = Object.entries(dag.experiment.pins ?? {}).flatMap(([node, outs]) => Object.entries(outs).map(([o, h]) => ({ key: `${node}.${o}`, h })));
  return (
    <div className="pad">
      <h3>Resolved parameters</h3>
      <table>
        <thead><tr><th>param</th><th>value</th><th>source</th></tr></thead>
        <tbody>{rows.length ? rows : <tr><td colSpan={3} className="muted">Run the experiment to see resolved params.</td></tr>}</tbody>
      </table>
      <h3>Inputs</h3>
      <table>
        <thead><tr><th>name</th><th>artifact</th></tr></thead>
        <tbody>
          {Object.entries(dag.experiment.inputs).map(([k, h]) => (
            <tr key={k}><td className="mono">{k}</td><td><a className="chip" onClick={() => onArtifact(h)}>{short(h, 12)}</a></td></tr>
          ))}
        </tbody>
      </table>
      {pins.length > 0 && (
        <>
          <h3>Pinned outputs</h3>
          <table>
            <thead><tr><th>output</th><th>artifact</th></tr></thead>
            <tbody>{pins.map((p) => <tr key={p.key}><td className="mono">{p.key}</td><td><a className="chip" onClick={() => onArtifact(p.h)}>{short(p.h, 12)}</a></td></tr>)}</tbody>
          </table>
        </>
      )}
    </div>
  );
}

export function MetricsPage({ dag }: { dag: Dag }) {
  const rows = dag.nodes.filter((n) => n.kind === "node").flatMap((n) =>
    Object.entries(n.metrics ?? {}).map(([k, v]) => (
      <tr key={`${n.name}.${k}`}><td>{n.name}</td><td className="mono">{k}</td><td className="num">{fmtVal(v)}</td><td>{n.status}</td></tr>
    )),
  );
  return (
    <div className="pad">
      <table>
        <thead><tr><th>node</th><th>metric</th><th className="num">value (last step)</th><th>status</th></tr></thead>
        <tbody>{rows.length ? rows : <tr><td colSpan={4} className="muted">No metrics logged.</td></tr>}</tbody>
      </table>
    </div>
  );
}

export function DiffPage({ diff, nameOf, openExp }: { diff?: Diff; nameOf: (id: string) => string; openExp: (id: string) => void }) {
  if (!diff) return <div className="empty">Pick an experiment to compare with.</div>;
  return (
    <div className="pad">
      <p style={{ marginTop: 0 }}>
        <b>A</b> <a onClick={() => openExp(diff.a)}>{nameOf(diff.a)}</a> <span className="mono muted">{diff.a}</span> vs <b>B</b>{" "}
        <a onClick={() => openExp(diff.b)}>{nameOf(diff.b)}</a> <span className="mono muted">{diff.b}</span>
      </p>
      <h3>Parameter changes</h3>
      <table>
        <thead><tr><th>param</th><th>A</th><th>B</th><th /></tr></thead>
        <tbody>
          {diff.params.length ? diff.params.map((p) => (
            <tr key={p.key}><td className="mono">{p.key}</td><td className="mono">{fmtVal(p.a)}</td><td className="mono">{fmtVal(p.b)}</td><td>{p.change}</td></tr>
          )) : <tr><td colSpan={4} className="muted">Identical parameters.</td></tr>}
        </tbody>
      </table>
      {diff.inputs.length > 0 && (
        <>
          <h3>Input changes</h3>
          <table>
            <thead><tr><th>input</th><th>A</th><th>B</th></tr></thead>
            <tbody>{diff.inputs.map((i) => <tr key={i.name}><td>{i.name}</td><td className="mono">{short(i.a)}</td><td className="mono">{short(i.b)}</td></tr>)}</tbody>
          </table>
        </>
      )}
      <h3>Nodes</h3>
      <table>
        <thead><tr><th>node</th><th>A</th><th>B</th><th>reuse</th></tr></thead>
        <tbody>
          {diff.nodes.map((n) => {
            const verdict = n.same_execution ? "shared execution" : n.same_outputs ? "same outputs" : n.same_signature ? "same signature" : "differs";
            const col = n.same_execution || n.same_outputs ? "cached" : "computed";
            return (
              <tr key={n.node}><td>{n.node}</td><td>{n.a_status ?? "—"}</td><td>{n.b_status ?? "—"}</td><td><span className="badge" style={{ color: `var(--${col})` }}>{verdict}</span></td></tr>
            );
          })}
        </tbody>
      </table>
      <h3>Metrics</h3>
      <table>
        <thead><tr><th>node</th><th>metric</th><th className="num">A</th><th className="num">B</th><th className="num">Δ</th></tr></thead>
        <tbody>
          {diff.metrics.length ? diff.metrics.map((m) => {
            const delta = m.a != null && m.b != null ? m.b - m.a : null;
            return (
              <tr key={`${m.node}.${m.key}`}>
                <td>{m.node}</td><td className="mono">{m.key}</td><td className="num">{fmtVal(m.a)}</td><td className="num">{fmtVal(m.b)}</td>
                <td className="num muted">{delta == null ? "" : `${delta > 0 ? "+" : ""}${fmtVal(delta)}`}</td>
              </tr>
            );
          }) : <tr><td colSpan={5} className="muted">No metrics.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
