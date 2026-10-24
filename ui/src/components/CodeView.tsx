// Code mode: the pipeline function's source beside the flow it describes, re-parsed as you type.
// The shape comes from parsing the text (never running it); for one experiment, the plan or
// the run's statuses colour it in.
import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, type Dag, type Diagnostic, type PlanNode, type Shape, type ShapeNode } from "../api";
import { PLAN } from "../copy";
import { useShape, useSource } from "../queries";
import { setUnsaved } from "../unsaved";
import type { Annotation } from "./AceEditor";
import { Graph, type GraphNode, mergeEdges } from "./Graph";
import { type HoverCard, nodeTypes } from "./nodes";

const AceEditor = lazy(() => import("./AceEditor"));

/** Gutter markers for the diagnostics that belong to the edited text. */
export function toAnnotations(diags: Diagnostic[]): Annotation[] {
  return diags
    .filter((d) => d.line != null)
    .map((d) => ({
      row: d.line! - 1,
      column: d.col ?? 0,
      text: `${d.message} [${d.code}]`,
      type: d.severity === "error" ? "error" : "warning",
    }));
}

/** Tone per node id: the plan's outcome for a draft, otherwise the last run's status. */
export function tonesFrom(dag?: Dag): Record<string, string> {
  const out: Record<string, string> = {};
  for (const n of dag?.nodes ?? []) {
    if (n.kind !== "node") continue;
    const p = (n as PlanNode).plan;
    out[n.id] = p ? PLAN[p].tone : n.status;
  }
  return out;
}

export function shapeNodes(shape: Shape, tones: Record<string, string>): GraphNode[] {
  return shape.nodes.map((n: ShapeNode) => {
    if (n.kind === "input") {
      return {
        id: n.id,
        type: "inputNode",
        width: 180,
        height: 48,
        minimapColor: "var(--input)",
        data: {
          name: n.name,
          sub: "pipeline input",
          hover: {
            title: `Input: ${n.name}`,
            body: "An argument of the pipeline function: a file or folder the experiment starts from.",
            facts: [],
          },
        },
      };
    }
    if (n.kind === "dynamic") {
      return {
        id: n.id,
        type: "pipelineNode",
        width: 250,
        height: 68,
        minimapColor: "var(--queued)",
        data: {
          name: n.name,
          status: "dynamic",
          variant: "dynamic",
          line2: "Can't see inside",
          line3: `line ${n.line}`,
          hover: {
            title: "Not a step",
            body: "This call isn't a dendrite step, so parsing can't tell what it does with the data. The plan, which runs the code, can.",
            facts: [["call", n.source ?? n.name]],
          } as HoverCard,
        },
      };
    }
    const cond = n.conditional ?? [];
    const tone = tones[n.id] ?? "queued";
    const facts: [string, string][] = [["line", String(n.line)]];
    if (n.outputs?.length) facts.push(["produces", n.outputs.join(", ")]);
    if (n.params?.length) facts.push(["parameters", n.params.join(", ")]);
    if (n.alternatives) facts.push(["called", `in ${n.alternatives} branches`]);
    return {
      id: n.id,
      type: "pipelineNode",
      width: 250,
      height: 68,
      minimapColor: `var(--${tone})`,
      data: {
        name: `${n.name}${n.repeated ? " ⟳" : ""}`,
        status: tone,
        variant: cond.length ? "upstream" : "",
        line2: cond.length
          ? `if ${cond.join(" and ")}`
          : n.repeated
            ? "in a loop"
            : n.submit
              ? "submitted (runs in parallel)"
              : "always runs",
        line3: n.outputs?.length ? `→ ${n.outputs.join(", ")}` : "",
        hover: {
          title: n.name,
          body: cond.length
            ? "Runs only when this condition holds. Parsing can't know which way it goes; the plan for an experiment can."
            : n.repeated
              ? "Called inside a loop, so it may run several times — each call needs its own name."
              : "Called on every run of this pipeline.",
          facts,
          hint: "Click to jump to this line",
        } as HoverCard,
      },
    };
  });
}

function DiagnosticList({
  diags,
  stepDiags,
  firstLine,
  onPick,
}: {
  diags: Diagnostic[];
  stepDiags: Diagnostic[];
  firstLine: number;
  onPick: (row: number) => void;
}) {
  if (!diags.length && !stepDiags.length)
    return <div className="diag-empty">No problems found in this pipeline or its steps.</div>;
  return (
    <ul className="diags" aria-label="Problems">
      {diags.map((d, i) => (
        <li key={`p${i}`} className={d.severity}>
          <a onClick={() => d.line != null && onPick(d.line - 1)}>
            <b>{d.severity === "error" ? "Error" : "Warning"}</b> line {(d.line ?? 1) + firstLine - 1}: {d.message}{" "}
            <span className="muted">[{d.code}]</span>
          </a>
        </li>
      ))}
      {stepDiags.map((d, i) => (
        <li key={`s${i}`} className={d.severity}>
          <b>{d.severity === "error" ? "Error" : "Warning"}</b> in step {d.step}: {d.message}{" "}
          <span className="muted mono">
            {(d.file ?? "").split("/").pop()}:{d.file_line} [{d.code}]
          </span>
        </li>
      ))}
    </ul>
  );
}

type Prompt = null | "confirm-save" | "conflict";

export function CodeView({ pipeline, dag, canEdit }: { pipeline: string; dag?: Dag; canEdit: boolean }) {
  const qc = useQueryClient();
  const shapeQ = useShape(pipeline);
  const { data: snap, refetch: refetchSource } = useSource(shapeQ.data?.source_hash);
  const [draft, setDraft] = useState<string | null>(null);
  const [live, setLive] = useState<Shape | null>(null);
  const [goTo, setGoTo] = useState<{ row: number; seq: number }>();
  const [prompt, setPrompt] = useState<Prompt>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const seq = useRef(0);

  const editable = canEdit && !!snap?.editable;
  const dirty = draft !== null && !!snap && draft !== snap.source;

  // Re-parse edited text after a pause; the last response wins.
  useEffect(() => {
    if (draft === null) return void setLive(null);
    const mine = ++seq.current;
    const t = window.setTimeout(() => {
      api
        .shapeOf(pipeline, draft)
        .then((s) => mine === seq.current && setLive(s))
        .catch(() => {});
    }, 400);
    return () => window.clearTimeout(t);
  }, [draft, pipeline]);

  useEffect(() => {
    setUnsaved(dirty ? `${pipeline}'s code` : null);
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      setUnsaved(null);
    };
  }, [dirty, pipeline]);

  const shape = live ?? shapeQ.data;
  const tones = useMemo(() => tonesFrom(dag), [dag]);
  const nodes = useMemo(() => (shape ? shapeNodes(shape, tones) : []), [shape, tones]);
  const edges = useMemo(() => mergeEdges(shape?.edges ?? []), [shape]);
  const annotations = useMemo(() => toAnnotations(shape?.diagnostics ?? []), [shape]);
  const lineOf = useMemo(() => new Map((shape?.nodes ?? []).map((n) => [n.id, n.line])), [shape]);

  async function save() {
    if (!snap?.file_sha || draft === null || !shapeQ.data?.source_hash) return;
    setSaving(true);
    setFailure(null);
    try {
      await api.saveSource(shapeQ.data.source_hash, draft, snap.file_sha);
      setPrompt(null);
      setDraft(null);
      setLive(null);
      // The server reloads changed modules before the next plan, so drafts re-plan with this code.
      await Promise.all(["shape", "source", "plan", "dag"].map((k) => qc.invalidateQueries({ queryKey: [k] })));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setPrompt("conflict");
      else setFailure(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  if (shapeQ.error) {
    const notLoaded = shapeQ.error instanceof ApiError && shapeQ.error.code === "pipeline_not_imported";
    return (
      <div className="empty">
        {notLoaded ? (
          <>
            The UI server hasn't loaded <b>{pipeline}</b>'s code. Restart it with{" "}
            <code>dendrite ui --import &lt;module&gt;</code> to see and edit it.
          </>
        ) : (
          String(shapeQ.error)
        )}
      </div>
    );
  }
  if (!shape || !snap) return <div className="empty">Loading the pipeline's code…</div>;
  const where = `${shape.display ?? shape.file ?? "unknown file"}:${shape.first_line}`;
  const note = !canEdit
    ? "Read-only: editing is off (start `dendrite ui` on localhost or with --allow-edit)."
    : snap.status !== "same"
      ? "Read-only: the file on disk no longer matches the loaded code. Restart the server to pick it up."
      : dirty
        ? "Unsaved · the graph shows your edits, parsed but not run"
        : "Edit the pipeline; the graph follows as you type";
  return (
    <div className="code-view">
      <div className="code-pane">
        <div className="code-head">
          <span className="mono" title={shape.file ?? undefined}>
            {where}
          </span>
          <span className="spacer" />
          {editable && dirty && <button onClick={() => (setDraft(null), setLive(null))}>Revert</button>}
          {editable && (
            <button className="primary" disabled={!dirty} onClick={() => setPrompt("confirm-save")}>
              Save…
            </button>
          )}
        </div>
        <div className={`code-note ${dirty ? "dirty" : ""}`}>{note}</div>
        {prompt === "confirm-save" && (
          <div className="confirm code-confirm">
            <span>
              Write this to <b>{shape.display ?? shape.file}</b>? Drafts re-plan with the new code.
            </span>
            <button onClick={() => setPrompt(null)}>Cancel</button>
            <button className="primary" disabled={saving} onClick={save}>
              {saving ? "Saving…" : "Save to file"}
            </button>
          </div>
        )}
        {prompt === "conflict" && (
          <div className="confirm code-confirm">
            <span className="err">The file changed on disk since it was loaded, so nothing was saved.</span>
            <button onClick={() => (setPrompt(null), setDraft(null), setLive(null), refetchSource(), shapeQ.refetch())}>
              Reload (lose my edits)
            </button>
          </div>
        )}
        {failure && <div className="code-note err">Could not save: {failure}</div>}
        <div className="code-editor">
          <Suspense fallback={<div className="empty">Loading editor…</div>}>
            <AceEditor
              value={draft ?? snap.source}
              readOnly={!editable}
              firstLine={shape.first_line}
              onChange={(v) => setDraft(v === snap.source ? null : v)}
              annotations={annotations}
              goTo={goTo}
              focus={false}
            />
          </Suspense>
        </div>
        <DiagnosticList
          diags={shape.diagnostics}
          stepDiags={shape.step_diagnostics ?? []}
          firstLine={shape.first_line}
          onPick={(row) => setGoTo({ row, seq: Date.now() })}
        />
      </div>
      <div className="shape-pane">
        <div className="shape-note">
          Flow parsed from the source{shape.dynamic ? " · grey boxes are calls parsing can't see into" : ""}.{" "}
          {dag && `Colours: this experiment's ${dag.experiment.status === "draft" ? "plan" : "last run"}.`}
        </div>
        <div className="shape-graph">
          {nodes.length ? (
            <Graph
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onSelect={(id) => {
                const line = lineOf.get(id);
                if (line) setGoTo({ row: line - 1, seq: Date.now() });
              }}
              legend={null}
            />
          ) : (
            <div className="empty">
              {shape.diagnostics.some((d) => d.code === "syntax")
                ? "Fix the syntax error to see the flow."
                : "No steps called yet."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
