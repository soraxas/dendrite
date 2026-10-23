// Custom React Flow nodes. Pipeline nodes encode run status; lineage nodes encode data vs computation.
// Every node can carry a hover card that says, in a sentence, what happened.
import { Handle, NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import { useState, type CSSProperties, type ReactNode } from "react";

export interface HoverCard {
  title: string;
  body: string;
  facts?: [string, string][];
  hint?: string;
}

export interface Port {
  name: string;
  hash: string;
  sub: string;
}

type Base = {
  dir?: "RIGHT" | "DOWN";
  selected?: boolean;
  hover?: HoverCard;
  /** Shown around the node while it is selected, so its data is one click away. */
  ports?: { inputs: Port[]; outputs: Port[]; active?: string; onPick: (hash: string) => void };
};

function PortChips({ ports, active, onPick, side, isVisible, position }: {
  ports: Port[];
  active?: string;
  onPick: (hash: string) => void;
  side: "in" | "out";
  isVisible: boolean;
  position: Position;
}) {
  if (!ports.length) return null;
  return (
    <NodeToolbar isVisible={isVisible} position={position} offset={10}>
      <div className={`ports ${side}`} role="group" aria-label={side === "in" ? "Input data" : "Output data"}>
        <span className="ports-title">{side === "in" ? "in" : "out"}</span>
        {ports.map((p) => (
          <button
            key={`${p.name}:${p.hash}`}
            className={`port ${p.hash === active ? "on" : ""}`}
            title={`${p.name} · ${p.sub} — click to preview or trace it`}
            onClick={(e) => {
              e.stopPropagation();
              onPick(p.hash);
            }}
          >
            <b>{p.name}</b> <span className="muted">{p.sub}</span>
          </button>
        ))}
      </div>
    </NodeToolbar>
  );
}

function Handles({ dir }: { dir?: "RIGHT" | "DOWN" }) {
  const down = dir === "DOWN";
  return (
    <>
      <Handle type="target" position={down ? Position.Top : Position.Left} isConnectable={false} />
      <Handle type="source" position={down ? Position.Bottom : Position.Right} isConnectable={false} />
    </>
  );
}

function Shell({ data, className, style, children, label }: { data: Base; className: string; style: CSSProperties; children: ReactNode; label: string }) {
  const [hovered, setHovered] = useState(false);
  const h = data.hover;
  return (
    <div
      className={`gnode ${className} ${data.selected ? "sel" : ""}`}
      style={style}
      aria-label={label}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <Handles dir={data.dir} />
      {children}
      {data.ports && (
        <>
          <PortChips ports={data.ports.inputs} active={data.ports.active} onPick={data.ports.onPick} side="in" isVisible={!!data.selected} position={data.dir === "DOWN" ? Position.Top : Position.Left} />
          <PortChips ports={data.ports.outputs} active={data.ports.active} onPick={data.ports.onPick} side="out" isVisible={!!data.selected} position={data.dir === "DOWN" ? Position.Bottom : Position.Right} />
        </>
      )}
      {h && (
        <NodeToolbar isVisible={hovered && !data.selected} position={data.dir === "DOWN" ? Position.Right : Position.Bottom} offset={12}>
          <div className="hovercard" role="tooltip">
            <div className="hc-title">{h.title}</div>
            <div className="hc-body">{h.body}</div>
            {h.facts && h.facts.length > 0 && (
              <dl>
                {h.facts.map(([k, v]) => (
                  <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
                ))}
              </dl>
            )}
            <div className="hc-hint">{h.hint ?? "Click for details"}</div>
          </div>
        </NodeToolbar>
      )}
    </div>
  );
}

const tone = (name: string): CSSProperties => ({ background: `var(--${name}-bg)`, borderColor: `var(--${name})` });

export type PipelineData = Base & {
  name: string;
  status: string;
  line2: string;
  line3: string;
  /** For reused/frozen steps: the experiment whose result was taken, as a link. */
  source?: { verb: string; label: string; rest: string; open: () => void };
  /** Extra classes, e.g. "upstream" for a planned step that only runs because an earlier one does. */
  variant?: string;
};
export function PipelineNode({ data }: NodeProps & { data: PipelineData }) {
  const animated = data.status === "running" || data.status === "waiting";
  const src = data.source;
  return (
    <Shell data={data} className={`${data.status} ${animated ? "animated" : ""} ${data.variant ?? ""}`} style={tone(data.status)} label={`${data.name}: ${data.line2}`}>
      <div className="accent" style={{ background: `var(--${data.status})` }} />
      <div className="t1">{data.name}</div>
      {src ? (
        <div className="t2">
          {src.verb}{" "}
          <a
            className="src-link nodrag"
            title={`Open ${src.label} with this step selected`}
            onClick={(e) => {
              e.stopPropagation();
              src.open();
            }}
          >
            {src.label}
          </a>
          {src.rest}
        </div>
      ) : (
        <div className="t2">{data.line2}</div>
      )}
      <div className="t3">{data.line3}</div>
    </Shell>
  );
}

export type InputData = Base & { name: string; sub: string };
export function InputNode({ data }: NodeProps & { data: InputData }) {
  return (
    <Shell data={data} className="pill" style={tone("input")} label={`input ${data.name}`}>
      <div className="t1">{data.name}</div>
      <div className="t3">{data.sub}</div>
    </Shell>
  );
}

export type ArtifactData = Base & { label: string; sub: string; focus: boolean };
export function ArtifactNode({ data }: NodeProps & { data: ArtifactData }) {
  const style = data.focus ? { background: "var(--accent-bg)", borderColor: "var(--accent)", borderWidth: 2.5 } : tone("data");
  return (
    <Shell data={data} className="pill" style={style} label={`data ${data.label}`}>
      <div className="t1">{data.label}</div>
      <div className="t3">{data.sub}</div>
    </Shell>
  );
}

export type ExecutionData = Base & { node: string; sub: string };
export function ExecutionNode({ data }: NodeProps & { data: ExecutionData }) {
  return (
    <Shell data={data} className="" style={tone("exec")} label={`computation ${data.node}`}>
      <div className="accent" style={{ background: "var(--exec)" }} />
      <div className="t1">{data.node}</div>
      <div className="t3">{data.sub}</div>
    </Shell>
  );
}

export const nodeTypes = {
  pipelineNode: PipelineNode,
  inputNode: InputNode,
  artifactNode: ArtifactNode,
  executionNode: ExecutionNode,
};
