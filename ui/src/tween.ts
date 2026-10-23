// Smooth graph changes: edges morph from their old route to the new one, in step with the CSS
// transition that moves nodes (same duration and easing), instead of being replaced.
import { useEffect, useRef, useState } from "react";
import type { Point } from "./layout";

export const MOVE_MS = 350;
const SAMPLES = 32;

/** Material "standard" easing, matching the nodes' `cubic-bezier(.4, 0, .2, 1)` closely enough. */
export const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** ``n`` points evenly spaced along a polyline, so two routes with different bends can be blended. */
export function resample(pts: Point[], n = SAMPLES): Point[] {
  if (pts.length === 0) return [];
  if (pts.length === 1) return Array.from({ length: n }, () => ({ ...pts[0] }));
  const seg = pts.slice(1).map((p, i) => Math.hypot(p.x - pts[i].x, p.y - pts[i].y));
  const total = seg.reduce((a, b) => a + b, 0) || 1;
  const out: Point[] = [];
  let i = 0, walked = 0;
  for (let k = 0; k < n; k++) {
    const target = (total * k) / (n - 1);
    while (i < seg.length - 1 && walked + seg[i] < target) walked += seg[i++];
    const f = seg[i] ? Math.min(1, (target - walked) / seg[i]) : 0;
    out.push({ x: pts[i].x + (pts[i + 1].x - pts[i].x) * f, y: pts[i].y + (pts[i + 1].y - pts[i].y) * f });
  }
  return out;
}

export function blend(a: Point[], b: Point[], t: number): Point[] {
  return a.map((p, i) => ({ x: p.x + (b[i].x - p.x) * t, y: p.y + (b[i].y - p.y) * t }));
}

const same = (a: Point[], b: Point[]) => a.length === b.length && a.every((p, i) => p.x === b[i].x && p.y === b[i].y);

/** The route to draw now: the target, or a blend of the previous and the new route while it moves. */
export function useTweenedPoints(target: Point[]): Point[] {
  const [shown, setShown] = useState(target);
  const current = useRef(target); // what is on screen, so an interrupted move continues from there
  useEffect(() => {
    if (same(current.current, target)) return;
    if (typeof requestAnimationFrame === "undefined" || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      current.current = target;
      setShown(target);
      return;
    }
    const from = resample(current.current), to = resample(target);
    const t0 = performance.now();
    let raf = 0;
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / MOVE_MS);
      if (k < 1) {
        current.current = blend(from, to, ease(k));
        setShown(current.current);
        raf = requestAnimationFrame(step);
      } else {
        current.current = target;
        setShown(target);
      }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return shown;
}
