import { cleanup, screen, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FileViewer from "./FileViewer";

afterEach(cleanup);

// Unknown types complete successfully in the real library, without firing an error.
// The wrapper must choose the text fallback before it mounts the renderer.
it.each(["README", "results.custom"])("preserves UTF-8 text for %s without relying on a viewer error", (filename) => {
  render(<FileViewer url="/api/artifacts/hash/download" filename={filename} fallbackText="Results: 42" onError={vi.fn()} />);
  expect(screen.getByText("Results: 42").tagName).toBe("PRE");
});
