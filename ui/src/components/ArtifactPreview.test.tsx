import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type ArtifactDetail } from "../api";
import { renderWithQuery } from "../test/render";
import { ArtifactDetailPanel, type Nav } from "./DetailPanel";

// Render engines need browser workers/canvas; exercise our selection and error handling here.
vi.mock("./FileViewer", () => ({
  default: ({ filename, url, onError }: { filename: string; url: string; onError: () => void }) => (
    <div>
      <a href={url}>Viewing {filename}</a>
      <button onClick={onError}>Simulate renderer failure</button>
    </div>
  ),
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const nav: Nav = { openExp: vi.fn(), openStep: vi.fn(), selectArtifact: vi.fn(), selectExec: vi.fn(), trace: vi.fn(), nameOf: (id) => id };
const artifact = (over: Partial<ArtifactDetail> = {}): ArtifactDetail => ({
  hash: "abc123", kind: "file", filename: "report.pdf", size: 100000, present: true,
  producer: null, experiments: [], preview: { type: "too_large" }, ...over,
});

function setup(a: ArtifactDetail) {
  vi.spyOn(api, "artifact").mockResolvedValue(a);
  return renderWithQuery(<ArtifactDetailPanel hash={a.hash} nav={nav} canTrace />);
}

it("previews files above the old text limit using their original filename", async () => {
  setup(artifact());
  const viewer = await screen.findByText("Viewing report.pdf");
  expect(viewer.getAttribute("href")).toBe("/api/artifacts/abc123/download");
  expect(screen.queryByText("Too large to preview.")).toBeNull();
});

it("opens nested directory members without treating their hashes as registered artifacts", async () => {
  setup(artifact({ kind: "dir", filename: "reports", preview: { type: "manifest", count: 2, entries: [
    { path: "nested/a report.csv", hash: "member1", size: 20 },
    { path: "plot.png", hash: "member2", size: 40 },
  ] } }));
  fireEvent.click(await screen.findByRole("button", { name: "nested/a report.csv" }));
  expect((await screen.findByText("Viewing a report.csv")).getAttribute("href"))
    .toBe("/api/artifacts/abc123/download?path=nested%2Fa%20report.csv");
  fireEvent.click(screen.getByRole("button", { name: "plot.png" }));
  expect((await screen.findByText("Viewing plot.png")).getAttribute("href"))
    .toBe("/api/artifacts/abc123/download?path=plot.png");
});

it("keeps a download available when the renderer cannot preview a file", async () => {
  setup(artifact({ filename: "damaged.pdf", preview: { type: "binary" } }));
  fireEvent.click(await screen.findByRole("button", { name: "Simulate renderer failure" }));
  expect(await screen.findByText(/Unable to preview this file/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "Download file" }).getAttribute("href"))
    .toBe("/api/artifacts/abc123/download");
});

it("preserves Python value previews", async () => {
  setup(artifact({ kind: "value", filename: null, preview: { type: "value", repr: "[1, 2, 3]" } }));
  expect(await screen.findByText("[1, 2, 3]")).toBeTruthy();
  expect(screen.queryByText(/Viewing/)).toBeNull();
});

it("preserves the server text when rendering fails", async () => {
  setup(artifact({ filename: "report.txt", preview: { type: "text", text: "Scientific results: 42" } }));
  fireEvent.click(await screen.findByRole("button", { name: "Simulate renderer failure" }));
  expect(await screen.findByText("Scientific results: 42")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Download file" })).toBeTruthy();
});

it("does not fetch files missing from the store", async () => {
  setup(artifact({ present: false, preview: { type: "missing" } }));
  expect(await screen.findByText("Blob missing from store!")).toBeTruthy();
  expect(screen.queryByText(/Viewing/)).toBeNull();
});
