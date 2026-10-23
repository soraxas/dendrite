import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ACTIVE, api, ApiError, type Dag } from "./api";

export const isBusy = (d?: Dag) => !!d && (d.experiment.status === "running" || d.nodes.some((n) => ACTIVE.has(n.status)));

export const useInfo = () => useQuery({ queryKey: ["info"], queryFn: api.info, staleTime: Infinity });

export const useExperiments = () =>
  useQuery({
    queryKey: ["experiments"],
    queryFn: api.experiments,
    refetchInterval: (q) => (q.state.data?.some((e) => e.status === "running") ? 1000 : 4000),
  });

export const useDag = (id?: string) =>
  useQuery({
    queryKey: ["dag", id],
    queryFn: () => api.dag(id!),
    enabled: !!id,
    refetchInterval: (q) => (isBusy(q.state.data) ? 1000 : 4000),
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

export const useShape = (pipeline?: string) =>
  useQuery({
    queryKey: ["shape", pipeline],
    queryFn: () => api.shape(pipeline!),
    enabled: !!pipeline,
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  });
