// An experiment's Artifacts tab: every input and output grouped stage → step, filters, and Compare.
import { useQueries } from "@tanstack/react-query";
import { type ReactNode, useMemo, useState } from "react";
import { type ArtifactRef, api, type Dag } from "../api";
import {
  type ArtifactRow,
  artifactRows,
  compareCandidates,
  FILTER_KINDS,
  filterRows,
  groupRows,
  KIND_LABEL,
} from "../artifacts";
import { COLLAPSE_AT } from "../fanout";
import { fmtSize, fmtVal, short } from "../format";
import { useArtifact, useExperiments } from "../queries";
import type { Route } from "../router";
import { ArtifactIcon } from "./ArtifactIcon";
import { ArtifactPreview } from "./ArtifactPreview";

const SOURCE_LABEL = { input: "input", computed: "computed", reused: "reused", pinned: "frozen", pending: "…" };

function Thumb({ r }: { r: ArtifactRef }) {
  if (r.kind === "file" && /\.(png|jpe?g|gif|webp|svg)$/i.test(r.filename ?? ""))
    return <img className="art-thumb" src={api.downloadUrl(r.hash)} alt="" loading="lazy" />;
  return <ArtifactIcon kind={r.kind} filename={r.filename} />;
}

function Row({
  r,
  selected,
  onSelect,
  onCompare,
  nameOf,
  openExp,
}: {
  r: ArtifactRow;
  selected: boolean;
  onSelect: () => void;
  onCompare?: () => void;
  nameOf: (id: string) => string;
  openExp: (id: string) => void;
}) {
  return (
    <tr className={selected ? "selected" : ""} onClick={onSelect}>
      <td className="thumb">
        <Thumb r={r.ref} />
      </td>
      <td>
        <b className="mono">{r.item != null ? `[${r.item}] ${r.output}` : r.output}</b>
        {r.names.map((n) => (
          <span key={n} className="name-chip" style={{ marginLeft: 6 }}>
            ★ @{n}
          </span>
        ))}
        <div className="muted mono">{r.ref.filename ?? r.ref.kind}</div>
      </td>
      <td className="num">{fmtSize(r.ref.size)}</td>
      <td>
        <span className={`src src-${r.source}`}>{SOURCE_LABEL[r.source]}</span>
        {r.from && (
          <>
            {" from "}
            <a
              onClick={(e) => {
                e.stopPropagation();
                openExp(r.from!);
              }}
            >
              {nameOf(r.from)}
            </a>
          </>
        )}
      </td>
      <td className="actions">
        {onCompare && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onCompare();
            }}
            title="See this output side by side across this experiment, its parent and its forks"
          >
            Compare
          </button>
        )}
      </td>
    </tr>
  );
}

export function ArtifactsPage({
  dag,
  route,
  go,
  select,
  nameOf,
  openExp,
}: {
  dag: Dag;
  route: Route;
  go: (r: Route) => void;
  select: (hash: string) => void;
  nameOf: (id: string) => string;
  openExp: (id: string) => void;
}) {
  const rows = useMemo(() => artifactRows(dag), [dag]);
  const shown = useMemo(() => filterRows(rows, route.kind, route.q), [rows, route.kind, route.q]);
  const groups = useMemo(() => groupRows(shown), [shown]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const counts = useMemo(() => {
    const c = new Map<string, number>();
    for (const r of filterRows(rows, undefined, route.q)) c.set(r.kind, (c.get(r.kind) ?? 0) + 1);
    return c;
  }, [rows, route.q]);
  const set = (patch: Partial<Route>) => go({ ...route, ...patch });
  const selected = route.sel?.startsWith("artifact:") ? route.sel.slice(9) : undefined;
  const [step, out] = route.cmp
    ? [route.cmp.slice(0, route.cmp.lastIndexOf(".")), route.cmp.slice(route.cmp.lastIndexOf(".") + 1)]
    : [];

  return (
    <div className="pad artifacts">
      {route.cmp && step && out && (
        <Compare
          base={dag}
          node={step}
          output={out}
          nameOf={nameOf}
          openExp={openExp}
          close={() => set({ cmp: undefined })}
        />
      )}
      <div className="art-filters">
        <div className="tabs" role="tablist">
          <button
            role="tab"
            aria-selected={!route.kind}
            className={route.kind ? "" : "active"}
            onClick={() => set({ kind: undefined })}
          >
            All {filterRows(rows, undefined, route.q).length}
          </button>
          {FILTER_KINDS.filter((k) => counts.get(k)).map((k) => (
            <button
              key={k}
              role="tab"
              aria-selected={route.kind === k}
              className={route.kind === k ? "active" : ""}
              onClick={() => set({ kind: k })}
            >
              {KIND_LABEL[k]} {counts.get(k)}
            </button>
          ))}
        </div>
        <input
          type="search"
          className="item-search"
          placeholder="Filter by step, output or name"
          aria-label="Filter artifacts"
          value={route.q ?? ""}
          onChange={(e) => go({ ...route, q: e.target.value || undefined })}
        />
      </div>
      {!rows.length && <p className="muted">Nothing produced yet.</p>}
      {rows.length > 0 && !shown.length && <p className="muted">No artifact matches the filter.</p>}
      {groups.map((g) => (
        <section key={g.stage || "-"} className="art-stage">
          {g.stage && <h3>{g.stage}</h3>}
          <table className="art-table">
            <tbody>
              {g.steps.map((s) => {
                const fan = s.items >= COLLAPSE_AT && !open.has(s.step) && !route.q;
                return [
                  <tr key={`h:${s.step}`} className="art-step">
                    <td colSpan={5}>
                      <span className="mono">{s.step}</span>
                      {s.items > 0 && (
                        <>
                          {" "}
                          <span className="muted">· {s.items} items</span>{" "}
                          {s.items >= COLLAPSE_AT && !route.q && (
                            <a
                              onClick={() => {
                                const next = new Set(open);
                                if (next.has(s.step)) next.delete(s.step);
                                else next.add(s.step);
                                setOpen(next);
                              }}
                            >
                              {fan ? "show" : "hide"}
                            </a>
                          )}
                        </>
                      )}
                    </td>
                  </tr>,
                  ...(fan
                    ? []
                    : s.rows.map((r) => (
                        <Row
                          key={r.id}
                          r={r}
                          selected={selected === r.ref.hash}
                          onSelect={() => select(r.ref.hash)}
                          onCompare={r.node ? () => set({ cmp: `${r.node}.${r.output}` }) : undefined}
                          nameOf={nameOf}
                          openExp={openExp}
                        />
                      ))),
                ];
              })}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

function CompareColumn({
  label,
  sub,
  hash,
  same,
}: {
  label: ReactNode;
  sub: ReactNode;
  hash: string | undefined;
  same: boolean;
}) {
  const { data } = useArtifact(!same && hash ? hash : undefined);
  return (
    <div className="cmp-col">
      <div className="cmp-head">
        {label}
        <div className="muted cmp-sub">{sub}</div>
      </div>
      {!hash ? (
        <div className="muted">not produced</div>
      ) : same ? (
        <div className="muted">identical to this experiment's ({short(hash)})</div>
      ) : data ? (
        <ArtifactPreview artifact={data} />
      ) : (
        <div className="muted">Loading…</div>
      )}
    </div>
  );
}

/** One ``node.output`` side by side across this experiment, its parent and its forks. */
export function Compare({
  base,
  node,
  output,
  nameOf,
  openExp,
  close,
}: {
  base: Dag;
  node: string;
  output: string;
  nameOf: (id: string) => string;
  openExp: (id: string) => void;
  close: () => void;
}) {
  const { data: tree = [] } = useExperiments();
  const { ids, more } = compareCandidates(tree, base.experiment.id);
  const others = ids.slice(1);
  const dags = useQueries({
    queries: others.map((id) => ({ queryKey: ["dag", id], queryFn: () => api.dag(id), staleTime: 10000 })),
  });
  const diffs = useQueries({
    queries: others.map((id) => ({
      queryKey: ["diff", base.experiment.id, id],
      queryFn: () => api.diff(base.experiment.id, id),
      staleTime: 10000,
    })),
  });
  const hashOf = (d?: Dag) => d?.nodes.find((n) => n.name === node)?.outputs?.[output]?.hash;
  const mine = hashOf(base);
  return (
    <section className="compare-panel">
      <div className="cmp-title">
        <h3>
          Compare{" "}
          <span className="mono">
            {node}.{output}
          </span>
        </h3>
        <button type="button" onClick={close}>
          Close
        </button>
      </div>
      <div className="cmp-cols">
        <CompareColumn label={<b>{nameOf(base.experiment.id)}</b>} sub="this experiment" hash={mine} same={false} />
        {others.map((id, i) => {
          const h = hashOf(dags[i].data);
          const changes = diffs[i].data?.params ?? [];
          const rel = id === base.experiment.parent_id ? "parent" : "fork";
          return (
            <CompareColumn
              key={id}
              label={<a onClick={() => openExp(id)}>{nameOf(id)}</a>}
              sub={
                <>
                  {rel}
                  {changes.length > 0 &&
                    `: ${changes
                      .slice(0, 3)
                      .map((c) => `${c.key} ${fmtVal(c.a)} → ${fmtVal(c.b)}`)
                      .join(", ")}${changes.length > 3 ? ` +${changes.length - 3}` : ""}`}
                </>
              }
              hash={dags[i].isLoading ? undefined : h}
              same={!!h && h === mine}
            />
          );
        })}
      </div>
      {others.length === 0 && <p className="muted">This experiment has no parent or forks to compare with.</p>}
      {more > 0 && <p className="muted">{more} more forks not shown.</p>}
    </section>
  );
}
