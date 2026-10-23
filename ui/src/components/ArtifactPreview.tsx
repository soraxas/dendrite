import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type ArtifactDetail } from "../api";
import { fmtSize } from "../format";

const FileViewer = lazy(() => import("./FileViewer"));

type PreviewSource = { url: string; filename: string; fallbackText?: string };

function Unavailable({ url, fallbackText }: { url: string; fallbackText?: string }) {
  return <div className="preview-unavailable" role="status">
    {fallbackText !== undefined ? <pre>{fallbackText}</pre> : <p>Unable to preview this file. Its format may be unsupported or the file may be damaged.</p>}
    <a href={url}>Download file</a>
  </div>;
}

class ViewerBoundary extends Component<{ children: ReactNode; url: string; fallbackText?: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <Unavailable url={this.props.url} fallbackText={this.props.fallbackText} /> : this.props.children; }
}

function ViewerContent({ url, filename, fallbackText }: PreviewSource) {
  const [failed, setFailed] = useState(false);
  return failed ? <Unavailable url={url} fallbackText={fallbackText} /> : <ViewerBoundary url={url} fallbackText={fallbackText}>
    <Suspense fallback={<div className="preview-unavailable" role="status">Loading preview…</div>}>
      <FileViewer url={url} filename={filename} fallbackText={fallbackText} onError={() => setFailed(true)} />
    </Suspense>
  </ViewerBoundary>;
}

function FilePreview({ url, filename, fallbackText }: PreviewSource) {
  const [expanded, setExpanded] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (expanded) dialog.current?.showModal();
  }, [expanded]);
  return <>
    <div className="file-preview-heading">
      <span className="mono" title={filename}>{filename}</span>
      <button onClick={() => setExpanded(true)}>Expand preview</button>
    </div>
    <div className="file-preview">
      {!expanded && <ViewerContent key={url} url={url} filename={filename} fallbackText={fallbackText} />}
    </div>
    {expanded && <dialog ref={dialog} className="file-preview-dialog" aria-label={`Preview ${filename}`} onCancel={() => setExpanded(false)}>
      <div className="file-preview-heading">
        <span className="mono">{filename}</span>
        <a href={url}>Download file</a>
        <button onClick={() => setExpanded(false)} autoFocus>Close preview</button>
      </div>
      <div className="file-preview"><ViewerContent key={url} url={url} filename={filename} fallbackText={fallbackText} /></div>
    </dialog>}
  </>;
}

function DirectoryPreview({ artifact }: { artifact: ArtifactDetail }) {
  const [path, setPath] = useState<string | null>(null);
  const [limit, setLimit] = useState(80);
  const preview = artifact.preview;
  if (preview.type !== "manifest") return null;
  return <>
    <table>
      <thead><tr><th>path ({preview.count})</th><th className="num">size</th></tr></thead>
      <tbody>{preview.entries.slice(0, limit).map((entry) => <tr key={entry.path}>
        <td><button className="file-entry mono" aria-pressed={path === entry.path} onClick={() => setPath(entry.path)}>{entry.path}</button></td>
        <td className="num">{fmtSize(entry.size)}</td>
      </tr>)}</tbody>
    </table>
    {preview.entries.length > limit && <button onClick={() => setLimit(preview.entries.length)}>Show more files</button>}
    {preview.count > preview.entries.length && <p className="muted">Showing the first {preview.entries.length} of {preview.count} files.</p>}
    {path && <FilePreview key={path} url={api.downloadUrl(artifact.hash, path)} filename={path.split("/").at(-1)!} />}
  </>;
}

export function ArtifactPreview({ artifact: a }: { artifact: ArtifactDetail }) {
  if (!a.present || a.preview.type === "missing") return <span className="err">Blob missing from store!</span>;
  if (a.kind === "file") return <FilePreview key={a.hash} url={api.downloadUrl(a.hash)} filename={a.filename ?? a.hash}
    fallbackText={a.preview.type === "text" ? a.preview.text : undefined} />;
  if (a.kind === "dir") return <DirectoryPreview key={a.hash} artifact={a} />;
  if (a.preview.type === "value") return <pre>{a.preview.repr}</pre>;
  if (a.preview.type === "too_large") return <span className="muted">Too large to preview.</span>;
  return null;
}
