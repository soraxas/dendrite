import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";
import { fmtSize } from "../format";
import { inventory, report, storageExp } from "../test/fixtures";
import { renderWithQuery } from "../test/render";
import {
  BulkDelete,
  CleanupPanel,
  countSkips,
  DeleteExperiment,
  descendantsOf,
  groupReport,
  oldExperiments,
  orphanedForks,
  StoragePage,
} from "./Storage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const disabled = (el: HTMLElement) => (el as HTMLButtonElement | HTMLInputElement).disabled;
const nameOf = (id: string) => ({ "e-a": "lr-sweep", "e-b": "lr-sweep-2" })[id] ?? id;

describe("report grouping", () => {
  it("orders groups by reason and labels them", () => {
    const g = groupReport(report());
    expect(g.map((x) => x.why)).toEqual(["orphan", "unreferenced", "unused-since"]);
    expect(g[1]).toMatchObject({ count: 1, bytes: 3000, label: "Used only by deleted experiments or replaced runs" });
  });
  it("counts skipped items per reason", () => {
    expect(countSkips(report())).toEqual([
      { why: "grace", label: "written in the last hour (may still be uploading)", count: 1 },
    ]);
  });
  it("finds every fork below an experiment", () => {
    const rows = [
      { id: "e-base", parent_id: null },
      { id: "e-a", parent_id: "e-base" },
      { id: "e-b", parent_id: "e-a" },
      { id: "e-c", parent_id: null },
    ];
    expect(descendantsOf(rows, "e-base")).toEqual(["e-a", "e-b"]);
    expect(descendantsOf(rows, "e-c")).toEqual([]);
  });
  it("formats byte sizes by magnitude", () => {
    expect([fmtSize(512), fmtSize(2048), fmtSize(5 << 20), fmtSize(3 * 2 ** 30)]).toEqual([
      "512 B",
      "2.0 KB",
      "5.0 MB",
      "3.00 GB",
    ]);
  });
});

describe("DeleteExperiment", () => {
  it("asks first, says what it frees, and deletes on confirm", async () => {
    const est = vi.spyOn(api, "estimate").mockResolvedValue({ ids: ["e-base"], bytes: 2 << 20 });
    const del = vi
      .spyOn(api, "deleteExperiment")
      .mockResolvedValue({ deleted: "e-base", ids: ["e-base"], reparented: {}, parent_id: null });
    const onDeleted = vi.fn();
    renderWithQuery(<DeleteExperiment exp={storageExp()} forks={[]} canEdit nameOf={nameOf} onDeleted={onDeleted} />);
    expect(est).not.toHaveBeenCalled(); // only asked once the dialog opens
    fireEvent.click(screen.getByText("Delete…"));
    expect(del).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("alertdialog").textContent).toContain("2.0 MB"));
    fireEvent.click(screen.getByText("Cancel"));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    fireEvent.click(screen.getByText("Delete…"));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(["e-base"], null));
    expect(del).toHaveBeenCalledWith("e-base", "refuse");
  });

  it("keeps forks by default, moving them to the parent", async () => {
    const est = vi.spyOn(api, "estimate").mockResolvedValue({ ids: ["e-base"], bytes: 1 << 20 });
    const del = vi
      .spyOn(api, "deleteExperiment")
      .mockResolvedValue({ deleted: "e-base", ids: ["e-base"], reparented: { "e-a": "e-root" }, parent_id: "e-root" });
    renderWithQuery(
      <DeleteExperiment
        exp={storageExp({ parent_id: "e-root" })}
        forks={["e-a", "e-b"]}
        canEdit
        nameOf={(id) => (id === "e-root" ? "root" : nameOf(id))}
        onDeleted={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Delete…"));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("lr-sweep, lr-sweep-2");
    expect(dialog.textContent).toContain("they become forks of root");
    expect((screen.getByLabelText(/Keep them/) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("e-base", "keep"));
    expect(est).toHaveBeenCalledWith(["e-base"], "keep");
  });

  it("deletes forks too when chosen, with the estimate for all of them", async () => {
    const est = vi
      .spyOn(api, "estimate")
      .mockImplementation(async (_ids, forks) => ({ ids: [], bytes: forks === "delete" ? 7 << 20 : 1 << 20 }));
    const del = vi
      .spyOn(api, "deleteExperiment")
      .mockResolvedValue({ deleted: "e-base", ids: ["e-b", "e-a", "e-base"], reparented: {}, parent_id: null });
    renderWithQuery(
      <DeleteExperiment exp={storageExp()} forks={["e-a", "e-b"]} canEdit nameOf={nameOf} onDeleted={vi.fn()} />,
    );
    fireEvent.click(screen.getByText("Delete…"));
    fireEvent.click(screen.getByLabelText(/Delete them too/));
    await waitFor(() => expect(screen.getByRole("alertdialog").textContent).toContain("7.0 MB"));
    fireEvent.click(screen.getByRole("button", { name: "Delete 3" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("e-base", "delete"));
    expect(est).toHaveBeenLastCalledWith(["e-base"], "delete");
  });

  it("is disabled when editing is off or the experiment runs", () => {
    renderWithQuery(
      <DeleteExperiment exp={storageExp()} forks={[]} canEdit={false} nameOf={nameOf} onDeleted={vi.fn()} />,
    );
    expect(disabled(screen.getByText("Delete…"))).toBe(true);
    cleanup();
    renderWithQuery(
      <DeleteExperiment
        exp={storageExp({ status: "running" })}
        forks={[]}
        canEdit
        nameOf={nameOf}
        onDeleted={vi.fn()}
      />,
    );
    expect(disabled(screen.getByText("Delete…"))).toBe(true);
  });
});

describe("selection helpers", () => {
  const rows = [
    storageExp({ id: "e-old", finished_at: 1_000 }),
    storageExp({ id: "e-kept", finished_at: 1_000, keep: true }),
    storageExp({ id: "e-run", finished_at: null, created_at: 1_000, status: "running" }),
    storageExp({ id: "e-new", finished_at: 99 * 86400 }),
    storageExp({ id: "e-fork", parent_id: "e-old", finished_at: 99 * 86400 }),
  ];
  it("picks finished, unkept, not-running experiments older than N days", () => {
    expect(oldExperiments(rows, 30, 100 * 86400)).toEqual(["e-old"]);
    expect(oldExperiments(rows, 0, 100 * 86400)).toEqual(["e-old", "e-new", "e-fork"]);
  });
  it("finds forks a selection would leave behind", () => {
    expect(orphanedForks(rows, new Set(["e-old"]))).toEqual(["e-fork"]);
    expect(orphanedForks(rows, new Set(["e-old", "e-fork"]))).toEqual([]);
  });
});

describe("BulkDelete", () => {
  it("shows the combined estimate and deletes the selection, keeping forks", async () => {
    const rows = [
      storageExp({ id: "e-a", name: "lr-sweep" }),
      storageExp({ id: "e-b", name: "lr-sweep-2" }),
      storageExp({ id: "e-c", name: "kid", parent_id: "e-a" }),
    ];
    vi.spyOn(api, "estimate").mockResolvedValue({ ids: ["e-a", "e-b"], bytes: 9 << 20 });
    const many = vi
      .spyOn(api, "deleteMany")
      .mockResolvedValue({ deleted: ["e-a", "e-b"], reparented: { "e-c": null } });
    const clear = vi.fn();
    renderWithQuery(<BulkDelete rows={rows} selected={new Set(["e-a", "e-b"])} clear={clear} nameOf={nameOf} />);
    await waitFor(() => expect(screen.getByRole("region").textContent).toContain("9.0 MB"));
    fireEvent.click(screen.getByText("Delete selected…"));
    expect(screen.getByRole("alertdialog").textContent).toContain("1 fork: e-c");
    fireEvent.click(screen.getByRole("button", { name: "Delete 2" }));
    await waitFor(() => expect(many).toHaveBeenCalledWith(["e-a", "e-b"], "keep"));
    expect(clear).toHaveBeenCalled();
  });
});

describe("CleanupPanel", () => {
  it("previews, then applies exactly the previewed hashes", async () => {
    const preview = report();
    const gc = vi
      .spyOn(api, "gc")
      .mockResolvedValueOnce(preview)
      .mockResolvedValueOnce({ ...preview, dry_run: false });
    renderWithQuery(<CleanupPanel canEdit collectable={6000} />);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.change(screen.getByLabelText("days"), { target: { value: "14" } });
    fireEvent.click(screen.getByText("Preview"));
    await screen.findByText("Left over from failed or interrupted runs");
    expect(gc).toHaveBeenLastCalledWith({ older_than_days: 14 });
    expect(screen.getByText(/Skipping 1/)).toBeTruthy();
    fireEvent.click(screen.getByText(/Delete 3 items/));
    await screen.findByText(/Deleted 3 items/);
    expect(gc).toHaveBeenLastCalledWith({
      older_than_days: 14,
      apply: true,
      hashes: preview.deleted.map((d) => d.hash),
    });
  });

  it("previews without editing, but can't apply", async () => {
    vi.spyOn(api, "gc").mockResolvedValue(report());
    renderWithQuery(<CleanupPanel canEdit={false} collectable={6000} />);
    fireEvent.click(screen.getByText("Preview"));
    expect(disabled(await screen.findByText(/Delete 3 items/))).toBe(true);
  });

  it("needs an explicit override while something runs, then forces", async () => {
    const preview = report();
    const gc = vi
      .spyOn(api, "gc")
      .mockResolvedValueOnce(preview)
      .mockResolvedValueOnce({ ...preview, dry_run: false });
    renderWithQuery(<CleanupPanel canEdit collectable={6000} busy={["experiment e-x is running"]} />);
    fireEvent.click(screen.getByText("Preview"));
    const go = await screen.findByText(/Delete 3 items/);
    expect(disabled(go)).toBe(true);
    fireEvent.click(screen.getByLabelText(/clean up anyway/));
    expect(disabled(go)).toBe(false);
    fireEvent.click(go);
    await screen.findByText(/Deleted 3 items/);
    expect(gc).toHaveBeenLastCalledWith(expect.objectContaining({ apply: true, force: true }));
  });

  it("re-previews with recently written data included, and applies with it", async () => {
    const fresh = report({
      deleted: [],
      bytes: 0,
      groups: {},
      skipped: [{ hash: "a".repeat(64), size: 1000, why: "grace" }],
    });
    const all = report();
    const gc = vi
      .spyOn(api, "gc")
      .mockResolvedValueOnce(fresh)
      .mockResolvedValueOnce(all)
      .mockResolvedValueOnce({ ...all, dry_run: false });
    renderWithQuery(<CleanupPanel canEdit collectable={6000} />);
    fireEvent.click(screen.getByText("Preview"));
    fireEvent.click(await screen.findByLabelText(/Include data written in the last hour/));
    fireEvent.click(await screen.findByText(/Delete 3 items/));
    await screen.findByText(/Deleted 3 items/);
    expect(gc).toHaveBeenNthCalledWith(2, { older_than_days: null, include_recent: true });
    expect(gc).toHaveBeenLastCalledWith(expect.objectContaining({ apply: true, include_recent: true }));
  });

  it("shows why the server refused", async () => {
    vi.spyOn(api, "gc")
      .mockResolvedValueOnce(report())
      .mockRejectedValueOnce(new ApiError(409, "experiment e-x is running", "busy"));
    renderWithQuery(<CleanupPanel canEdit collectable={6000} />);
    fireEvent.click(screen.getByText("Preview"));
    fireEvent.click(await screen.findByText(/Delete 3 items/));
    expect(await screen.findByText("experiment e-x is running")).toBeTruthy();
  });
});

describe("StoragePage", () => {
  const props = { go: vi.fn(), nameOf, openExp: vi.fn(), openArtifact: vi.fn() };

  it("shows totals and per-experiment sizes", async () => {
    vi.spyOn(api, "storage").mockResolvedValue(inventory());
    renderWithQuery(<StoragePage tab="experiments" canEdit {...props} />);
    expect(await screen.findByText("Can be cleaned up")).toBeTruthy();
    expect(screen.getByText("9.0 MB")).toBeTruthy(); // stored
    expect(screen.getByText("2.0 MB")).toBeTruthy(); // frees
    expect(screen.getByText("base")).toBeTruthy();
  });

  it("explains and disables mutations when editing is off", async () => {
    vi.spyOn(api, "storage").mockResolvedValue(inventory());
    renderWithQuery(<StoragePage tab="experiments" canEdit={false} {...props} />);
    await screen.findByText("base");
    expect(screen.getAllByText(/Editing is off/).length).toBeGreaterThan(0);
    expect(disabled(screen.getByText("Delete…"))).toBe(true);
    expect(disabled(screen.getByRole("checkbox", { name: "keep" }))).toBe(true);
    expect(disabled(screen.getByText("Clear"))).toBe(true);
  });

  it("evicts a cache entry after previewing it", async () => {
    const entry = {
      execution_id: "x1",
      node: "train",
      experiment_id: "e-a",
      finished_at: 1,
      duration: 840,
      outputs: {},
      bytes: 4096,
      present: true,
      uses: 2,
      reused: 1,
      last_used: 1,
      live: true,
    };
    vi.spyOn(api, "storage").mockResolvedValue(inventory({ cache: [entry] }));
    const evict = vi
      .spyOn(api, "evict")
      .mockResolvedValueOnce(
        report({ deleted: [{ hash: "e".repeat(64), size: 4096, why: "evicted:x1" }], bytes: 4096, skipped: [] }),
      )
      .mockResolvedValueOnce(report({ dry_run: false }));
    renderWithQuery(<StoragePage tab="cache" canEdit {...props} />);
    fireEvent.click(await screen.findByText("Evict…"));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("took 14.0m");
    expect(evict).toHaveBeenLastCalledWith("x1", false, false);
    fireEvent.click(screen.getByRole("button", { name: "Evict" }));
    await waitFor(() => expect(evict).toHaveBeenLastCalledWith("x1", true, false));
  });

  it("offers to mark a crashed run stopped", async () => {
    vi.spyOn(api, "storage").mockResolvedValue(
      inventory({
        busy: ["experiment e-run is running"],
        experiments: [storageExp({ id: "e-run", name: "stuck", status: "running" })],
      }),
    );
    const stop = vi.spyOn(api, "stop").mockResolvedValue({} as never);
    renderWithQuery(<StoragePage tab="experiments" canEdit {...props} />);
    expect((await screen.findByRole("status")).textContent).toContain("e-run is running");
    fireEvent.click(screen.getAllByText("Mark stuck stopped…")[0]);
    fireEvent.click(screen.getByRole("button", { name: "Mark stopped" }));
    await waitFor(() => expect(stop).toHaveBeenCalledWith("e-run"));
    expect(disabled(screen.getByLabelText("select stuck"))).toBe(true); // running rows can't be bulk-selected
  });

  it("evicts a large artifact, but never an experiment input", async () => {
    const big = {
      hash: "f".repeat(64),
      kind: "dir",
      filename: "frames",
      size: 9 << 20,
      producer_node: "extract",
      present: true,
      live: true,
      experiments: 2,
    };
    const input = { ...big, hash: "9".repeat(64), filename: "video.bin", producer_node: null };
    vi.spyOn(api, "storage").mockResolvedValue(inventory({ largest: [big, input] }));
    const ev = vi
      .spyOn(api, "evictArtifact")
      .mockResolvedValue(
        report({ deleted: [{ hash: big.hash, size: big.size, why: "evicted:f" }], bytes: big.size, skipped: [] }),
      );
    renderWithQuery(<StoragePage tab="largest" canEdit {...props} />);
    expect(await screen.findAllByText("Evict…")).toHaveLength(1);
    fireEvent.click(screen.getByText("Evict…"));
    await screen.findByRole("alertdialog");
    fireEvent.click(screen.getByRole("button", { name: "Evict" }));
    await waitFor(() => expect(ev).toHaveBeenLastCalledWith(big.hash, true, false));
  });

  it("selects old experiments for bulk deletion", async () => {
    const old = storageExp({ id: "e-a", name: "lr-sweep", finished_at: 1_000 });
    const kept = storageExp({ id: "e-k", name: "best", finished_at: 1_000, keep: true });
    vi.spyOn(api, "storage").mockResolvedValue(inventory({ experiments: [old, kept] }));
    vi.spyOn(api, "estimate").mockResolvedValue({ ids: ["e-a"], bytes: 1 << 20 });
    renderWithQuery(<StoragePage tab="experiments" canEdit {...props} />);
    fireEvent.click(await screen.findByText("Select old"));
    expect((screen.getByLabelText("select lr-sweep") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("select best") as HTMLInputElement).checked).toBe(false);
    expect(await screen.findByText("Delete selected…")).toBeTruthy();
  });
});
