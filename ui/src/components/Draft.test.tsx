import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import { exp, field, plan, step } from "../test/fixtures";
import { renderWithQuery } from "../test/render";
import { DraftBar, DraftStepEditor, ParamRow, parseParam, planSummary, runCommand } from "./Draft";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("parseParam", () => {
  it("keeps integers integral and rejects anything else", () => {
    expect(parseParam(field(), " 60000 ")).toEqual({ value: 60000 });
    expect(parseParam(field(), "6e4").error).toBe("a whole number");
    expect(parseParam(field(), "1.5").error).toBe("a whole number");
  });
  it("accepts any finite number for float params", () => {
    const f = field({ json_type: "number" });
    expect(parseParam(f, "2e-3")).toEqual({ value: 0.002 });
    expect(parseParam(f, "").error).toBe("a number");
    expect(parseParam(f, "nan").error).toBe("a number");
  });
  it("passes strings through untouched, including surrounding spaces", () => {
    expect(parseParam(field({ json_type: "string" }), " exhaustive")).toEqual({ value: " exhaustive" });
  });
  it("parses JSON for lists, objects and untyped params", () => {
    expect(parseParam(field({ json_type: "array" }), "[1, 2]")).toEqual({ value: [1, 2] });
    expect(parseParam(field({ json_type: "any" }), "null")).toEqual({ value: null });
    expect(parseParam(field({ json_type: "object" }), "{oops").error).toBe("valid JSON");
  });
});

describe("planSummary", () => {
  const t = (totals: Partial<ReturnType<typeof plan>["totals"]>) => plan({ totals: { ...plan().totals, ...totals } });
  it("totals what will run, with an estimate", () => {
    expect(planSummary(plan())).toBe("Will run 2 of 4 steps · about 14.0m");
  });
  it("flags estimates that leave steps out", () => {
    expect(planSummary(t({ unestimated: 1 }))).toBe("Will run 2 of 4 steps · about 14.0m+");
  });
  it("says why nothing runs", () => {
    expect(planSummary(t({ run: 0, reuse: 4, frozen: 0 }))).toBe("Nothing to run — every step is reused");
    expect(planSummary(t({ run: 0, reuse: 0, frozen: 4 }))).toBe("Nothing to run — every step is frozen");
    expect(planSummary(t({ run: 0, reuse: 3, frozen: 1 }))).toBe("Nothing to run — 3 reused, 1 frozen");
  });
});

it("runCommand includes --import only when the module is importable", () => {
  expect(runCommand(exp(), "my.pipelines")).toBe("dendrite run e-draft --import my.pipelines");
  expect(runCommand(exp(), null)).toBe("dendrite run e-draft");
});

describe("ParamRow", () => {
  const setup = (p = field(), disabled = false) => {
    const onSave = vi.fn();
    const onReset = vi.fn();
    const r = render(<ParamRow node="train" p={p} onSave={onSave} onReset={onReset} disabled={disabled} />);
    return { ...r, onSave, onReset, input: screen.getByRole("textbox") as HTMLInputElement };
  };
  beforeEach(() => vi.useFakeTimers());

  it("saves a typed value after a pause, as the parsed type", () => {
    const { input, onSave } = setup();
    fireEvent.change(input, { target: { value: "90000" } });
    expect(onSave).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(600));
    expect(onSave).toHaveBeenCalledWith("train.iterations", 90000, false);
  });

  it("flags float params so the server keeps 1 as 1.0", () => {
    const { input, onSave } = setup(field({ name: "lr", value: 0.001, json_type: "number", annotation: "float" }));
    fireEvent.change(input, { target: { value: "1" } });
    fireEvent.blur(input);
    expect(onSave).toHaveBeenCalledWith("train.lr", 1, true);
  });

  it("shows invalid input instead of saving it", () => {
    const { input, onSave } = setup();
    fireEvent.change(input, { target: { value: "lots" } });
    act(() => vi.advanceTimersByTime(600));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText("Enter a whole number")).toBeTruthy();
  });

  it("does not save when the value is unchanged", () => {
    const { input, onSave } = setup();
    fireEvent.change(input, { target: { value: "30000" } });
    fireEvent.blur(input);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("keeps what the user is typing when a re-plan delivers new props", () => {
    const { input, rerender, onSave, onReset } = setup();
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "4" } });
    rerender(<ParamRow node="train" p={field({ value: 31000 })} onSave={onSave} onReset={onReset} disabled={false} />);
    expect(input.value).toBe("4");
    fireEvent.blur(input);
    rerender(<ParamRow node="train" p={field({ value: 32000 })} onSave={onSave} onReset={onReset} disabled={false} />);
    expect(input.value).toBe("32000");
  });

  it("offers reset only for changed params, and shows what it was", () => {
    const { onReset } = setup(field({ value: 60000, changed: true, overridden: true }));
    expect(screen.getByText("was 30000 · default 30000")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Reset iterations"));
    expect(onReset).toHaveBeenCalledWith("train.iterations");
    cleanup();
    setup();
    expect(screen.queryByLabelText("Reset iterations")).toBeNull();
  });

  it("is read-only when disabled", () => {
    const { input } = setup(field(), true);
    expect(input.disabled).toBe(true);
  });
});

describe("DraftStepEditor", () => {
  const n = step({ param_schema: [field()] });
  const nameOf = (id: string) => (id === "e-base" ? "baseline" : id);

  it("freezes a step to the parent's result", async () => {
    const edit = vi.spyOn(api, "editDraft").mockResolvedValue(exp());
    renderWithQuery(<DraftStepEditor n={n} plan={plan({ nodes: [n] })} canEdit nameOf={nameOf} openStep={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("Freeze to baseline's result"));
    await waitFor(() => expect(edit).toHaveBeenCalledWith("e-draft", { pins: { train: "parent" } }));
  });

  it("unfreezes a frozen step, and disables its params meanwhile", async () => {
    const edit = vi.spyOn(api, "editDraft").mockResolvedValue(exp());
    const frozen = plan({ experiment: exp({ pins: { train: { model: "ab" } } }), nodes: [n] });
    renderWithQuery(
      <DraftStepEditor
        n={{ ...n, plan: "frozen", reason: null }}
        plan={frozen}
        canEdit
        nameOf={nameOf}
        openStep={vi.fn()}
      />,
    );
    expect((screen.getByLabelText(/iterations/) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Freeze to baseline's result"));
    await waitFor(() => expect(edit).toHaveBeenCalledWith("e-draft", { pins: { train: null } }));
  });

  it("explains why a step will run", () => {
    renderWithQuery(<DraftStepEditor n={n} plan={plan({ nodes: [n] })} canEdit nameOf={nameOf} openStep={vi.fn()} />);
    expect(screen.getByText(/It will run because its parameters changed\./)).toBeTruthy();
  });

  it("shows a server rejection next to the form", async () => {
    vi.spyOn(api, "editDraft").mockRejectedValue(
      new ApiError(409, "experiment e-draft is running; fork it to make changes"),
    );
    renderWithQuery(<DraftStepEditor n={n} plan={plan({ nodes: [n] })} canEdit nameOf={nameOf} openStep={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("Freeze to baseline's result"));
    expect(await screen.findByText(/fork it to make changes/)).toBeTruthy();
  });

  it("is read-only when editing is off", () => {
    renderWithQuery(
      <DraftStepEditor n={n} plan={plan({ nodes: [n] })} canEdit={false} nameOf={nameOf} openStep={vi.fn()} />,
    );
    expect((screen.getByLabelText("Freeze to baseline's result") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText(/iterations/) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/Editing is off/)).toBeTruthy();
  });
});

describe("DraftBar", () => {
  it("shows the run command for the loaded module", () => {
    renderWithQuery(<DraftBar exp={exp()} plan={plan()} canEdit onDiscarded={vi.fn()} />);
    expect(screen.getByText("dendrite run e-draft --import my.pipelines")).toBeTruthy();
  });

  it("explains a missing pipeline module instead of an empty graph", () => {
    const err = new ApiError(409, "not loaded", "pipeline_not_imported");
    renderWithQuery(<DraftBar exp={exp()} planError={err} canEdit onDiscarded={vi.fn()} />);
    expect(screen.getByText(/hasn't loaded the pipeline code/)).toBeTruthy();
  });

  it("says where an incomplete preview stopped", () => {
    renderWithQuery(
      <DraftBar exp={exp()} plan={plan({ complete: false, blocked_by: "featurize" })} canEdit onDiscarded={vi.fn()} />,
    );
    expect(screen.getByText(/The preview stops at featurize/)).toBeTruthy();
  });

  it("renames the draft when the field loses focus", async () => {
    const edit = vi.spyOn(api, "editDraft").mockResolvedValue(exp({ name: "lr-sweep" }));
    renderWithQuery(<DraftBar exp={exp()} plan={plan()} canEdit onDiscarded={vi.fn()} />);
    const input = screen.getByLabelText("Draft name");
    fireEvent.change(input, { target: { value: "lr-sweep" } });
    fireEvent.blur(input);
    await waitFor(() => expect(edit).toHaveBeenCalledWith("e-draft", { name: "lr-sweep" }));
  });

  it("discards only after confirmation", async () => {
    const discard = vi.spyOn(api, "discard").mockResolvedValue({ deleted: "e-draft" });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const onDiscarded = vi.fn();
    renderWithQuery(<DraftBar exp={exp()} plan={plan()} canEdit onDiscarded={onDiscarded} />);
    fireEvent.click(screen.getByText("Discard"));
    expect(discard).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Discard"));
    await waitFor(() => expect(onDiscarded).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(discard).toHaveBeenCalledWith("e-draft");
  });

  it("hides editing controls when editing is off", () => {
    renderWithQuery(<DraftBar exp={exp()} plan={plan()} canEdit={false} onDiscarded={vi.fn()} />);
    expect(screen.queryByLabelText("Draft name")).toBeNull();
    expect(screen.queryByText("Discard")).toBeNull();
  });
});
