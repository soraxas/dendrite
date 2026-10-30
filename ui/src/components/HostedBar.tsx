// Who you are and which project you're in, on a hosted dendrite server.
import { useState } from "react";
import type { HostedInfo } from "../api";

export function HostedBar({ hosted }: { hosted: HostedInfo }) {
  const [busy, setBusy] = useState(false);
  const signOut = async () => {
    setBusy(true);
    try {
      await fetch("/auth/logout", { method: "POST", headers: { "X-Dendrite-CSRF": hosted.csrf ?? "" } });
    } finally {
      window.location.assign("/");
    }
  };
  return (
    <details className="lab hosted">
      <summary title="Signed in to a dendrite server">
        {hosted.project} · {hosted.user}
      </summary>
      <div className="lab-pop">
        <dl className="kv">
          <dt>project</dt>
          <dd>{hosted.project}</dd>
          <dt>role</dt>
          <dd>{hosted.role ?? "none"}</dd>
          <dt>signed in as</dt>
          <dd>{hosted.user}</dd>
        </dl>
        {(hosted.projects?.length ?? 0) > 1 && (
          <p>
            <a href="/">All projects</a> ({hosted.projects?.length})
          </p>
        )}
        <button disabled={busy} onClick={signOut}>
          Sign out
        </button>
      </div>
    </details>
  );
}
