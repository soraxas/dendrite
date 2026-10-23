import { expect, it } from "vitest";
import { artifactCategory } from "./ArtifactIcon";

it.each([
  ["frame.PNG", "image"], ["clip.webm", "video"], ["voice.opus", "audio"],
  ["data.jsonl", "code"], ["backup.tar.gz", "archive"], ["cloud.ply", "model"],
  ["report.pdf", "document"], ["results.parquet", "table"], ["README", "file"],
  ["/folder.png/unknown.bin", "file"],
])("identifies %s from the filename", (filename, category) => {
  expect(artifactCategory({ kind: "file", filename })).toBe(category);
});

it("uses the artifact kind for directories and Python values", () => {
  expect(artifactCategory({ kind: "dir", filename: "frames.png" })).toBe("folder");
  expect(artifactCategory({ kind: "value", filename: null })).toBe("value");
  expect(artifactCategory({ filename: null })).toBe("file");
});
