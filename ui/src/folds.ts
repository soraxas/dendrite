// How the graph is folded, per pipeline: the Overview / All steps switch and the stages and
// fan-outs the user flipped from their default. Kept in localStorage so a refresh, or a new run
// of the same pipeline, opens the way you left it. Storage may be unavailable (private windows,
// blocked site data): then folds just last for the session.
import { useCallback, useEffect, useState } from "react";

export interface Folds {
  showSteps: boolean;
  stages: string[]; // stage ids flipped from the default
  fan: string[]; // fan-out steps flipped from the default
}

const KEY = "dendrite.folds.v1";
const EMPTY: Folds = { showSteps: false, stages: [], fan: [] };

export function loadAll(): Record<string, Folds> {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveAll(all: Record<string, Folds>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    /* per-browser convenience only */
  }
}

const flip = (list: string[], item: string) => (list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);

export function useFolds(pipeline: string | undefined) {
  const [all, setAll] = useState<Record<string, Folds>>(loadAll);
  useEffect(() => saveAll(all), [all]);
  const folds = (pipeline && all[pipeline]) || EMPTY;
  const update = useCallback(
    (change: (f: Folds) => Folds) => {
      if (!pipeline) return;
      setAll((a) => ({ ...a, [pipeline]: change(a[pipeline] ?? EMPTY) }));
    },
    [pipeline],
  );
  return {
    folds,
    toggleStage: useCallback((id: string) => update((f) => ({ ...f, stages: flip(f.stages, id) })), [update]),
    toggleFan: useCallback((step: string) => update((f) => ({ ...f, fan: flip(f.fan, step) })), [update]),
    // Switching level starts every stage from that level's default again.
    setShowSteps: useCallback((show: boolean) => update((f) => ({ ...f, showSteps: show, stages: [] })), [update]),
  };
}
