/**
 * Tiers v2, defense d: which markdown images the console loads by itself.
 *
 * A markdown image is fetched as soon as an answer is shown, so an image URL carrying private
 * data would leak without a click. An image loads only from the console itself or from a host on
 * the allowlist (KESTREL_IMAGE_ALLOWLIST, empty by default; a host covers its subdomains). Every
 * other image is shown as a plain link with its full URL. The backend's CSP header
 * (img-src 'self' + the allowlist) enforces the same rule in the browser, as a backstop.
 *
 * Kept free of React and the DOM so `npm test` can run it under plain Node.
 */
export function imageLoads(src: string, allowlist: readonly string[], origin: string): boolean {
  if (!src.trim()) return false;
  let url: URL;
  try {
    url = new URL(src, origin);
  } catch {
    return false;
  }
  if (url.origin === origin) return true;
  if (url.protocol !== "https:" && url.protocol !== "http:") return false; // data:, blob:, ...: the CSP blocks them too
  const host = url.hostname.toLowerCase();
  return allowlist.some((h) => host === h || host.endsWith(`.${h}`));
}
