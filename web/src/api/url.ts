/** Fixed loopback origin used by the bundled Core sidecar. */
const PACKAGED_CORE_ORIGIN = 'http://127.0.0.1:8787';

interface FrontendLocation {
  protocol: string;
  hostname: string;
}

function currentLocation(): FrontendLocation | null {
  return typeof window === 'undefined' ? null : window.location;
}

function isPackagedTauri(location: FrontendLocation | null): boolean {
  return location?.protocol === 'tauri:' || location?.hostname === 'tauri.localhost';
}

/**
 * Resolve one Core API path for the current frontend build.
 *
 * Development and Core-served browser builds keep relative paths. A packaged
 * Tauri WebView has neither the Core origin nor Vite's proxy, so it must target
 * the bundled Core sidecar explicitly.
 */
export function resolveApiUrl(
  path: string,
  isDevelopment = import.meta.env.DEV,
  location: FrontendLocation | null = currentLocation(),
): string {
  return !isDevelopment && isPackagedTauri(location)
    ? `${PACKAGED_CORE_ORIGIN}${path}`
    : path;
}

export type ApiUrlResolver = (path: string) => string;
