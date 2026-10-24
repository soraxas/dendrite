import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, type Shape, type SourceSnapshot } from "../api";
import { exp, plan, step } from "../test/fixtures";
import { renderWithQuery } from "../test/render";
import { confirmLeave, setUnsaved } from "../unsaved";
import { CodeView, shapeNodes, toAnnotations, tonesFrom } from "./CodeView";

// The editor is lazy-loaded Ace; a textarea stands in for it here.
vi.mock("./AceEditor", () => ({
  default: ({ value, onChange, readOnly }: { value: string; onChange: (v: string) => void; readOnly: boolean }) => (
    <textarea aria-label="Source code" value={value} readOnly={readOnly} onChange={(e) => onChange(e.target.value)} />
  ),
}));
// Layout needs a real browser; these tests are about the code pane.
vi.mock("./Graph", () => ({
  Graph: ({ nodes }: { nodes: { id: string }[] }) => <div data-testid="graph">{nodes.map((n) => n.id).join(",")}</div>,
  mergeEdges: (e: unknown[]) => e,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setUnsaved(null);
});

const SOURCE = '@pipeline(name="p")\ndef p(video):\n    return extract(video)\n';
const shape = (over: Partial<Shape> = {}): Shape => ({
  name: "p",
  nodes: [
    { id: "input:video", kind: "input", name: "video", line: 2 },
    {
      id: "extract",
      kind: "node",
      name: "extract",
      line: 3,
      conditional: [],
      repeated: false,
      outputs: ["frames"],
      params: ["fps"],
    },
  ],
  edges: [{ from: "input:video", to: "extract", label: "video", input: "video" }],
  diagnostics: [],
  step_diagnostics: [],
  dynamic: false,
  file: "/repo/pipes.py",
  display: "pipes.py",
  first_line: 10,
  source_hash: "h1",
  ...over,
});
const snap = (over: Partial<SourceSnapshot> = {}): SourceSnapshot => ({
  hash: "h1",
  file: "/repo/pipes.py",
  display: "pipes.py",
  line: 10,
  module: "pipes",
  qualname: "p",
  source: SOURCE,
  status: "same",
  file_sha: "sha",
  editable: true,
  ...over,
});

describe("helpers", () => {
  it("turns diagnostics into 0-based gutter rows", () => {
    const a = toAnnotations([
      {
        code: "unpack-mismatch",
        severity: "error",
        message: "colmap produces 2 outputs",
        line: 3,
        col: 4,
        step: "colmap",
      },
    ]);
    expect(a).toEqual([{ row: 2, column: 4, text: "colmap produces 2 outputs [unpack-mismatch]", type: "error" }]);
  });

  it("colours shape nodes by the plan for drafts and by run status otherwise", () => {
    const draft = plan({ nodes: [step({ id: "extract", name: "extract", plan: "reuse" })] });
    expect(tonesFrom(draft)).toEqual({ extract: "cached" });
    const ran = {
      experiment: exp({ status: "completed" }),
      edges: [],
      nodes: [{ id: "extract", kind: "node" as const, name: "extract", status: "computed" as const }],
    };
    expect(tonesFrom(ran)).toEqual({ extract: "computed" });
  });

  it("marks conditional, repeated and dynamic nodes", () => {
    const s = shape({
      nodes: [
        {
          id: "refine",
          kind: "node",
          name: "refine",
          line: 5,
          conditional: ["fine", "big"],
          repeated: false,
          outputs: ["model"],
        },
        { id: "x0", kind: "node", name: "x0", line: 6, conditional: [], repeated: true, outputs: ["frames"] },
        { id: "dyn:7:4", kind: "dynamic", name: "json.dumps(model)", line: 7, source: "json.dumps(model)" },
      ],
    });
    const [refine, loop, dyn] = shapeNodes(s, {}).map((n) => n.data as Record<string, string>);
    expect([refine.variant, refine.line2, refine.status]).toEqual(["upstream", "if fine and big", "queued"]);
    expect([loop.name, loop.line2]).toEqual(["x0 ⟳", "in a loop"]);
    expect([dyn.status, dyn.line2]).toEqual(["dynamic", "Can't see inside"]);
  });
});

describe("CodeView", () => {
  const setup = (snapOver: Partial<SourceSnapshot> = {}, canEdit = true) => {
    vi.spyOn(api, "shape").mockResolvedValue(shape());
    vi.spyOn(api, "source").mockResolvedValue(snap(snapOver));
    return renderWithQuery(<CodeView pipeline="p" canEdit={canEdit} />);
  };

  it("re-parses edits without saving and lists their problems at file lines", async () => {
    const shapeOf = vi.spyOn(api, "shapeOf").mockResolvedValue(
      shape({
        diagnostics: [
          {
            code: "unknown-arg",
            severity: "error",
            message: "extract has no parameter 'fsp'",
            line: 3,
            col: 4,
            step: "extract",
          },
        ],
      }),
    );
    const save = vi.spyOn(api, "saveSource");
    setup();
    const editor = await screen.findByLabelText("Source code");
    fireEvent.change(editor, { target: { value: SOURCE.replace("video)", "video, fsp=1)") } });
    await waitFor(() => expect(shapeOf).toHaveBeenCalled(), { timeout: 2000 });
    expect(await screen.findByText(/line 12: extract has no parameter 'fsp'/)).toBeTruthy(); // 3 + first line 10 - 1
    expect(screen.getByText(/Unsaved/)).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
  });

  it("asks before leaving with unsaved edits", async () => {
    vi.spyOn(api, "shapeOf").mockResolvedValue(shape());
    setup();
    fireEvent.change(await screen.findByLabelText("Source code"), { target: { value: `${SOURCE}# note\n` } });
    const ask = vi.spyOn(window, "confirm").mockReturnValue(false);
    expect(confirmLeave()).toBe(false);
    expect(ask.mock.calls[0][0]).toMatch(/unsaved changes to p's code/);
  });

  it("saves after confirmation through the guarded source write", async () => {
    vi.spyOn(api, "shapeOf").mockResolvedValue(shape());
    const save = vi.spyOn(api, "saveSource").mockResolvedValue({ file_sha: "sha2" });
    setup();
    const edited = `${SOURCE}# note\n`;
    fireEvent.change(await screen.findByLabelText("Source code"), { target: { value: edited } });
    fireEvent.click(screen.getByText("Save…"));
    expect(save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Save to file"));
    await waitFor(() => expect(save).toHaveBeenCalledWith("h1", edited, "sha"));
  });

  it("refuses to overwrite a file that changed meanwhile", async () => {
    vi.spyOn(api, "shapeOf").mockResolvedValue(shape());
    vi.spyOn(api, "saveSource").mockRejectedValue(new ApiError(409, "file changed"));
    setup();
    fireEvent.change(await screen.findByLabelText("Source code"), { target: { value: `${SOURCE}#\n` } });
    fireEvent.click(screen.getByText("Save…"));
    fireEvent.click(screen.getByText("Save to file"));
    expect(await screen.findByText(/changed on disk since it was loaded, so nothing was saved/)).toBeTruthy();
  });

  it("is read-only when the file no longer matches or editing is off", async () => {
    setup({ status: "changed", editable: false });
    expect(((await screen.findByLabelText("Source code")) as HTMLTextAreaElement).readOnly).toBe(true);
    expect(screen.getByText(/no longer matches the loaded code/)).toBeTruthy();
    expect(screen.queryByText("Save…")).toBeNull();
    cleanup();
    setup({}, false);
    expect(await screen.findByText(/editing is off/)).toBeTruthy();
  });

  it("explains a pipeline the server hasn't loaded", async () => {
    vi.spyOn(api, "shape").mockRejectedValue(new ApiError(404, "not loaded", "pipeline_not_imported"));
    renderWithQuery(<CodeView pipeline="p" canEdit />);
    expect(await screen.findByText(/hasn't loaded/)).toBeTruthy();
  });
});
