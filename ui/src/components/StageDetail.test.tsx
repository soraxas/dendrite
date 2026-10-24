import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, type DagNode, type PipelineView } from "../api";
import { stageGroups } from "../stages";
import { renderWithQuery } from "../test/render";
import { StageAssign, StageDetail, stageCode } from "./StageDetail";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const n = (id: string, stage: string[] = []): DagNode => ({
  id,
  kind: "node",
  name: id,
  status: "computed",
  step: id,
  key: null,
  stage,
  duration: 2,
});
const view: PipelineView = { assign: { transcribe: "Audio" }, styles: {} };
const nodes = [n("extract", ["Privacy"]), n("blur", ["Privacy"]), n("transcribe")];

describe("StageDetail", () => {
  it("lists its steps, opens one, and saves a new label and colour to the view", async () => {
    const save = vi.spyOn(api, "saveView").mockImplementation(async (_p, v) => v);
    const onOpen = vi.fn();
    const privacy = stageGroups(nodes, view)[0];
    renderWithQuery(
      <StageDetail stage={privacy} view={view} pipeline="site" canEdit collapsed onToggle={vi.fn()} onOpen={onOpen} />,
    );
    fireEvent.click(screen.getByText("blur"));
    expect(onOpen).toHaveBeenCalledWith("blur");
    fireEvent.change(screen.getByLabelText("Stage label"), { target: { value: "Privacy filter" } });
    fireEvent.click(screen.getByRole("radio", { name: "pink" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls[0]).toEqual([
      "site",
      {
        assign: { transcribe: "Audio" },
        styles: { Privacy: { label: "Privacy filter", description: undefined, color: "pink" } },
      },
    ]);
  });

  it("shows UI-only stages as code, and can't save when editing is off", () => {
    const audio = stageGroups(nodes, view).find((s) => s.name === "Audio")!;
    renderWithQuery(
      <StageDetail
        stage={audio}
        view={view}
        pipeline="site"
        canEdit={false}
        collapsed
        onToggle={vi.fn()}
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Stage as code").textContent).toBe(stageCode(view, "Audio"));
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText("remove")).toBeNull();
  });
});

describe("StageAssign", () => {
  it("puts a step in an existing or new stage, and takes it out", async () => {
    const save = vi.spyOn(api, "saveView").mockImplementation(async (_p, v) => v);
    renderWithQuery(<StageAssign step="publish" view={view} pipeline="site" stages={["Privacy", "Audio"]} canEdit />);
    fireEvent.change(screen.getByLabelText("Put in a stage"), { target: { value: "Privacy" } });
    await waitFor(() =>
      expect(save).toHaveBeenLastCalledWith("site", {
        assign: { transcribe: "Audio", publish: "Privacy" },
        styles: {},
      }),
    );
    fireEvent.change(screen.getByLabelText("New stage name"), { target: { value: "Publish" } });
    fireEvent.click(screen.getByText("Add"));
    await waitFor(() =>
      expect(save).toHaveBeenLastCalledWith("site", {
        assign: { transcribe: "Audio", publish: "Publish" },
        styles: {},
      }),
    );
  });
});
