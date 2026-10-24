// Thin wrapper over ace-builds, loaded on demand so the main bundle stays small.

import type { Ace } from "ace-builds";
import { useEffect, useRef } from "react";

export interface Annotation {
  row: number; // 0-based, relative to `value`
  column: number;
  text: string;
  type: "error" | "warning" | "info";
}

interface Props {
  value: string;
  readOnly: boolean;
  firstLine: number;
  onChange: (v: string) => void;
  annotations?: Annotation[];
  /** Move the cursor to this 0-based row; `seq` changes on every request so the same row can repeat. */
  goTo?: { row: number; seq: number };
  focus?: boolean;
}

export default function AceEditor({ value, readOnly, firstLine, onChange, annotations, goTo, focus = true }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<Ace.Editor | null>(null);
  const change = useRef(onChange);
  change.current = onChange;
  const notes = useRef(annotations);
  notes.current = annotations;

  // biome-ignore lint/correctness/useExhaustiveDependencies: mount once; later prop changes are applied by the effects below
  useEffect(() => {
    let disposed = false;
    let media: MediaQueryList | null = null;
    let onScheme: (() => void) | null = null;
    (async () => {
      const ace = (await import("ace-builds/src-noconflict/ace")).default;
      await Promise.all([
        import("ace-builds/src-noconflict/mode-python"),
        import("ace-builds/src-noconflict/theme-github_light_default"),
        import("ace-builds/src-noconflict/theme-github_dark"),
      ]);
      if (disposed || !host.current) return;
      const ed = ace.edit(host.current, {
        mode: "ace/mode/python",
        fontSize: 13,
        showPrintMargin: false,
        useWorker: false,
        tabSize: 4,
        useSoftTabs: true,
        firstLineNumber: firstLine,
      });
      media = window.matchMedia("(prefers-color-scheme: dark)");
      onScheme = () => ed.setTheme(media!.matches ? "ace/theme/github_dark" : "ace/theme/github_light_default");
      onScheme();
      media.addEventListener("change", onScheme);
      ed.setValue(value, -1);
      ed.setReadOnly(readOnly);
      ed.session.on("change", () => change.current(ed.getValue()));
      editor.current = ed;
      ed.session.setAnnotations(notes.current ?? []);
      if (focus) ed.focus();
    })();
    return () => {
      disposed = true;
      if (media && onScheme) media.removeEventListener("change", onScheme);
      editor.current?.destroy();
      editor.current = null;
    };
  }, []);

  useEffect(() => {
    const ed = editor.current;
    if (ed && ed.getValue() !== value) ed.setValue(value, -1);
  }, [value]);
  useEffect(() => editor.current?.setReadOnly(readOnly), [readOnly]);
  useEffect(() => editor.current?.session.setAnnotations(annotations ?? []), [annotations]);
  useEffect(() => {
    const ed = editor.current;
    if (ed && goTo) {
      ed.gotoLine(goTo.row + 1, 0, true);
      ed.focus();
    }
  }, [goTo]);

  return <div ref={host} className="ace-host" aria-label="Source code" />;
}
