// Custom React Flow nodes. Pipeline nodes encode run status; lineage nodes encode data vs computation.
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { CSSProperties } from "react";

type Base = { dir?: "RIGHT" | "DOWN"; selected?: boolean };

function Handles({ dir }: { dir?: "RIGHT" | "DOWN" }) {
  const down = dir === "DOWN";
  return (
    <>
      <Handle type="target" position={down ? Position.Top : Position.Left} isConnectable={false} />
      <Handle type="source" position={down ? Position.Bottom : Position.Right} isConnectable={false} />
    </>
  );
}

function cls(d: Base, extra = "") {
  return `gnode ${extra} ${d.selected ? "sel" : ""}`;
}

const tone = (name: string): CSSProperties => ({ background: `var(--${name}-bg)`, borderColor: `var(--${name})` });

export type PipelineData = Base & { name: string; status: string; line2: string; line3: string };
export function PipelineNode({ data }: NodeProps & { data: PipelineData }) {
  const animated = data.status === "running" || data.status === "waiting";
  return (
    <div className={cls(data, `${data.status} ${animated ? "animated" : ""}`)} style={tone(data.status)} title={`${data.name} · ${data.status}`}>
      <Handles dir={data.dir} />
      <div className="accent" style={{ background: `var(--${data.status})` }} />
      <div className="t1">{data.name}</div>
      <div className="t2">{data.line2}</div>
      <div className="t3">{data.line3}</div>
    </div>
  );
}

export type InputData = Base & { name: string; hash: string };
export function InputNode({ data }: NodeProps & { data: InputData }) {
  return (
    <div className={cls(data, "pill")} style={tone("input")} title={`input ${data.name}`}>
      <Handles dir={data.dir} />
      <div className="t1">{data.name}</div>
      <div className="t3">input · {data.hash}</div>
    </div>
  );
}

export type ArtifactData = Base & { label: string; sub: string; focus: boolean };
export function ArtifactNode({ data }: NodeProps & { data: ArtifactData }) {
  return (
    <div className={cls(data, "pill")} style={data.focus ? { background: "var(--accent-bg)", borderColor: "var(--accent)", borderWidth: 2.5 } : tone("data")} title={data.label}>
      <Handles dir={data.dir} />
      <div className="t1">{data.label}</div>
      <div className="t3">{data.sub}</div>
    </div>
  );
}

export type ExecutionData = Base & { node: string; sub: string };
export function ExecutionNode({ data }: NodeProps & { data: ExecutionData }) {
  return (
    <div className={cls(data)} style={tone("exec")} title={`${data.node} execution`}>
      <Handles dir={data.dir} />
      <div className="accent" style={{ background: "var(--exec)" }} />
      <div className="t1">{data.node}</div>
      <div className="t3">{data.sub}</div>
    </div>
  );
}

export const nodeTypes = {
  pipelineNode: PipelineNode,
  inputNode: InputNode,
  artifactNode: ArtifactNode,
  executionNode: ExecutionNode,
};
