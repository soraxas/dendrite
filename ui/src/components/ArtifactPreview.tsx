import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type ArtifactDetail } from "../api";
import { fmtSize } from "../format";
import { ArtifactIcon, artifactCategory } from "./ArtifactIcon";

const FileViewer = lazy(() => import("./FileViewer"));
const DIRECTORY_PAGE_SIZE = 10;

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

function MediaPreview({ url, filename }: PreviewSource) {
  const [failed, setFailed] = useState(false);
  const [audioOnly, setAudioOnly] = useState(false);
  const video = artifactCategory({ filename }) === "video";
  // Preserve packaged decoders (such as MP4V) when the browser cannot play the stream itself.
  if (failed) return <ViewerContent url={url} filename={filename} />;
  // A direct URL lets the browser request byte ranges, including metadata at the end of large files.
  return video
    ? <div className="native-media">
      {audioOnly && <div className="media-notice" role="status">Video playback is unavailable in this browser. Audio may still play. <a href={url}>Download file</a></div>}
      <video className="media-player" src={url} controls playsInline preload="metadata" aria-label={`Preview ${filename}`} onLoadedMetadata={(e) => setAudioOnly(e.currentTarget.videoWidth === 0)} onError={() => setFailed(true)} />
    </div>
    : <div className="audio-preview"><audio src={url} controls preload="metadata" aria-label={`Preview ${filename}`} onError={() => setFailed(true)} /></div>;
}

function FilePreview({ url, filename, fallbackText }: PreviewSource) {
  const [expanded, setExpanded] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const category = artifactCategory({ filename });
  const media = (category === "video" || category === "audio") && !/\.midi?$/i.test(filename);
  const content = () => media
    ? <MediaPreview key={url} url={url} filename={filename} />
    : <ViewerContent key={url} url={url} filename={filename} fallbackText={fallbackText} />;
  useEffect(() => {
    if (expanded) dialog.current?.showModal();
  }, [expanded]);
  return <>
    <div className="file-preview-heading">
      <span className="mono" title={filename}>{filename}</span>
      <button onClick={() => setExpanded(true)}>Expand preview</button>
    </div>
    <div className={`file-preview ${media ? `media-preview ${category}` : ""}`}>
      {!expanded && content()}
    </div>
    {expanded && <dialog ref={dialog} className="file-preview-dialog" aria-label={`Preview ${filename}`} onCancel={() => setExpanded(false)}>
      <div className="file-preview-heading">
        <span className="mono">{filename}</span>
        <a href={url}>Download file</a>
        <button onClick={() => setExpanded(false)} autoFocus>Close preview</button>
      </div>
      <div className={`file-preview ${media ? `media-preview ${category}` : ""}`}>{content()}</div>
    </dialog>}
  </>;
}

function DirectoryPreview({ artifact }: { artifact: ArtifactDetail }) {
  const [path, setPath] = useState<string | null>(null);
  const [limit, setLimit] = useState(DIRECTORY_PAGE_SIZE);
  const preview = artifact.preview;
  if (preview.type !== "manifest") return null;
  return <>
    <table>
      <thead><tr><th>path ({preview.count})</th><th className="num">size</th></tr></thead>
      <tbody>{preview.entries.slice(0, limit).map((entry) => <tr key={entry.path}>
        <td><button className="file-entry mono" aria-pressed={path === entry.path} onClick={() => setPath(entry.path)}><ArtifactIcon kind="file" filename={entry.path} />{entry.path}</button></td>
        <td className="num">{fmtSize(entry.size)}</td>
      </tr>)}</tbody>
    </table>
    <div className="file-list-controls">
      {preview.entries.length > limit && <button onClick={() => setLimit((n) => n + DIRECTORY_PAGE_SIZE)}>
        Show {Math.min(DIRECTORY_PAGE_SIZE, preview.entries.length - limit)} more ({preview.entries.length - limit} remaining)
      </button>}
      {limit > DIRECTORY_PAGE_SIZE && <button onClick={() => setLimit(DIRECTORY_PAGE_SIZE)}>Show fewer</button>}
    </div>
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
