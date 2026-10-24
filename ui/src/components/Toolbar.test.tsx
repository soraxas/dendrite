import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import { exp } from "../test/fixtures";
import { renderWithQuery } from "../test/render";
import { ForkButton } from "./Toolbar";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const dag = {
  experiment: exp({ id: "e-base", name: "baseline", status: "completed", parent_id: null }),
  nodes: [],
  edges: [],
};

it("forks into a named draft and opens it", async () => {
  const fork = vi.spyOn(api, "fork").mockResolvedValue(exp({ id: "e-new" }));
  const open = vi.fn();
  renderWithQuery(<ForkButton dag={dag} canEdit open={open} />);
  fireEvent.click(screen.getByText("Fork…"));
  await waitFor(() => expect(open).toHaveBeenCalledWith("e-new"));
  expect(fork).toHaveBeenCalledWith("e-base", "baseline-draft");
});

it("says so when forking fails, rather than doing nothing", async () => {
  vi.spyOn(api, "fork").mockRejectedValue(new ApiError(404, "Not Found"));
  renderWithQuery(<ForkButton dag={dag} canEdit open={vi.fn()} />);
  fireEvent.click(screen.getByText("Fork…"));
  const button = await screen.findByText("Fork failed — retry");
  expect(button.getAttribute("title")).toBe("Fork failed: Not Found");
});

it("is disabled, with the reason, when editing is off", () => {
  renderWithQuery(<ForkButton dag={dag} canEdit={false} open={vi.fn()} />);
  const button = screen.getByText("Fork…") as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  expect(button.title).toMatch(/Editing is off/);
});
