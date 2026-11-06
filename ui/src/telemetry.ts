// Pure helpers for run telemetry: console rendering, scalar grouping, smoothing and downsampling.
import type { ScalarPoint } from "./api";

export interface Span {
  text: string;
  fg?: string;
  bold?: boolean;
}

const BASIC = ["#000", "#c33", "#3a3", "#c93", "#36c", "#a3a", "#3aa", "#ccc"];
const BRIGHT = ["#666", "#f55", "#5d5", "#fd5", "#59f", "#d5d", "#5dd", "#fff"];

function color256(n: number): string {
  if (n < 8) return BASIC[n];
  if (n < 16) return BRIGHT[n - 8];
  if (n >= 232) {
    const v = 8 + (n - 232) * 10;
    return `rgb(${v},${v},${v})`;
  }
  const i = n - 16;
  const c = (x: number) => (x ? 55 + x * 40 : 0);
  return `rgb(${c(Math.floor(i / 36))},${c(Math.floor(i / 6) % 6)},${c(i % 6)})`;
}

/** Split text with ANSI SGR codes into styled spans; other escape sequences are dropped. */
export function parseAnsi(text: string): Span[] {
  const out: Span[] = [];
  let fg: string | undefined;
  let bold = false;
  // biome-ignore lint/suspicious/noControlCharactersInRegex: matching terminal escape sequences
  const re = /\x1b\[([0-9;?]*)([A-Za-z])/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), fg, bold });
    last = re.lastIndex;
    if (m[2] !== "m") continue;
    const codes = m[1] === "" ? [0] : m[1].split(";").map(Number);
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i];
      if (c === 0) [fg, bold] = [undefined, false];
      else if (c === 1) bold = true;
      else if (c === 22) bold = false;
      else if (c === 39) fg = undefined;
      else if (c >= 30 && c <= 37) fg = BASIC[c - 30];
      else if (c >= 90 && c <= 97) fg = BRIGHT[c - 90];
      else if (c === 38 && codes[i + 1] === 5) {
        fg = color256(codes[i + 2] ?? 7);
        i += 2;
      } else if (c === 38 && codes[i + 1] === 2) {
        fg = `rgb(${codes[i + 2] ?? 0},${codes[i + 3] ?? 0},${codes[i + 4] ?? 0})`;
        i += 4;
      }
    }
  }
  if (last < text.length) out.push({ text: text.slice(last), fg, bold });
  return out.filter((s) => s.text);
}

/** Apply carriage returns the way a terminal shows them: the last rewrite of each line wins. */
export function collapseCR(text: string): string {
  if (!text.includes("\r")) return text;
  return text
    .split("\n")
    .map((line) => {
      const parts = line.replace(/\r+$/, "").split("\r");
      return parts[parts.length - 1];
    })
    .join("\n");
}

export interface Point {
  step: number;
  value: number;
  t?: number | null;
}
export interface Series {
  name: string;
  points: Point[];
  /** Which source (experiment) it came from, when charts overlay several. */
  source?: string;
}
export interface Chart {
  title: string;
  series: Series[];
}

/** Group metric points into charts: ``title/series`` keys share a chart; a plain key is its own. */
export function groupScalars(points: ScalarPoint[]): Chart[] {
  const charts = new Map<string, Map<string, Series>>();
  for (const p of points) {
    const i = p.key.indexOf("/");
    const [title, name] = i > 0 && i < p.key.length - 1 ? [p.key.slice(0, i), p.key.slice(i + 1)] : [p.key, p.key];
    const c = charts.get(title) ?? new Map<string, Series>();
    charts.set(title, c);
    const s = c.get(name) ?? { name, points: [] };
    c.set(name, s);
    s.points.push({ step: p.step, value: p.value, t: p.t });
  }
  return [...charts].map(([title, c]) => ({
    title,
    series: [...c.values()].map((s) => ({ ...s, points: s.points.sort((a, b) => a.step - b.step) })),
  }));
}

/** Exponential moving average with debiasing (TensorBoard's smoothing); 0 leaves values as they are. */
export function smooth(values: number[], weight: number): number[] {
  if (weight <= 0) return values;
  let last = 0;
  let n = 0;
  return values.map((v) => {
    last = last * weight + (1 - weight) * v;
    n++;
    return last / (1 - weight ** n);
  });
}

/** At most ~``buckets * 2`` points: each bucket keeps its min and max, so spikes stay visible. */
export function downsample<T extends { value: number }>(points: T[], buckets = 400): T[] {
  if (points.length <= buckets * 2) return points;
  const size = points.length / buckets;
  const out: T[] = [];
  for (let b = 0; b < buckets; b++) {
    const slice = points.slice(Math.floor(b * size), Math.floor((b + 1) * size));
    if (!slice.length) continue;
    let lo = slice[0],
      hi = slice[0];
    for (const p of slice) {
      if (p.value < lo.value) lo = p;
      if (p.value > hi.value) hi = p;
    }
    out.push(...(slice.indexOf(lo) <= slice.indexOf(hi) ? [lo, hi] : [hi, lo]));
  }
  return out;
}

export const SERIES_COLORS = ["var(--accent)", "#e8833a", "#3aa675", "#c94f7c", "#7b6cd9", "#c9a227", "#3a9ec9"];

/** Console lines built incrementally: finished lines plus the line still being written. */
export interface LineBuffer {
  lines: string[];
  partial: string;
}

/** Append raw output to ``buf``; carriage returns are applied as each line completes. */
export function appendLines(buf: LineBuffer, text: string): LineBuffer {
  const parts = (buf.partial + text).split("\n");
  const partial = parts.pop() ?? "";
  if (!parts.length) return { lines: buf.lines, partial };
  return { lines: buf.lines.concat(parts.map(collapseCR)), partial };
}

/** Charts from several sources (e.g. experiments), merged by title; series are prefixed with the
 *  source's label when there is more than one source. */
export function mergeSources(sources: { label: string; points: ScalarPoint[] }[]): Chart[] {
  const many = sources.length > 1;
  const byTitle = new Map<string, Series[]>();
  for (const src of sources) {
    for (const c of groupScalars(src.points)) {
      const list = byTitle.get(c.title) ?? [];
      byTitle.set(c.title, list);
      for (const s of c.series)
        list.push({
          ...s,
          source: src.label,
          name: many ? (s.name === c.title ? src.label : `${src.label} · ${s.name}`) : s.name,
        });
    }
  }
  return [...byTitle].map(([title, series]) => ({ title, series }));
}

export type XAxis = "step" | "time";

/** The x of each point: its step, or seconds since the series' first logged point. Points without
 *  a time are dropped on the time axis. */
export function xOf(points: Point[], axis: XAxis): { x: number; value: number; step: number }[] {
  if (axis === "step") return points.map((p) => ({ x: p.step, value: p.value, step: p.step }));
  const timed = points.filter((p) => p.t != null);
  const t0 = Math.min(...timed.map((p) => p.t as number));
  return timed.map((p) => ({ x: (p.t as number) - t0, value: p.value, step: p.step }));
}

/** Seconds as a short duration for axis ticks: 45s, 3m 20s, 2h 5m. */
export function fmtSeconds(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60 >= 1 ? ` ${Math.round(s % 60)}s` : ""}`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
}
