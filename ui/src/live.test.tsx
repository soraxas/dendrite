import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isLive, useLiveUpdates } from "./queries";

class FakeSource {
  static last: FakeSource;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeSource.last = this;
  }
  close() {
    this.closed = true;
  }
}

function Live() {
  useLiveUpdates();
  return null;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("live updates", () => {
  it("refetches only what an event names, and reports the connection", () => {
    vi.stubGlobal("EventSource", FakeSource);
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <Live />
      </QueryClientProvider>,
    );
    const es = FakeSource.last;
    expect(es.url).toBe("/api/events");
    act(() => es.onopen!());
    expect(isLive()).toBe(true);
    act(() => es.onmessage!({ data: JSON.stringify({ hello: true }) }));
    expect(invalidate).not.toHaveBeenCalled();
    act(() => es.onmessage!({ data: JSON.stringify({ tree: true, experiments: ["e-1"] }) }));
    const keys = invalidate.mock.calls.map((c) => JSON.stringify(c[0]!.queryKey));
    expect(keys).toEqual(expect.arrayContaining(['["experiments"]', '["dag","e-1"]', '["plan","e-1"]']));
    expect(keys).not.toContain('["dag","e-2"]');
    // a burst of events for one experiment refetches it at most once per gap
    invalidate.mockClear();
    act(() => {
      for (let i = 0; i < 10; i++) es.onmessage!({ data: JSON.stringify({ experiments: ["e-1"] }) });
    });
    expect(invalidate.mock.calls.filter((c) => JSON.stringify(c[0]!.queryKey) === '["dag","e-1"]').length).toBe(0); // still inside the gap
    act(() => es.onerror!());
    expect(isLive()).toBe(false);
    unmount();
    expect(es.closed).toBe(true);
  });
});
