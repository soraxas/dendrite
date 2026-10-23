import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, type Dag, type Lineage, type PlanNode } from "./api";
import { ArtifactDetailPanel, ExecutionDetailPanel, ExperimentDetail, NodeDetail, type Nav } from "./components/DetailPanel";
import { CodeView } from "./components/CodeView";
import { DagView } from "./components/DagView";
import { DraftBar, DraftStepEditor } from "./components/Draft";
import { LineageView } from "./components/LineageView";
import { DiffPage, MetricsPage, ParamsPage } from "./components/Pages";
import { Sidebar } from "./components/Sidebar";
import { StoragePage } from "./components/Storage";
import { Toolbar } from "./components/Toolbar";
import { collapsedSteps, fanoutGroups, groupId, nextSelection } from "./fanout";
import { GroupDetail } from "./components/DetailPanel";
import { useDag, useDiff, useExperiments, useInfo, useIsLive, useLineage, useLiveUpdates, usePlan } from "./queries";
import { parse, splitSel, useRoute, type Route } from "./router";
import { confirmLeave } from "./unsaved";

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
  const [route, navigateTo] = useRoute();
  // Switching view or experiment asks first when the Code view holds unsaved edits.
  const navigate = useCallback(
    (r: Route, opts?: { replace?: boolean }) => {
      const current = parse(location.hash);
      if ((r.view !== current.view || r.exp !== current.exp) && !confirmLeave()) return;
      navigateTo(r, opts);
    },
    [navigateTo],
  );
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
  useLiveUpdates();
  const live = useIsLive();
  const { data: tree = [] } = useExperiments();
  const { data: info } = useInfo();
  const inLineage = route.view === "lineage";
  const inStorage = route.view === "storage";
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

  // Executions outlive deleted experiments, so a result can name an experiment that's gone.
  const nameOf = useCallback((id: string) => {
    const e = tree.find((x) => x.id === id);
    return e ? e.name || e.id : tree.length ? `deleted experiment ${id}` : id;
  }, [tree]);

  const setSel = useCallback((sel?: string) => navigate({ ...route, sel }, { replace: true }), [navigate, route]);
  // Clicking the selected item again deselects it (closing its data chips).
  const pick = useCallback((sel: string) => setSel(nextSelection(route.sel, sel)), [setSel, route.sel]);
  // Fan-out groups the user flipped from their default, per experiment.
  const [toggled, setToggled] = useState<Record<string, string[]>>({});
  const toggleGroup = useCallback((step: string) => {
    if (!route.exp) return;
    setToggled((t) => {
      const cur = new Set(t[route.exp!] ?? []);
      if (cur.has(step)) cur.delete(step);
      else cur.add(step);
      return { ...t, [route.exp!]: [...cur] };
    });
  }, [route.exp]);
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

  const groups = useMemo(() => fanoutGroups(dag?.nodes ?? []), [dag?.nodes]);
  const collapsedGroups = useMemo(() => collapsedSteps(groups, new Set(toggled[route.exp ?? ""] ?? [])), [groups, toggled, route.exp]);

  // ---- main area
  let toolbar = null;
  let body = <div className="empty">{tree.length ? "Loading…" : "No experiments yet — create and run one with dendrite.Lab()."}</div>;
  let graphSel: string | undefined;
  if (inStorage) {
    toolbar = (
      <div className="toolbar">
        <div className="tb-title">
          <div className="tb-row"><span className="mode-chip">Storage</span><h2>Where the space goes</h2></div>
          <div className="story">Delete old experiments, evict cache you can recompute, and clean up what nothing uses.</div>
        </div>
      </div>
    );
    body = (
      <StoragePage
        tab={route.tab ?? "experiments"}
        go={(tab) => navigate({ view: "storage", tab }, { replace: true })}
        canEdit={canEdit}
        nameOf={nameOf}
        openExp={nav.openExp}
        openArtifact={(h) => navigate({ view: "lineage", artifact: h, sel: `artifact:${h}` })}
      />
    );
  } else if (inLineage) {
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
          onSelect={(gid) => pick(execIds.has(gid) ? `exec:${gid}` : `artifact:${gid}`)}
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
    graphSel = kind === "node" ? id : kind === "input" ? `input:${id}` : kind === "group" ? groupId(id!) : kind === "artifact" && id ? ownerOf(dag, id) : undefined;
    // A selected item inside a collapsed group highlights the group.
    const inGroup = graphSel && groups.find((g) => collapsedGroups.has(g.step) && g.members.some((m) => m.id === graphSel));
    if (inGroup) graphSel = inGroup.id;
    if (route.view === "params") body = <ParamsPage dag={dag} onArtifact={nav.selectArtifact} />;
    else if (route.view === "code") body = <CodeView key={dag.experiment.pipeline} pipeline={dag.experiment.pipeline} dag={dag} canEdit={canEdit} />;
    else if (route.view === "metrics") body = <MetricsPage dag={dag} />;
    else if (route.view === "diff") body = <DiffPage diff={diffQ.data} nameOf={nameOf} openExp={nav.openExp} />;
    else
      body = (
        <DagView
          dag={dag}
          nameOf={nameOf}
          selected={graphSel}
          onSelect={(gid) => pick(gid.startsWith("input:") ? gid : gid.startsWith("group:") ? gid : `node:${gid}`)}
          collapsed={collapsedGroups}
          onToggleGroup={toggleGroup}
          onArtifact={nav.selectArtifact}
          onOpenSource={nav.openStep}
          activeArtifact={kind === "artifact" ? id : undefined}
          planned={!!plan}
        />
      );
  } else if (dagQ.error) {
    body =
      dagQ.error instanceof ApiError && dagQ.error.status === 404 && tree.length && !tree.some((e) => e.id === route.exp) ? (
        <div className="empty">This experiment was deleted. Its results' lineage is still recorded, and other experiments keep using shared results.</div>
      ) : (
        <div className="empty err">{String(dagQ.error)}</div>
      );
  }

  // ---- detail panel: exactly the selected item
  let detail = null;
  if (inStorage) {
    detail = (
      <section className="muted storage-help">
        <h3>How space is counted</h3>
        <p><b>Frees</b> is what deleting an experiment and then cleaning up gives back: data no other experiment uses. Results shared by forks are counted once and stay while anything uses them.</p>
        <p>Evicted or cleaned-up results recompute the next time a run needs them. Lineage records always stay.</p>
      </section>
    );
  } else if (kind === "group" && dag && id) {
    const g = groups.find((x) => x.step === id);
    detail = g ? (
      <GroupDetail
        group={g}
        collapsed={collapsedGroups.has(g.step)}
        onToggle={() => toggleGroup(g.step)}
        onOpen={(node) => {
          if (collapsedGroups.has(g.step)) toggleGroup(g.step);
          setSel(`node:${node}`);
        }}
      />
    ) : null;
  } else if (kind === "node" && dag) {
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
    detail = (
      <ExperimentDetail
        dag={dag}
        tree={tree}
        nav={nav}
        canEdit={canEdit}
        onDeleted={(parent) => navigate(parent ? { view: "dag", exp: parent } : { view: "home" })}
      />
    );
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
        <span className="live" title={live ? "Live: the page updates as soon as something changes" : "Not connected for live updates; the page refreshes every few seconds"}>
          <i className={`live-dot ${live ? "" : "off"}`} /> {live ? "live" : "polling"}
        </span>
        <button className={`top-link ${inStorage ? "active" : ""}`} title="Storage use, deleting experiments and cleaning up" onClick={() => navigate({ view: "storage" })}>
          Storage
        </button>
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
