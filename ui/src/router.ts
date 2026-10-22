// Hash routing: #/exp/:id[/params|metrics|diff/:other] and #/artifact/:hash[/from/:exp],
// with the graph selection in a `sel` query param so it survives polling, reloads and links.
import { useCallback, useSyncExternalStore } from "react";

export type View = "home" | "dag" | "params" | "metrics" | "diff" | "lineage";

export interface Route {
  view: View;
  exp?: string;
  other?: string;
  artifact?: string;
  sel?: string; // "<kind>:<id>" — node:<name> | input:<name> | artifact:<hash> | exec:<id>
}

export function parse(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const sel = new URLSearchParams(query).get("sel") ?? undefined;
  if (parts[0] === "exp" && parts[1]) {
    const sub = parts[2] as View | undefined;
    if (sub === "diff") return { view: "diff", exp: parts[1], other: parts[3], sel };
    if (sub === "params" || sub === "metrics") return { view: sub, exp: parts[1], sel };
    return { view: "dag", exp: parts[1], sel };
  }
  if (parts[0] === "artifact" && parts[1]) {
    return { view: "lineage", artifact: parts[1], exp: parts[2] === "from" ? parts[3] : undefined, sel };
  }
  return { view: "home", sel };
}

export function href(r: Route): string {
  const enc = encodeURIComponent;
  let path = "#/";
  if (r.view === "lineage" && r.artifact) path = `#/artifact/${enc(r.artifact)}${r.exp ? `/from/${enc(r.exp)}` : ""}`;
  else if (r.exp) {
    path = `#/exp/${enc(r.exp)}`;
    if (r.view === "params" || r.view === "metrics") path += `/${r.view}`;
    if (r.view === "diff" && r.other) path += `/diff/${enc(r.other)}`;
  }
  return r.sel ? `${path}?sel=${enc(r.sel)}` : path;
}

function subscribe(cb: () => void) {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
}

export function useRoute(): [Route, (r: Route, opts?: { replace?: boolean }) => void] {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  const navigate = useCallback((r: Route, opts?: { replace?: boolean }) => {
    const next = href(r);
    if (next === location.hash) return;
    if (opts?.replace) {
      history.replaceState(null, "", next);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } else location.hash = next;
  }, []);
  return [parse(hash), navigate];
}

export function splitSel(sel?: string): { kind?: string; id?: string } {
  if (!sel) return {};
  const i = sel.indexOf(":");
  return i < 0 ? {} : { kind: sel.slice(0, i), id: sel.slice(i + 1) };
}
