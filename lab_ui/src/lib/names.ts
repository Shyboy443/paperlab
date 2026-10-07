import { useSyncExternalStore } from "react";

// Which name the page shows: PaperLab's own (the one Telegram and the operator console use) or the champion name
// from the Lovable dashboard. Remembered per browser; storage can be unavailable, so every access is guarded.
export type NameMode = "paperlab" | "champion";

const KEY = "paperlab.lab.names";
const listeners = new Set<() => void>();

function read(): NameMode {
  try {
    return localStorage.getItem(KEY) === "champion" ? "champion" : "paperlab";
  } catch {
    return "paperlab";
  }
}

let mode: NameMode = read();

export function setNameMode(next: NameMode) {
  mode = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* private window: keep it for this page only */
  }
  listeners.forEach((l) => l());
}

export function useNameMode(): NameMode {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => mode,
  );
}

type Named = { name: string; champion?: string | undefined };

/** The name to show in the chosen mode. */
export function useBotName() {
  const m = useNameMode();
  return (b: Named): string => (m === "champion" && b.champion ? b.champion : b.name);
}

/** The other name, for an "aka" line (undefined when there is none). */
export function useOtherName() {
  const m = useNameMode();
  return (b: Named): string | undefined => (b.champion ? (m === "champion" ? b.name : b.champion) : undefined);
}
