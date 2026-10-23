import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useSyncExternalStore } from "react";
import { ACTIVE, api, ApiError, type Dag } from "./api";

// Live updates: the server pushes "this changed" events (Server-Sent Events, /api/events), so
// pages refetch only what changed. Polling remains as a slow safety net while connected.
let liveConnected = false;
const liveListeners = new Set<() => void>();
function setLive(on: boolean) {
  if (on === liveConnected) return;
  liveConnected = on;
  liveListeners.forEach((l) => l());
}
export const isLive = () => liveConnected;
export const useIsLive = () => useSyncExternalStore((cb) => (liveListeners.add(cb), () => liveListeners.delete(cb)), isLive);
const SAFETY_NET = 15000;

/** Subscribe to the server's change events for the lifetime of the app. */
export function useLiveUpdates(): void {
  const qc = useQueryClient();
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource("/api/events");
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false); // EventSource retries by itself; polling covers the gap
    es.onmessage = (m) => {
      const e = JSON.parse(m.data) as { hello?: boolean; tree?: boolean; experiments?: string[] };
      setLive(true);
      if (e.hello) return;
      if (e.tree) qc.invalidateQueries({ queryKey: ["experiments"] });
      for (const id of e.experiments ?? []) {
        qc.invalidateQueries({ queryKey: ["dag", id] });
        qc.invalidateQueries({ queryKey: ["plan", id] });
      }
      if (e.experiments?.length) for (const k of ["diff", "lineage", "artifact", "execution", "storage"]) qc.invalidateQueries({ queryKey: [k] });
    };
    return () => {
      es.close();
      setLive(false);
    };
  }, [qc]);
}

export const isBusy = (d?: Dag) => !!d && (d.experiment.status === "running" || d.nodes.some((n) => ACTIVE.has(n.status)));

export const useInfo = () => useQuery({ queryKey: ["info"], queryFn: api.info, staleTime: Infinity });

export const useExperiments = () =>
  useQuery({
    queryKey: ["experiments"],
    queryFn: api.experiments,
    refetchInterval: (q) => (liveConnected ? SAFETY_NET : q.state.data?.some((e) => e.status === "running") ? 1000 : 4000),
  });

export const useDag = (id?: string) =>
  useQuery({
    queryKey: ["dag", id],
    queryFn: () => api.dag(id!),
    enabled: !!id,
    refetchInterval: (q) => (liveConnected ? SAFETY_NET : isBusy(q.state.data) ? 1000 : 4000),
  });

export const useLineage = (hash?: string) =>
  useQuery({ queryKey: ["lineage", hash], queryFn: () => api.lineage(hash!), enabled: !!hash, refetchInterval: 5000 });

export const useArtifact = (hash?: string) =>
  useQuery({ queryKey: ["artifact", hash], queryFn: () => api.artifact(hash!), enabled: !!hash, refetchInterval: 10000 });

export const useExecution = (id?: string | null) =>
  useQuery({ queryKey: ["execution", id], queryFn: () => api.execution(id!), enabled: !!id, staleTime: 30000 });

export const useDiff = (a?: string, b?: string) =>
  useQuery({ queryKey: ["diff", a, b], queryFn: () => api.diff(a!, b!), enabled: !!a && !!b, refetchInterval: 4000 });

export const useSource = (hash?: string | null) =>
  useQuery({ queryKey: ["source", hash], queryFn: () => api.source(hash!), enabled: !!hash, staleTime: 0 });

/** Dry-run plan of a draft. Kept on screen while a re-plan after an edit is in flight. */
export const usePlan = (id?: string) =>
  useQuery({
    queryKey: ["plan", id],
    queryFn: () => api.plan(id!),
    enabled: !!id,
    placeholderData: keepPreviousData,
    refetchInterval: 8000, // picks up code edits on disk (the server reloads changed modules)
    retry: (n, e) => !(e instanceof ApiError && e.status === 409) && n < 2,
  });

/** Storage inventory. Listing a large bucket is slow, so it never polls: mutations invalidate
 *  ["storage"] and the page has a refresh link (the server also caches it for 30 s). */
export const useStorage = (enabled = true) =>
  useQuery({ queryKey: ["storage"], queryFn: () => api.storage(), enabled, staleTime: 60000 });

/** A pipeline's UI view (stage assignments and styles). */
export const useView = (pipeline?: string) =>
  useQuery({ queryKey: ["view", pipeline], queryFn: () => api.view(pipeline!), enabled: !!pipeline, staleTime: 30000 });

export const useShape = (pipeline?: string) =>
  useQuery({
    queryKey: ["shape", pipeline],
    queryFn: () => api.shape(pipeline!),
    enabled: !!pipeline,
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  });
