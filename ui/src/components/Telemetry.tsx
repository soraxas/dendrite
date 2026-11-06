// Run telemetry of one execution: console, scalar charts, logged samples and environment.
import { useQueries, useQuery } from "@tanstack/react-query";
import { type MouseEvent, memo, useEffect, useMemo, useRef, useState } from "react";
import { api, direct, type LogChunk, type LogPage, type NodeScalars, type Sample } from "../api";
import { fmtSize, short } from "../format";
import { useExperiments } from "../queries";
import {
  appendLines,
  type Chart,
  collapseCR,
  downsample,
  fmtSeconds,
  groupScalars,
  type LineBuffer,
  mergeSources,
  parseAnsi,
  SERIES_COLORS,
  smooth,
  type XAxis,
  xOf,
} from "../telemetry";

type Tab = "console" | "scalars" | "samples" | "environment";
const POLL_MS = 1000;
const MAX_LINES = 2000; // lines rendered unless filtering or "show all"; Download has everything

/** Console output of an execution as lines, appended as chunks arrive while it runs. */
export function useLog(execId: string) {
  const [buf, setBuf] = useState<LineBuffer>({ lines: [], partial: "" });
  const [tail, setTail] = useState<LogChunk | null>(null);
  const [running, setRunning] = useState(false);
  const [stored, setStored] = useState<LogPage["stored"]>(null);
  const raw = useRef<string[]>([]); // for "Download"
  useEffect(() => {
    let stop = false;
    let after = -1;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setBuf({ lines: [], partial: "" });
    setTail(null);
    raw.current = [];
    const tick = async () => {
      try {
        const page = await api.logs(execId, after);
        if (stop) return;
        const fresh = page.chunks.filter((c) => c.seq >= 0);
        if (fresh.length) {
          after = fresh[fresh.length - 1].seq;
          const text = fresh.map((c) => c.text).join("");
          raw.current.push(text);
          setBuf((prev) => appendLines(prev, text));
        }
        setTail(page.chunks.find((c) => c.seq < 0) ?? null);
        setRunning(page.running);
        setStored(page.stored ?? null);
        if (page.stored && !page.running && after >= 0 && !fresh.length && raw.current.length) {
          // The run just ended and its log moved to the store: start over from the stored copy.
          after = -1;
          raw.current = [];
          setBuf({ lines: [], partial: "" });
          timer = setTimeout(tick, 0);
          return;
        }
        if (page.running || page.chunks.length >= 2000) timer = setTimeout(tick, page.running ? POLL_MS : 0);
      } catch {
        if (!stop) timer = setTimeout(tick, POLL_MS * 5);
      }
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [execId]);
  return { buf, tail, running, raw, stored };
}

const Line = memo(function Line({ text }: { text: string }) {
  return (
    <div>
      {parseAnsi(text).map((s, j) => (
        <span key={j} style={{ color: s.fg, fontWeight: s.bold ? 600 : undefined }}>
          {s.text}
        </span>
      ))}
      {"\n"}
    </div>
  );
});

function Console({ execId }: { execId: string }) {
  const { buf, tail, running, raw, stored } = useLog(execId);
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const box = useRef<HTMLPreElement>(null);
  const all = useMemo(() => {
    const ls = buf.partial ? [...buf.lines, collapseCR(buf.partial)] : buf.lines;
    if (!tail) return ls;
    return [...ls, "… (live tail of a log past its size limit) …", ...collapseCR(tail.text).split("\n")];
  }, [buf, tail]);
  const filtered = useMemo(() => {
    if (!query) return all;
    const q = query.toLowerCase();
    return all.filter((l) => l.toLowerCase().includes(q));
  }, [all, query]);
  const hidden = showAll || query ? 0 : Math.max(0, filtered.length - MAX_LINES);
  const shown = hidden ? filtered.slice(hidden) : filtered;
  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll whenever new lines arrive
  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [shown, follow]);
  const download = () => {
    if (stored) {
      window.open(direct(api.downloadUrl(stored.hash)), "_blank", "noopener");
      return;
    }
    const url = URL.createObjectURL(new Blob(raw.current, { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${execId}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };
  if (!all.length)
    return (
      <div className="muted">
        {running ? "No output yet…" : "This run printed nothing."}{" "}
        <span className="hint">
          Output from compiled code and subprocesses is captured while one step runs at a time; with steps running in
          parallel, use <code>dendrite.run</code> for subprocesses.
        </span>
      </div>
    );
  return (
    <div className="console">
      <div className="console-bar">
        <input
          type="search"
          className="item-search"
          placeholder="Filter lines"
          aria-label="Filter console lines"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="muted">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow
        </label>
        <button type="button" onClick={download}>
          Download
        </button>
        {running && (
          <span className="badge" style={{ color: "var(--running)" }}>
            live
          </span>
        )}
      </div>
      {stored && stored.shown < stored.size && (
        <div className="muted console-note">
          Showing the last {fmtSize(stored.shown)} of {fmtSize(stored.size)}.{" "}
          <a href={direct(api.downloadUrl(stored.hash))} target="_blank" rel="noreferrer">
            Download the whole log
          </a>
        </div>
      )}
      <pre
        ref={box}
        className="console-text"
        onScroll={(e) => {
          const el = e.currentTarget;
          setFollow(el.scrollTop + el.clientHeight >= el.scrollHeight - 8);
        }}
      >
        {hidden > 0 && (
          <div className="muted">
            {hidden.toLocaleString()} earlier lines hidden · <a onClick={() => setShowAll(true)}>show all</a> or filter
            to search them
          </div>
        )}
        {shown.map((line, i) => (
          <Line key={hidden + i} text={line} />
        ))}
      </pre>
    </div>
  );
}

const W = 320,
  H = 150,
  PAD = { l: 46, r: 8, t: 8, b: 22 };

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumSignificantDigits: 3 });

/** Axis and readout numbers: 60k, 1.2M, 0.0276, 3.1e-5. */
export function fmtTick(v: number): string {
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1e4) return compact.format(v).toLowerCase();
  if (a < 1e-3) return v.toExponential(1);
  return Number(v.toPrecision(3)).toString();
}

export interface ChartView {
  weight: number;
  axis: XAxis;
  logY: boolean;
  /** The x range zoomed into, shared by every chart in a group. */
  zoom: [number, number] | null;
}

/** One chart: a line per series, hover readout, drag to zoom, double-click to reset. */
export function ScalarChart({
  chart,
  view,
  onZoom,
}: {
  chart: Chart;
  view: ChartView;
  onZoom: (z: [number, number] | null) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);
  const drawn = chart.series.map((s, i) => {
    const pts = xOf(s.points, view.axis);
    const sm = smooth(
      pts.map((p) => p.value),
      view.weight,
    );
    const all = pts.map((p, j) => ({ ...p, value: sm[j] }));
    const visible = view.zoom ? all.filter((p) => p.x >= view.zoom![0] && p.x <= view.zoom![1]) : all;
    return { name: s.name, color: SERIES_COLORS[i % SERIES_COLORS.length], raw: pts, points: downsample(visible) };
  });
  const all = drawn.flatMap((s) => s.points);
  const single = drawn.every((s) => s.raw.length <= 1);
  const logY = view.logY && all.every((p) => p.value > 0);
  const fy = (v: number) => (logY ? Math.log10(v) : v);
  const xs = all.map((p) => p.x),
    ys = all.map((p) => fy(p.value));
  const [x0, x1] = view.zoom ?? [Math.min(...xs), Math.max(...xs)];
  let [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  if (y0 === y1) [y0, y1] = [y0 - 1, y1 + 1];
  const X = (x: number) => PAD.l + ((x - x0) / (x1 - x0 || 1)) * (W - PAD.l - PAD.r);
  const Y = (v: number) => PAD.t + (1 - (fy(v) - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
  const toX = (e: MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    return x0 + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (x1 - x0);
  };
  const fmtX = (x: number) => (view.axis === "time" ? fmtSeconds(x) : fmtTick(Math.round(x)));
  const nearest = (s: (typeof drawn)[number]) => {
    if (!s.raw.length) return null;
    if (hover == null) return s.raw[s.raw.length - 1];
    return s.raw.reduce((b, p) => (Math.abs(p.x - hover) < Math.abs(b.x - hover) ? p : b));
  };
  return (
    <figure className="scalar-chart">
      <figcaption className="mono">{chart.title}</figcaption>
      {!single && all.length > 0 && (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          role="img"
          aria-label={`${chart.title} chart`}
          className={drag ? "dragging" : undefined}
          onMouseDown={(e) => {
            const x = toX(e);
            setDrag([x, x]);
          }}
          onMouseMove={(e) => {
            const x = toX(e);
            setHover(x);
            if (drag) setDrag([drag[0], x]);
          }}
          onMouseUp={() => {
            if (drag && Math.abs(X(drag[1]) - X(drag[0])) > 6)
              onZoom([Math.min(...drag), Math.max(...drag)] as [number, number]);
            setDrag(null);
          }}
          onMouseLeave={() => {
            setHover(null);
            setDrag(null);
          }}
          onDoubleClick={() => onZoom(null)}
        >
          {[0, 0.5, 1].map((f) => {
            const v = y0 + (y1 - y0) * f;
            const label = logY ? 10 ** v : v;
            const y = PAD.t + (1 - f) * (H - PAD.t - PAD.b);
            return (
              <g key={f}>
                <line x1={PAD.l} x2={W - PAD.r} y1={y} y2={y} stroke="var(--line)" />
                <text x={PAD.l - 4} y={y + 3} textAnchor="end" className="tick">
                  {fmtTick(label)}
                </text>
              </g>
            );
          })}
          <text x={PAD.l} y={H - 6} className="tick">
            {fmtX(x0)}
          </text>
          <text x={W - PAD.r} y={H - 6} textAnchor="end" className="tick">
            {fmtX(x1)}
          </text>
          {drawn.map((s) => (
            <polyline
              key={s.name}
              fill="none"
              stroke={s.color}
              strokeWidth={1.6}
              points={s.points.map((p) => `${X(p.x).toFixed(1)},${Y(p.value).toFixed(1)}`).join(" ")}
            />
          ))}
          {hover != null && hover >= x0 && hover <= x1 && !drag && (
            <line x1={X(hover)} x2={X(hover)} y1={PAD.t} y2={H - PAD.b} stroke="var(--line-strong)" />
          )}
          {drag && (
            <rect
              x={Math.min(X(drag[0]), X(drag[1]))}
              y={PAD.t}
              width={Math.abs(X(drag[1]) - X(drag[0]))}
              height={H - PAD.t - PAD.b}
              className="zoom-box"
            />
          )}
        </svg>
      )}
      <div className="scalar-legend">
        {drawn.map((s) => {
          const p = nearest(s);
          if (!p) return null;
          return (
            <span key={s.name}>
              <i style={{ background: s.color }} />
              {chart.series.length > 1 || s.name !== chart.title ? `${s.name} ` : ""}
              <b className="num">{fmtTick(p.value)}</b>
              {!single && <span className="muted"> @ {view.axis === "time" ? fmtSeconds(p.x) : `step ${p.step}`}</span>}
            </span>
          );
        })}
      </div>
    </figure>
  );
}

/** A grid of charts with shared controls: smoothing, step or time axis, log scale and zoom. */
export function ScalarCharts({ charts }: { charts: Chart[] }) {
  const [view, setView] = useState<ChartView>({ weight: 0, axis: "step", logY: false, zoom: null });
  if (!charts.length) return <div className="muted">No metrics logged. Call log_metric("loss/train", v, step=i).</div>;
  const points = charts.flatMap((c) => c.series.flatMap((s) => s.points));
  const timed = points.some((p) => p.t != null);
  const positive = points.every((p) => p.value > 0);
  const many = charts.some((c) => c.series.some((s) => s.points.length > 2));
  return (
    <div>
      {many && (
        <div className="chart-controls">
          <label className="muted smoothing">
            smoothing {view.weight.toFixed(2)}
            <input
              type="range"
              min={0}
              max={0.99}
              step={0.01}
              value={view.weight}
              aria-label="Smoothing"
              onChange={(e) => setView({ ...view, weight: Number(e.target.value) })}
            />
          </label>
          <div className="tabs" role="group" aria-label="X axis">
            {(["step", "time"] as const).map((a) => (
              <button
                key={a}
                type="button"
                aria-pressed={view.axis === a}
                className={view.axis === a ? "active" : ""}
                disabled={a === "time" && !timed}
                title={a === "time" && !timed ? "These points were recorded without a time" : undefined}
                onClick={() => setView({ ...view, axis: a, zoom: null })}
              >
                {a === "step" ? "Step" : "Time"}
              </button>
            ))}
          </div>
          <label className="muted" title={positive ? undefined : "A log scale needs positive values"}>
            <input
              type="checkbox"
              checked={view.logY && positive}
              disabled={!positive}
              onChange={(e) => setView({ ...view, logY: e.target.checked })}
            />{" "}
            log scale
          </label>
          {view.zoom ? (
            <button type="button" onClick={() => setView({ ...view, zoom: null })}>
              Reset zoom
            </button>
          ) : (
            <span className="muted hint">drag across a chart to zoom</span>
          )}
        </div>
      )}
      <div className="scalar-grid">
        {charts.map((c) => (
          <ScalarChart key={c.title} chart={c} view={view} onZoom={(zoom) => setView({ ...view, zoom })} />
        ))}
      </div>
    </div>
  );
}

function Scalars({ execId, running }: { execId: string; running: boolean }) {
  const { data } = useQuery({
    queryKey: ["scalars", execId, running],
    queryFn: () => api.scalars(execId),
    refetchInterval: running ? 2000 : false,
  });
  if (!data) return <div className="muted">Loading…</div>;
  return <ScalarCharts charts={groupScalars(data.points)} />;
}

function SampleGroup({ items }: { items: Sample[] }) {
  const [idx, setIdx] = useState(items.length - 1);
  const latest = items.length - 1;
  const i = Math.min(idx, latest);
  // Follow new steps while looking at the latest one.
  const prevLen = useRef(items.length);
  useEffect(() => {
    if (idx === prevLen.current - 1) setIdx(items.length - 1);
    prevLen.current = items.length;
  }, [items.length, idx]);
  const s = items[i];
  const url = api.downloadUrl(s.hash);
  const name = s.filename ?? "";
  const isImg = /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name);
  const isVideo = /\.(mp4|webm|mov|m4v)$/i.test(name);
  const isAudio = /\.(wav|mp3|ogg|flac|m4a)$/i.test(name);
  return (
    <figure className="sample">
      <figcaption className="mono">
        {s.title}
        {s.series !== s.title ? ` / ${s.series}` : ""} <span className="muted">step {s.step}</span>
      </figcaption>
      {isImg ? (
        <a href={direct(url)} target="_blank" rel="noreferrer">
          <img src={url} alt={`${s.title} at step ${s.step}`} loading="lazy" />
        </a>
      ) : isVideo ? (
        <video src={direct(url)} controls preload="metadata" />
      ) : isAudio ? (
        <audio src={direct(url)} controls preload="metadata" />
      ) : (
        <a href={direct(url)} target="_blank" rel="noreferrer" className="mono">
          {name || short(s.hash)} ({fmtSize(s.size)})
        </a>
      )}
      {items.length > 1 && (
        <input
          type="range"
          min={0}
          max={latest}
          value={i}
          aria-label={`${s.title} step`}
          onChange={(e) => setIdx(Number(e.target.value))}
        />
      )}
    </figure>
  );
}

function Samples({ execId, running }: { execId: string; running: boolean }) {
  const { data } = useQuery({
    queryKey: ["samples", execId, running],
    queryFn: () => api.samples(execId),
    refetchInterval: running ? 3000 : false,
  });
  if (!data) return <div className="muted">Loading…</div>;
  if (!data.samples.length)
    return <div className="muted">No samples logged. Call log_image("render", img, step=i) inside a step.</div>;
  const groups = new Map<string, Sample[]>();
  for (const s of data.samples) {
    const k = `${s.title}\u0000${s.series}`;
    groups.set(k, [...(groups.get(k) ?? []), s]);
  }
  return (
    <div className="sample-grid">
      {[...groups].map(([k, items]) => (
        <SampleGroup key={k} items={items} />
      ))}
    </div>
  );
}

function EnvironmentView({ execId }: { execId: string }) {
  const { data } = useQuery({ queryKey: ["environment", execId], queryFn: () => api.environment(execId) });
  const [showPkgs, setShowPkgs] = useState(false);
  if (!data) return <div className="muted">Loading…</div>;
  const env = data.environment;
  if (!env) return <div className="muted">No environment was recorded for this run (it predates capture).</div>;
  const g = env.git;
  return (
    <div className="env">
      <dl className="kv">
        {g ? (
          <>
            <dt>commit</dt>
            <dd className="mono">
              {g.commit ? short(g.commit, 12) : "?"} {g.branch && <span className="muted">({g.branch})</span>}{" "}
              {g.dirty && (
                <span className="badge" style={{ color: "var(--pinned)" }} title="uncommitted changes when it ran">
                  dirty
                </span>
              )}
            </dd>
            {g.remote && (
              <>
                <dt>remote</dt>
                <dd className="mono">{g.remote}</dd>
              </>
            )}
          </>
        ) : (
          <>
            <dt>git</dt>
            <dd className="muted">not a git checkout</dd>
          </>
        )}
        <dt>python</dt>
        <dd className="mono">
          {env.python.version} <span className="muted">{env.python.executable}</span>
        </dd>
        <dt>system</dt>
        <dd className="mono">
          {env.system.platform} <span className="muted">on {env.system.host}</span>
        </dd>
        {env.system.gpus.length > 0 && (
          <>
            <dt>gpus</dt>
            <dd className="mono">{env.system.gpus.join(", ")}</dd>
          </>
        )}
        {env.uv_lock && (
          <>
            <dt>uv.lock</dt>
            <dd className="mono">{short(env.uv_lock, 16)}</dd>
          </>
        )}
        {env.dendrite_env && (
          <>
            <dt>DENDRITE_ENV</dt>
            <dd className="mono">{env.dendrite_env}</dd>
          </>
        )}
        <dt>packages</dt>
        <dd>
          <a onClick={() => setShowPkgs(!showPkgs)}>
            {env.packages.length} installed {showPkgs ? "▴" : "▾"}
          </a>
        </dd>
      </dl>
      {showPkgs && <pre className="env-list">{env.packages.join("\n")}</pre>}
      {g?.diff && (
        <details>
          <summary>Uncommitted diff</summary>
          <pre className="env-list">{g.diff}</pre>
        </details>
      )}
    </div>
  );
}

/** Tabs for one execution's telemetry; ``from`` names the experiment that ran it when reused. */
export function RunTelemetry({
  execId,
  running,
  from,
  onOpenExperiment,
}: {
  execId: string;
  running: boolean;
  from?: { id: string; name: string | null } | null;
  onOpenExperiment?: (id: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("console");
  const tabs: [Tab, string][] = [
    ["console", "Console"],
    ["scalars", "Scalars"],
    ["samples", "Samples"],
    ["environment", "Environment"],
  ];
  return (
    <section className="run-telemetry">
      <h3>Run</h3>
      {from && (
        <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
          From the run in <a onClick={() => onOpenExperiment?.(from.id)}>{from.name || short(from.id)}</a>, which this
          step reused.
        </div>
      )}
      <div className="tabs" role="tablist" style={{ marginBottom: 8 }}>
        {tabs.map(([k, label]) => (
          <button
            key={k}
            role="tab"
            aria-selected={tab === k}
            className={tab === k ? "active" : ""}
            onClick={() => setTab(k)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "console" && <Console execId={execId} />}
      {tab === "scalars" && <Scalars execId={execId} running={running} />}
      {tab === "samples" && <Samples execId={execId} running={running} />}
      {tab === "environment" && <EnvironmentView execId={execId} />}
    </section>
  );
}

/** Every step's charts of an experiment (the Metrics page). */
/** Every step's charts of an experiment (the Metrics page), optionally overlaid with others. */
export function ExperimentScalars({
  expId,
  live,
  nameOf = (id) => id,
}: {
  expId: string;
  live: boolean;
  nameOf?: (id: string) => string;
}) {
  const { data } = useQuery({
    queryKey: ["experiment-scalars", expId, live],
    queryFn: () => api.experimentScalars(expId),
    refetchInterval: live ? 3000 : false,
  });
  const { data: tree = [] } = useExperiments();
  const [overlay, setOverlay] = useState<string[]>([]);
  const others = useQueries({
    queries: overlay.map((id) => ({
      queryKey: ["experiment-scalars", id, false],
      queryFn: () => api.experimentScalars(id),
      staleTime: 10000,
    })),
  });
  if (!data) return <div className="muted">Loading…</div>;
  const me = tree.find((e) => e.id === expId);
  const related = tree.filter((e) => e.id !== expId && (e.id === me?.parent_id || e.parent_id === expId));
  const rest = tree.filter((e) => e.id !== expId && !related.includes(e));
  const sources = [
    { id: expId, nodes: data.nodes },
    ...overlay.map((id, i) => ({ id, nodes: others[i].data?.nodes ?? [] })),
  ];
  const nodeNames = [...new Set(sources.flatMap((s) => s.nodes.map((n: NodeScalars) => n.node)))];
  const toggle = (id: string) => setOverlay(overlay.includes(id) ? overlay.filter((x) => x !== id) : [...overlay, id]);
  return (
    <div>
      {tree.length > 1 && (
        <div className="overlay-picker">
          <span className="muted">Overlay</span>
          {related.map((e) => (
            <button
              key={e.id}
              type="button"
              aria-pressed={overlay.includes(e.id)}
              className={overlay.includes(e.id) ? "active" : ""}
              onClick={() => toggle(e.id)}
              title={e.id === me?.parent_id ? "the parent of this experiment" : "a fork of this experiment"}
            >
              {nameOf(e.id)}
            </button>
          ))}
          {rest.length > 0 && (
            <select
              aria-label="Overlay another experiment"
              value=""
              onChange={(e) => e.target.value && toggle(e.target.value)}
            >
              <option value="">other experiment…</option>
              {rest.map((e) => (
                <option key={e.id} value={e.id}>
                  {overlay.includes(e.id) ? "✓ " : ""}
                  {nameOf(e.id)}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
      {!nodeNames.length && <div className="muted">No step logged metrics yet.</div>}
      {nodeNames.map((node) => (
        <section key={node}>
          <h3 className="mono">{node}</h3>
          <ScalarCharts
            charts={mergeSources(
              sources
                .map((src) => ({
                  label: nameOf(src.id),
                  points: src.nodes.find((n: NodeScalars) => n.node === node)?.points ?? [],
                }))
                .filter((src, i) => i === 0 || src.points.length || overlay.length),
            )}
          />
        </section>
      ))}
    </div>
  );
}
