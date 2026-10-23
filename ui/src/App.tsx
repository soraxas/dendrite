import { useCallback, useEffect, useMemo, useState } from "react";
import type { Dag, Lineage, PlanNode } from "./api";
import { ArtifactDetailPanel, ExecutionDetailPanel, ExperimentDetail, NodeDetail, type Nav } from "./components/DetailPanel";
import { DagView } from "./components/DagView";
import { DraftBar, DraftStepEditor } from "./components/Draft";
import { LineageView } from "./components/LineageView";
import { DiffPage, MetricsPage, ParamsPage } from "./components/Pages";
import { Sidebar } from "./components/Sidebar";
import { Toolbar } from "./components/Toolbar";
import { useDag, useDiff, useExperiments, useInfo, useLineage, usePlan } from "./queries";
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

/** The graph node to keep selected while one of its data items is open: producer, else input, else first reader. */
function ownerOf(dag: Dag, hash: string): string | undefined {
  const nodes = dag.nodes;
  return (
    nodes.find((n) => n.kind === "node" && Object.values(n.outputs ?? {}).some((r) => r?.hash === hash))?.id ??
    nodes.find((n) => n.kind === "input" && n.artifact?.hash === hash)?.id ??
    nodes.find((n) => n.kind === "node" && Object.values(n.inputs ?? {}).some((r) => r?.hash === hash))?.id
  );
}

function lineageLabel(lineage: Lineage | undefined, hash: string) {
  return lineage?.artifacts.find((a) => a.hash === hash)?.label;
}

const SIDEBAR_KEY = "dendrite.sidebarCollapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

export function App() {
  const [route, navigate] = useRoute();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleSidebar = useCallback(() => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, c ? "0" : "1");
      } catch {
        /* per-browser convenience only */
      }
      return !c;
    });
  }, []);
  const { data: tree = [] } = useExperiments();
  const { data: info } = useInfo();
  const inLineage = route.view === "lineage";
  // A draft has no runs yet; its graph is the plan of what running it would do.
  const isDraft = !inLineage && tree.find((e) => e.id === route.exp)?.status === "draft";
  const planQ = usePlan(isDraft ? route.exp : undefined);
  const plan = isDraft && planQ.data?.experiment.id === route.exp ? planQ.data : undefined;
  const dagQ = useDag(inLineage || (isDraft && !planQ.error) ? undefined : route.exp);
  const lineageQ = useLineage(inLineage ? route.artifact : undefined);
  const diffQ = useDiff(route.view === "diff" ? route.exp : undefined, route.other);
  const dag: Dag | undefined = plan ?? (isDraft && !planQ.error ? undefined : dagQ.data);
  const canEdit = !!info?.edit;
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
    openStep: (expId, node) => navigate({ view: "dag", exp: expId, sel: `node:${node}` }),
    selectArtifact: (h) => setSel(`artifact:${h}`),
    selectExec: (id) => setSel(`exec:${id}`),
    trace: (h) => navigate({ view: "lineage", artifact: h, exp: route.exp, sel: `artifact:${h}` }),
    nameOf,
  }), [navigate, setSel, route.exp, nameOf]);

  const { kind, id } = splitSel(route.sel);
  const go = (r: Route) => navigate(r);
  const running = tree.filter((e) => e.status === "running");

  // ---- main area
  let toolbar = null;
  let body = <div className="empty">{tree.length ? "Loading…" : "No experiments yet — create and run one with dendrite.Lab()."}</div>;
  let graphSel: string | undefined;
  if (inLineage) {
    const focus = lineage?.artifact;
    graphSel = kind === "artifact" || kind === "exec" ? id : focus?.hash;
    toolbar = (
      <div className="toolbar">
        <div className="tb-title">
          <div className="tb-row">
            <span className="mode-chip">Lineage</span>
            <h2>{focus ? focus.label : "…"}</h2>
          </div>
          {focus && (
            <div className="story">
              {focus.producer_node ? `Produced by ${focus.producer_node}` : "Source input"} · {lineage.ancestors.length} computation{lineage.ancestors.length === 1 ? "" : "s"} upstream ·{" "}
              {lineage.descendants.length} downstream
            </div>
          )}
        </div>
      </div>
    );
    if (lineageQ.error) body = <div className="empty err">{String(lineageQ.error)}</div>;
    else if (lineage) {
      const execIds = new Set(lineage.executions.map((x) => x.id));
      body = (
        <LineageView
          lineage={lineage}
          selected={graphSel}
          nameOf={nameOf}
          back={route.exp ? { label: nameOf(route.exp), go: () => nav.openExp(route.exp!) } : undefined}
          onSelect={(gid) => setSel(execIds.has(gid) ? `exec:${gid}` : `artifact:${gid}`)}
        />
      );
    }
  } else if (dag) {
    toolbar = (
      <>
        <Toolbar key={dag.experiment.id} dag={dag} tree={tree} route={route} go={go} nameOf={nameOf} canEdit={canEdit} plan={plan} />
        {isDraft && (
          <DraftBar
            key={`bar:${dag.experiment.id}`}
            exp={dag.experiment}
            plan={plan}
            planError={planQ.error}
            canEdit={canEdit}
            onDiscarded={() => (dag.experiment.parent_id ? nav.openExp(dag.experiment.parent_id) : navigate({ view: "home" }))}
          />
        )}
      </>
    );
    graphSel = kind === "node" ? id : kind === "input" ? `input:${id}` : kind === "artifact" && id ? ownerOf(dag, id) : undefined;
    if (route.view === "params") body = <ParamsPage dag={dag} onArtifact={nav.selectArtifact} />;
    else if (route.view === "metrics") body = <MetricsPage dag={dag} />;
    else if (route.view === "diff") body = <DiffPage diff={diffQ.data} nameOf={nameOf} openExp={nav.openExp} />;
    else
      body = (
        <DagView
          dag={dag}
          nameOf={nameOf}
          selected={graphSel}
          onSelect={(gid) => setSel(gid.startsWith("input:") ? gid : `node:${gid}`)}
          onArtifact={nav.selectArtifact}
          onOpenSource={nav.openStep}
          activeArtifact={kind === "artifact" ? id : undefined}
          planned={!!plan}
        />
      );
  } else if (dagQ.error) body = <div className="empty err">{String(dagQ.error)}</div>;

  // ---- detail panel: exactly the selected item
  let detail = null;
  if (kind === "node" && dag) {
    const n = dag.nodes.find((x) => x.kind === "node" && x.id === id);
    detail = !n ? null : plan ? (
      <DraftStepEditor key={n.id} n={n as PlanNode} plan={plan} canEdit={canEdit} nameOf={nameOf} openStep={nav.openStep} />
    ) : (
      <NodeDetail n={n} dag={dag} nav={nav} />
    );
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
    <div className={`app ${collapsed ? "side-collapsed" : ""}`}>
      <header className="top">
        <span className="brand">dendrite</span>
        {running.length > 0 && (
          <span className="live" title="The page updates every second while something runs.">
            <i className="dot pulse" style={{ background: "var(--running)" }} />
            {running.length} running
          </span>
        )}
        <span className="spacer" />
        {info && (
          <details className="lab">
            <summary title="Where results are stored">Lab</summary>
            <div className="lab-pop">
              <dl className="kv">
                <dt>metadata</dt><dd className="mono">{info.db}</dd>
                <dt>artifacts</dt><dd className="mono">{info.store}</dd>
              </dl>
            </div>
          </details>
        )}
      </header>
      <Sidebar tree={tree} selected={route.exp} onOpen={nav.openExp} collapsed={collapsed} onToggle={toggleSidebar} />
      <main>
        {toolbar}
        <div className="view">{body}</div>
      </main>
      <aside className="detail">{detail ?? <section className="muted">Select a node or artifact.</section>}</aside>
    </div>
  );
}
