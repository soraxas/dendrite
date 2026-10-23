import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadAll, useFolds } from "./folds";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("folds", () => {
  it("are remembered per pipeline across a reload", () => {
    const first = renderHook(() => useFolds("site"));
    act(() => first.result.current.toggleStage("stage:Privacy"));
    act(() => first.result.current.toggleFan("cut_clip"));
    first.unmount();
    const again = renderHook(() => useFolds("site")); // a refresh
    expect(again.result.current.folds).toEqual({ showSteps: false, stages: ["stage:Privacy"], fan: ["cut_clip"] });
    expect(renderHook(() => useFolds("other")).result.current.folds.stages).toEqual([]); // another pipeline is untouched
  });
  it("switching level restarts stages from that level's default", () => {
    const h = renderHook(() => useFolds("site"));
    act(() => h.result.current.toggleStage("stage:Privacy"));
    act(() => h.result.current.setShowSteps(true));
    expect(h.result.current.folds).toMatchObject({ showSteps: true, stages: [] });
  });
  it("still works when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(loadAll()).toEqual({});
    const h = renderHook(() => useFolds("site"));
    act(() => h.result.current.toggleFan("cut_clip"));
    expect(h.result.current.folds.fan).toEqual(["cut_clip"]);
  });
});
