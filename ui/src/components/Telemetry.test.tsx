import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { renderWithQuery } from "../test/render";
import { RunTelemetry } from "./Telemetry";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("shows the console with progress bars collapsed, and where a reused run came from", async () => {
  vi.spyOn(api, "logs").mockResolvedValue({
    chunks: [
      { seq: 0, stream: "stdout", t: 0, text: "loading\n 10%\r100%\n" },
      { seq: 1, stream: "stderr", t: 0, text: "\x1b[31mwarning\x1b[0m\n" },
    ],
    running: false,
    status: "success",
  });
  const open = vi.fn();
  renderWithQuery(
    <RunTelemetry execId="x1" running={false} from={{ id: "e-parent", name: "baseline" }} onOpenExperiment={open} />,
  );
  await waitFor(() => expect(screen.getByText("100%")).toBeTruthy());
  expect(screen.queryByText(/10%/)).toBeNull();
  expect(screen.getByText("warning").getAttribute("style")).toContain("color");
  fireEvent.click(screen.getByText("baseline"));
  expect(open).toHaveBeenCalledWith("e-parent");
});

it("draws one chart per title with a line per series", async () => {
  vi.spyOn(api, "logs").mockResolvedValue({ chunks: [], running: false, status: "success" });
  vi.spyOn(api, "scalars").mockResolvedValue({
    points: [
      { key: "loss/train", step: 0, value: 1 },
      { key: "loss/train", step: 1, value: 0.5 },
      { key: "loss/val", step: 0, value: 2 },
      { key: "loss/val", step: 1, value: 1 },
    ],
  });
  const { container } = renderWithQuery(<RunTelemetry execId="x2" running={false} />);
  fireEvent.click(screen.getByText("Scalars"));
  await waitFor(() => expect(screen.getByLabelText("loss chart")).toBeTruthy());
  expect(container.querySelectorAll("polyline")).toHaveLength(2);
});

it("lists samples with a step slider", async () => {
  vi.spyOn(api, "logs").mockResolvedValue({ chunks: [], running: false, status: "success" });
  vi.spyOn(api, "samples").mockResolvedValue({
    samples: [0, 1, 2].map((step) => ({
      title: "render",
      series: "render",
      step,
      hash: `h${step}`,
      t: 0,
      kind: "file",
      size: 10,
      filename: `render-${step}.png`,
    })),
  });
  renderWithQuery(<RunTelemetry execId="x3" running={false} />);
  fireEvent.click(screen.getByText("Samples"));
  await waitFor(() => expect(screen.getByAltText("render at step 2")).toBeTruthy());
  fireEvent.change(screen.getByLabelText("render step"), { target: { value: "0" } });
  expect(screen.getByAltText("render at step 0")).toBeTruthy();
});
