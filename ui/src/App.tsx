import { useCallback, useEffect, useMemo } from "react";
import type { Dag, Lineage } from "./api";
import { ArtifactDetailPanel, ExecutionDetailPanel, ExperimentDetail, NodeDetail, type Nav } from "./components/DetailPanel";
import { DagView } from "./components/DagView";
import { LineageView } from "./components/LineageView";
import { DiffPage, MetricsPage, ParamsPage } from "./components/Pages";
import { Sidebar } from "./components/Sidebar";
import { Toolbar } from "./components/Toolbar";
import { short } from "./format";
import { isBusy, useDag, useDiff, useExperiments, useInfo, useLineage } from "./queries";
import { splitSel, useRoute, type Route } from "./router";

/** Best human label for an artifact hash within the current DAG: the output (or input) name. */
function dagLabel(dag: Dag | undefined, hash: string): string | undefined {
  for (const n of dag?.nodes ?? []) {
    if (n.kind === "input" && n.artifact?.hash === hash) return n.name;
    for (const [k, r] of Object.entries(n.outputs ?? {})) if (r?.hash === hash) return k;
  }
  for (const n of dag?.nodes ?? []) for (const [k, r] of Object.entries(n.inputs ?? {})) if (r?.hash === hash) return k;
  return undefined;
}

function lineageLabel(lineage: Lineage | undefined, hash: string) {
  return lineage?.artifacts.find((a) => a.hash === hash)?.label;
}

export function App() {
  const [route, navigate] = useRoute();
  const { data: tree = [] } = useExperiments();
  const { data: info } = useInfo();
  const inLineage = route.view === "lineage";
  const dagQ = useDag(inLineage ? undefined : route.exp);
  const lineageQ = useLineage(inLineage ? route.artifact : undefined);
  const diffQ = useDiff(route.view === "diff" ? route.exp : undefined, route.other);
  const dag = dagQ.data;
  const lineage = lineageQ.data;

  useEffect(() => {
    if (route.view === "home" && tree.length) navigate({ view: "dag", exp: tree[tree.length - 1].id }, { replace: true });
  }, [route.view, tree, navigate]);

  const nameOf = useCallback((id: string) => {
    const e = tree.find((x) => x.id === id);
    return e ? e.name || e.id : id;
  }, [tree]);

  const setSel = useCallback((sel?: string) => navigate({ ...route, sel }, { replace: true }), [navigate, route]);
  const nav: Nav = useMemo(() => ({
    openExp: (id) => navigate({ view: "dag", exp: id }),
    selectArtifact: (h) => setSel(`artifact:${h}`),
    selectExec: (id) => setSel(`exec:${id}`),
    trace: (h) => navigate({ view: "lineage", artifact: h, exp: route.exp, sel: `artifact:${h}` }),
    nameOf,
  }), [navigate, setSel, route.exp, nameOf]);

  const { kind, id } = splitSel(route.sel);
  const go = (r: Route) => navigate(r);
  const busy = isBusy(dag) || tree.some((e) => e.status === "running");

  // ---- main area
  let toolbar = null;
  let body = <div className="empty">{tree.length ? "Loading…" : "No experiments yet — create and run one with dendrite.Lab()."}</div>;
  let graphSel: string | undefined;
  if (inLineage) {
    const focus = lineage?.artifact;
    graphSel = kind === "artifact" || kind === "exec" ? id : focus?.hash;
    toolbar = (
      <div className="toolbar">
        {route.exp && <a onClick={() => nav.openExp(route.exp!)}>← {nameOf(route.exp)}</a>}
        <h2>Lineage of {focus ? <span style={{ color: "var(--accent)" }}>{focus.label}</span> : "…"}</h2>
        {focus && (
          <span className="muted">
            {focus.kind} · <span className="mono">{short(focus.hash, 12)}</span>
            {focus.producer_node ? ` · produced by ${focus.producer_node}` : " · source input"} · {lineage.ancestors.length} upstream / {lineage.descendants.length} downstream computations
          </span>
        )}
      </div>
    );
    if (lineageQ.error) body = <div className="empty err">{String(lineageQ.error)}</div>;
    else if (lineage) {
      const execIds = new Set(lineage.executions.map((x) => x.id));
      body = <LineageView lineage={lineage} selected={graphSel} nameOf={nameOf} onSelect={(gid) => setSel(execIds.has(gid) ? `exec:${gid}` : `artifact:${gid}`)} />;
    }
  } else if (dag) {
    toolbar = <Toolbar key={dag.experiment.id} exp={dag.experiment} tree={tree} route={route} go={go} nameOf={nameOf} />;
    graphSel = kind === "node" ? id : kind === "input" ? `input:${id}` : undefined;
    if (route.view === "params") body = <ParamsPage dag={dag} onArtifact={nav.selectArtifact} />;
    else if (route.view === "metrics") body = <MetricsPage dag={dag} />;
    else if (route.view === "diff") body = <DiffPage diff={diffQ.data} nameOf={nameOf} openExp={nav.openExp} />;
    else body = <DagView dag={dag} nameOf={nameOf} selected={graphSel} onSelect={(gid) => setSel(gid.startsWith("input:") ? gid : `node:${gid}`)} />;
  } else if (dagQ.error) body = <div className="empty err">{String(dagQ.error)}</div>;

  // ---- detail panel: exactly the selected item
  let detail = null;
  if (kind === "node" && dag) {
    const n = dag.nodes.find((x) => x.kind === "node" && x.id === id);
    detail = n ? <NodeDetail n={n} dag={dag} nav={nav} /> : null;
  } else if (kind === "input" && dag) {
    const n = dag.nodes.find((x) => x.id === `input:${id}`);
    detail = n?.artifact ? <ArtifactDetailPanel hash={n.artifact.hash} label={n.name} nav={nav} canTrace /> : null;
  } else if (kind === "artifact" && id) {
    const label = inLineage ? lineageLabel(lineage, id) : dagLabel(dag, id);
    detail = <ArtifactDetailPanel key={id} hash={id} label={label} nav={nav} canTrace={!inLineage || id !== lineage?.artifact.hash} />;
  } else if (kind === "exec" && id) {
    detail = <ExecutionDetailPanel key={id} id={id} nav={nav} />;
  } else if (inLineage && lineage) {
    detail = <ArtifactDetailPanel hash={lineage.artifact.hash} label={lineage.artifact.label} nav={nav} canTrace={false} />;
  } else if (dag) {
    detail = <ExperimentDetail dag={dag} tree={tree} nav={nav} />;
  }

  return (
    <div className="app">
      <header className="top">
        <span className="brand">dendrite</span>
        <span className="live">
          <i className="dot" style={{ background: busy ? "var(--running)" : "var(--queued)" }} />
          {busy ? "running" : "idle"}
        </span>
        {info && <span className="meta">db {info.db} · store {info.store}</span>}
      </header>
      <Sidebar tree={tree} selected={route.exp} onOpen={nav.openExp} />
      <main>
        {toolbar}
        <div className="view">{body}</div>
      </main>
      <aside className="detail">{detail ?? <section className="muted">Select a node or artifact.</section>}</aside>
    </div>
  );
}
