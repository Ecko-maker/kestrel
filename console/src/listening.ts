// The wake word's listening switch, remembered per browser. The server starts listening whenever it starts
// (KESTREL_WAKE=on), so a browser that turned it off says so again on load. Storage may be blocked: then
// nothing is remembered and the switch still works for the page.
const KEY = "kestrel-wake-listening";

export function readListening(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function rememberListening(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    // not remembered; the switch still works for this page
  }
}

// What to send on load: the remembered choice when it differs from the server's state, else nothing.
export function listeningToApply(stored: string | null, status: { available: boolean; enabled?: boolean }): boolean | null {
  if (!status.available || (stored !== "on" && stored !== "off")) return null;
  const want = stored === "on";
  return want === !!status.enabled ? null : want;
}
