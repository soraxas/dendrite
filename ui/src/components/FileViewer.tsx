// Keep the viewer and its format engines out of the initial application bundle.
import {
  collectFileViewerRendererPlugins,
  createRendererRegistry,
  resolveFileViewerRendererDefinition,
} from "@file-viewer/core";
import Viewer, { fileViewerFullPreset, type ViewerOptions } from "@file-viewer/react-full";
import { useMemo } from "react";

const registry = createRendererRegistry(
  collectFileViewerRendererPlugins(fileViewerFullPreset).flatMap((plugin) => plugin.definitions ?? []),
);

export default function FileViewer({
  url,
  filename,
  fallbackText,
  onError,
}: {
  url: string;
  filename: string;
  fallbackText?: string;
  onError: () => void;
}) {
  const options = useMemo<ViewerOptions>(() => ({ theme: "system", locale: "en-US" }), []);
  // Unsupported formats are a successful library load, so they never reach onStateChange.error.
  if (fallbackText !== undefined && !resolveFileViewerRendererDefinition(registry, { filename })) {
    return (
      <div className="preview-unavailable">
        <pre>{fallbackText}</pre>
        <a href={url}>Download file</a>
      </div>
    );
  }
  return (
    <Viewer
      url={url}
      filename={filename}
      options={options}
      onStateChange={(state) => {
        if (state.error) onError();
      }}
      style={{ width: "100%", height: "100%", minHeight: 0 }}
    />
  );
}
