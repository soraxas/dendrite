import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, setCsrf } from "../api";
import { HostedBar } from "./HostedBar";

afterEach(() => {
  cleanup();
  setCsrf(null);
  vi.unstubAllGlobals();
});

it("shows the project, role and identity on a hosted server", () => {
  render(
    <HostedBar
      hosted={{ project: "recon", user: "Alice", role: "runner", csrf: "c", projects: ["recon", "splats"] }}
    />,
  );
  expect(screen.getByText("recon · Alice")).toBeTruthy();
  expect(screen.getByText("runner")).toBeTruthy();
  expect(screen.getByRole("link", { name: "All projects" }).getAttribute("href")).toBe("/");
});

it("signs out with the session's CSRF token", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}"));
  vi.stubGlobal("fetch", fetch);
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign });
  render(<HostedBar hosted={{ project: "recon", user: "Alice", role: "viewer", csrf: "tok" }} />);
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
  expect(fetch).toHaveBeenCalledWith("/auth/logout", { method: "POST", headers: { "X-Dendrite-CSRF": "tok" } });
});

it("sends the CSRF header on changes, not on reads, once a hosted session provides it", async () => {
  const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response("{}")));
  vi.stubGlobal("fetch", fetch);
  await api.gc({ apply: false });
  expect(fetch.mock.calls[0][1].headers["X-Dendrite-CSRF"]).toBeUndefined();
  setCsrf("tok");
  await api.gc({ apply: false });
  await api.info();
  expect(fetch.mock.calls[1][0]).toBe("api/gc");
  expect(fetch.mock.calls[1][1].headers["X-Dendrite-CSRF"]).toBe("tok");
  expect(fetch.mock.calls[2][1]).toBeUndefined();
});
