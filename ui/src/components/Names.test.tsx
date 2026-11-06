import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, type ArtifactName, api } from "../api";
import { href, parse } from "../router";
import { renderWithQuery } from "../test/render";
import { NameControl, NamesPage, suggestName } from "./Names";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const H = "a".repeat(64);
const row = (over: Partial<ArtifactName> = {}): ArtifactName => ({
  name: "best",
  hash: H,
  experiment_id: "e-1",
  node: "train",
  output: "model",
  note: "first",
  set_by: "tin",
  set_at: 1_700_000_000,
  kind: "file",
  size: 1234,
  filename: "model.ply",
  ...over,
});

it("suggests node-output names with only allowed characters", () => {
  expect(suggestName("train", "model")).toBe("train-model");
  expect(suggestName("blur[clip 1]", "video")).toBe("blur-clip-1-video");
  expect(suggestName(null, null, "render.png")).toBe("render");
});

it("routes #/names", () => {
  expect(parse("#/names")).toEqual({ view: "names", sel: undefined });
  expect(href({ view: "names" })).toBe("#/names");
});

it("names a step output with its source", async () => {
  const set = vi.spyOn(api, "setName").mockResolvedValue(row());
  renderWithQuery(
    <NameControl hash={H} names={[]} source={{ experiment: "e-1", node: "train", output: "model" }} canEdit />,
  );
  fireEvent.click(screen.getByText("☆ Name…"));
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("train-model");
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "keep" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() =>
    expect(set).toHaveBeenCalledWith("train-model", {
      experiment: "e-1",
      node: "train",
      output: "model",
      note: "keep",
    }),
  );
});

it("offers to move a taken name, with a compare-and-set on its target", async () => {
  const other = "b".repeat(64);
  const set = vi
    .spyOn(api, "setName")
    .mockRejectedValueOnce(new ApiError(409, "taken", "name_taken"))
    .mockResolvedValueOnce(row());
  vi.spyOn(api, "nameHistory").mockResolvedValue({ name: row({ hash: other }), history: [] });
  renderWithQuery(
    <NameControl hash={H} names={[]} source={{ experiment: "e-1", node: "train", output: "model" }} canEdit />,
  );
  fireEvent.click(screen.getByText("☆ Name…"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "best" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(screen.getByText(/already names another artifact/)).toBeTruthy());
  fireEvent.click(screen.getByText("Move it here"));
  await waitFor(() =>
    expect(set).toHaveBeenLastCalledWith("best", {
      experiment: "e-1",
      node: "train",
      output: "model",
      move: true,
      expect: other,
    }),
  );
});

it("rejects invalid names before asking the server", () => {
  const set = vi.spyOn(api, "setName");
  renderWithQuery(<NameControl hash={H} names={[]} canEdit />);
  fireEvent.click(screen.getByText("☆ Name…"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "no spaces" } });
  fireEvent.click(screen.getByText("Save"));
  expect(screen.getByText(/Use letters, digits/)).toBeTruthy();
  expect(set).not.toHaveBeenCalled();
});

it("shows names read-only without editing", () => {
  renderWithQuery(<NameControl hash={H} names={["best"]} canEdit={false} />);
  expect(screen.getByText("★ @best")).toBeTruthy();
  expect(screen.queryByText("☆ Name…")).toBeNull();
  expect(screen.queryByLabelText("Remove @best")).toBeNull();
});

it("lists names and opens one in its experiment", async () => {
  vi.spyOn(api, "names").mockResolvedValue({ names: [row(), row({ name: "raw", node: null, output: "src" })] });
  const open = vi.fn();
  renderWithQuery(<NamesPage canEdit nameOf={(id) => `exp ${id}`} openArtifact={open} openExp={vi.fn()} />);
  await waitFor(() => expect(screen.getByText("@best")).toBeTruthy());
  fireEvent.change(screen.getByLabelText("Find a name"), { target: { value: "raw" } });
  expect(screen.queryByText("@best")).toBeNull();
  fireEvent.click(screen.getByText("@raw"));
  expect(open).toHaveBeenCalledWith(H, "e-1");
});
