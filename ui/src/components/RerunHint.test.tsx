import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { RerunHint } from "./DetailPanel";

afterEach(cleanup);

it("gives the CLI command for a pipeline step and the method for an eager one", () => {
  render(<RerunHint exp="e-1" pipeline="recon" step="tile[3]" />);
  expect(screen.getByText("dendrite run e-1 --rerun tile[3]")).toBeTruthy();
  cleanup();
  render(<RerunHint exp="e-2" pipeline="session" step="count_lines[2]" />);
  expect(screen.getByText(/count_lines\.rerun\(\.\.\.\)/)).toBeTruthy();
});
