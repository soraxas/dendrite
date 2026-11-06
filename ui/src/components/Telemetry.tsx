// Run telemetry of one execution: console, scalar charts, logged samples and environment.
import { useQuery } from "@tanstack/react-query";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { api, direct, type LogChunk, type LogPage, type NodeScalars, type Sample, type ScalarPoint } from "../api";
import { fmtSize, short } from "../format";
import {
  appendLines,
  type Chart,
  collapseCR,
  downsample,
  groupScalars,
  type LineBuffer,
  parseAnsi,
  SERIES_COLORS,
  smooth,
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
  if (!all.length) return <div className="muted">{running ? "No output yet…" : "This run printed nothing."}</div>;
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
  H = 140,
  PAD = { l: 44, r: 8, t: 8, b: 20 };

function fmtTick(v: number): string {
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1e4 || a < 1e-3) return v.toExponential(1);
  return Number(v.toPrecision(3)).toString();
}

export function ScalarChart({ chart, weight }: { chart: Chart; weight: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const drawn = chart.series.map((s) => {
    const sm = smooth(
      s.points.map((p) => p.value),
      weight,
    );
    return {
      name: s.name,
      raw: s.points,
      points: downsample(s.points.map((p, i) => ({ step: p.step, value: sm[i] }))),
    };
  });
  const all = drawn.flatMap((s) => s.points);
  if (!all.length) return null;
  const xs = all.map((p) => p.step),
    ys = all.map((p) => p.value);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  let [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  if (y0 === y1) [y0, y1] = [y0 - 1, y1 + 1];
  const X = (x: number) => PAD.l + ((x - x0) / (x1 - x0 || 1)) * (W - PAD.l - PAD.r);
  const Y = (y: number) => PAD.t + (1 - (y - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
  const single = all.length === drawn.length; // one point per series: show values, not a chart
  const at = (s: (typeof drawn)[number]) =>
    hover == null
      ? s.raw[s.raw.length - 1]
      : s.raw.reduce((b, p) => (Math.abs(p.step - hover) < Math.abs(b.step - hover) ? p : b));
  return (
    <figure className="scalar-chart">
      <figcaption className="mono">{chart.title}</figcaption>
      {!single && (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width="100%"
          role="img"
          aria-label={`${chart.title} chart`}
          onMouseMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const px = ((e.clientX - r.left) / r.width) * W;
            setHover(x0 + ((px - PAD.l) / (W - PAD.l - PAD.r)) * (x1 - x0));
          }}
          onMouseLeave={() => setHover(null)}
        >
          {[y0, (y0 + y1) / 2, y1].map((v) => (
            <g key={v}>
              <line x1={PAD.l} x2={W - PAD.r} y1={Y(v)} y2={Y(v)} stroke="var(--line)" />
              <text x={PAD.l - 4} y={Y(v) + 3} textAnchor="end" className="tick">
                {fmtTick(v)}
              </text>
            </g>
          ))}
          <text x={PAD.l} y={H - 4} className="tick">
            {x0}
          </text>
          <text x={W - PAD.r} y={H - 4} textAnchor="end" className="tick">
            {x1}
          </text>
          {drawn.map((s, i) => (
            <polyline
              key={s.name}
              fill="none"
              stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
              strokeWidth={1.6}
              points={s.points.map((p) => `${X(p.step).toFixed(1)},${Y(p.value).toFixed(1)}`).join(" ")}
            />
          ))}
          {hover != null && hover >= x0 && hover <= x1 && (
            <line x1={X(hover)} x2={X(hover)} y1={PAD.t} y2={H - PAD.b} stroke="var(--line-strong)" />
          )}
        </svg>
      )}
      <div className="scalar-legend">
        {drawn.map((s, i) => {
          const p = at(s);
          return (
            <span key={s.name}>
              <i style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />
              {chart.series.length > 1 || s.name !== chart.title ? `${s.name} ` : ""}
              <b className="num">{fmtTick(p.value)}</b>
              {!single && <span className="muted"> @ {p.step}</span>}
            </span>
          );
        })}
      </div>
    </figure>
  );
}

export function ScalarCharts({ points }: { points: ScalarPoint[] }) {
  const [weight, setWeight] = useState(0);
  const charts = useMemo(() => groupScalars(points), [points]);
  if (!charts.length) return <div className="muted">No metrics logged. Call log_metric("loss/train", v, step=i).</div>;
  return (
    <div>
      {charts.some((c) => c.series.some((s) => s.points.length > 2)) && (
        <label className="muted smoothing">
          smoothing {weight.toFixed(2)}
          <input
            type="range"
            min={0}
            max={0.99}
            step={0.01}
            value={weight}
            onChange={(e) => setWeight(Number(e.target.value))}
          />
        </label>
      )}
      <div className="scalar-grid">
        {charts.map((c) => (
          <ScalarChart key={c.title} chart={c} weight={weight} />
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
  return <ScalarCharts points={data.points} />;
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
      <dl className="tech">
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
export function ExperimentScalars({ expId, live }: { expId: string; live: boolean }) {
  const { data } = useQuery({
    queryKey: ["experiment-scalars", expId, live],
    queryFn: () => api.experimentScalars(expId),
    refetchInterval: live ? 3000 : false,
  });
  if (!data) return <div className="muted">Loading…</div>;
  const nodes: NodeScalars[] = data.nodes;
  if (!nodes.length) return <div className="muted">No step logged metrics yet.</div>;
  return (
    <div>
      {nodes.map((n) => (
        <section key={n.node}>
          <h3 className="mono">{n.node}</h3>
          <ScalarCharts points={n.points} />
        </section>
      ))}
    </div>
  );
}
