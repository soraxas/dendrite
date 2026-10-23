// One app-wide "you have unsaved edits" flag, so navigation can ask before throwing them away.
let pending: string | null = null;

export function setUnsaved(what: string | null): void {
  pending = what;
}

/** True when it's fine to leave: nothing unsaved, or the user agreed to discard it. */
export function confirmLeave(): boolean {
  return !pending || window.confirm(`Discard unsaved changes to ${pending}?`);
}
