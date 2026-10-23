import type { ReactNode } from "react";
import type { ArtifactRef } from "../api";

type Category = "image" | "video" | "audio" | "code" | "archive" | "model" | "document" | "table" | "folder" | "value" | "file";
export type ArtifactType = { kind?: ArtifactRef["kind"]; filename?: string | null };

const extensions: Partial<Record<Category, string>> = {
  image: "png jpg jpeg gif webp avif bmp svg ico tif tiff heic heif psd raw dng",
  video: "webm mp4 m4v mov mkv avi ogv mpg mpeg wmv flv ts mts m2ts 3gp",
  audio: "mp3 wav ogg oga opus flac aac m4a aiff aif wma mid midi",
  code: "json jsonl ndjson yaml yml toml xml html htm css scss js jsx ts tsx py ipynb sh bash zsh sql r rs go c h cpp hpp java rb php swift kt ini cfg conf",
  archive: "zip gz tgz bz2 xz zst tar rar 7z br",
  model: "obj stl gltf glb fbx ply step stp iges igs 3mf usdz usd usda usdc blend off dae las laz pcd",
  document: "pdf txt md markdown rst log doc docx odt rtf ppt pptx odp epub",
  table: "csv tsv xls xlsx ods parquet arrow feather",
};
const byExtension = new Map(Object.entries(extensions).flatMap(([category, exts]) => exts!.split(" ").map((ext) => [ext, category as Category] as const)));

export function artifactCategory({ kind, filename }: ArtifactType): Category {
  if (kind === "dir") return "folder";
  if (kind === "value") return "value";
  const basename = filename?.split(/[\\/]/).at(-1) ?? "";
  const dot = basename.lastIndexOf(".");
  return dot >= 0 ? byExtension.get(basename.slice(dot + 1).toLowerCase()) ?? "file" : "file";
}

const symbols: Record<Category, ReactNode> = {
  image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8" cy="8" r="1.5" /><path d="m3 17 5-5 4 4 3-3 6 6" /></>,
  video: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m10 8 6 4-6 4Z" /></>,
  audio: <><path d="M9 18V5l12-2v13M9 9l12-2" /><ellipse cx="6" cy="18" rx="3" ry="3" /><ellipse cx="18" cy="16" rx="3" ry="3" /></>,
  code: <><path d="m7 7-5 5 5 5m10-10 5 5-5 5m-4-13-2 20" /></>,
  archive: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M10 3v3h4v3h-4v3h4v3h-4v3h4" /></>,
  model: <><path d="m12 2 10 5v10l-10 5-10-5V7Zm0 10 10-5M12 12 2 7m10 5v10M7 4.5l10 5" /></>,
  document: <><path d="M14 2H5v20h14V7Zm0 0v5h5M8 12h8m-8 4h8" /></>,
  table: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M3 15h18M9 3v18" /></>,
  folder: <path d="M3 5h6l2 3h10v12H3Z" />,
  value: <><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14c0 4 18 4 18 0V5M3 12c0 4 18 4 18 0" /></>,
  file: <path d="M14 2H5v20h14V7Zm0 0v5h5" />,
};

/** Uses recorded filenames; a port's display name (e.g. "output") is not a file type. */
export function ArtifactIcon(props: ArtifactType) {
  const category = artifactCategory(props);
  return <svg className="artifact-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <title>{category === "model" ? "3D model" : category}</title>
    {symbols[category]}
  </svg>;
}
