import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { type ArtifactRef, api, type Dag } from "../api";
import { exp } from "../test/fixtures";
import { renderWithQuery } from "../test/render";
import { ArtifactsPage, Compare } from "./Artifacts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const H = (h: string) => h.padEnd(64, "0");
const ref = (h: string): ArtifactRef => ({ hash: H(h), kind: "value", size: 3, filename: null });
const dagOf = (id: string, parent: string | null, h: string): Dag => ({
  experiment: exp({ id, name: id, parent_id: parent, status: "completed" }),
  edges: [],
  nodes: [{ id: "train", kind: "node", name: "train", status: "computed", seq: 1, outputs: { model: ref(h) } }],
});

it("compares an output across parent and forks, marking identical results", async () => {
  const tree = [
    { id: "p", name: "p", parent_id: null },
    { id: "me", name: "me", parent_id: "p" },
    { id: "f", name: "f", parent_id: "me" },
  ];
  vi.spyOn(api, "experiments").mockResolvedValue(tree as never);
  const compare = vi.spyOn(api, "compare").mockResolvedValue({
    node: "train",
    output: "model",
    columns: [
      { experiment: "me", name: "me", relation: "self", hash: H("a"), artifact: ref("a"), params: [] },
      { experiment: "p", name: "p", relation: "parent", hash: H("a"), artifact: ref("a"), params: [] },
      {
        experiment: "f",
        name: "f",
        relation: "fork",
        hash: H("b"),
        artifact: ref("b"),
        params: [{ key: "train.iterations", a: 1, b: 2, change: "changed" }],
      },
    ],
  });
  const artifact = vi.spyOn(api, "artifact").mockImplementation(
    async (h) =>
      ({
        ...ref("x"),
        hash: h,
        present: true,
        producer: null,
        experiments: [],
        preview: { type: "value", text: h.slice(0, 1) },
        auto_preview_bytes: null,
      }) as never,
  );
  renderWithQuery(
    <Compare
      base={dagOf("me", "p", "a")}
      node="train"
      output="model"
      nameOf={(id) => id}
      openExp={vi.fn()}
      close={vi.fn()}
    />,
  );
  await waitFor(() => expect(screen.getByText(/identical to this experiment/)).toBeTruthy()); // the parent: same hash
  expect(screen.getByText(/train.iterations 1 → 2/)).toBeTruthy();
  expect(compare).toHaveBeenCalledWith("me", "train", "model", ["p", "f"]);
  await waitFor(() => expect(artifact).toHaveBeenCalledWith(H("b")));
  expect(artifact).toHaveBeenCalledTimes(2); // this experiment's and the fork's; the parent's is the same artifact
});

it("filters through the route and selects a row", () => {
  const go = vi.fn();
  const select = vi.fn();
  renderWithQuery(
    <ArtifactsPage
      dag={dagOf("me", null, "a")}
      route={{ view: "artifacts", exp: "me" }}
      go={go}
      select={select}
      nameOf={(id) => id}
      openExp={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByText("model"));
  expect(select).toHaveBeenCalledWith("a".padEnd(64, "0"));
  fireEvent.click(screen.getByText(/^Values/));
  expect(go).toHaveBeenCalledWith({ view: "artifacts", exp: "me", kind: "value" });
  fireEvent.click(screen.getByText("Compare"));
  expect(go).toHaveBeenCalledWith({ view: "artifacts", exp: "me", cmp: "train.model" });
});
