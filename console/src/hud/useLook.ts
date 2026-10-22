// "classic" (default) or "hud". Remembered per browser; storage can be missing or blocked
// (private windows, blocked site data), so every access is wrapped and falls back to classic.

import { useEffect, useState } from "react";

export type Look = "classic" | "hud";
const KEY = "kestrel-look";

export function storageGet(key: string, storage: () => Storage = () => localStorage): string | null {
  try {
    return storage().getItem(key);
  } catch {
    return null;
  }
}

export function storageSet(key: string, value: string, storage: () => Storage = () => localStorage): void {
  try {
    storage().setItem(key, value);
  } catch {
    // not remembered; the page still works
  }
}

export function useLook(): [Look, (look: Look) => void] {
  const [look, setLook] = useState<Look>(() => (storageGet(KEY) === "hud" ? "hud" : "classic"));
  useEffect(() => {
    document.documentElement.dataset.look = look;
    storageSet(KEY, look);
  }, [look]);
  return [look, setLook];
}

export const prefersReducedMotion = () => {
  try {
    return matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
};

// The same, kept current if the user changes the setting while the page is open.
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion);
  useEffect(() => {
    let media: MediaQueryList;
    try {
      media = matchMedia("(prefers-reduced-motion: reduce)");
    } catch {
      return;
    }
    const onChange = () => setReduced(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return reduced;
}
