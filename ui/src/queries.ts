import { useQuery } from "@tanstack/react-query";
import { ACTIVE, api, type Dag } from "./api";

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
