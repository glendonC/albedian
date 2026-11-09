// No #! line: apps/web/vite.config.ts imports this file, and Vite's config bundler inlines it after code of its own.
/**
 * The guard on the web app's /api proxy (apps/web/vite.config.ts, as the proxy's `bypass`).
 *
 * Under `pnpm field --lan` the web app answers every device on the network, and its proxy forwards to the API whether
 * or not the client is a browser: a client that sends no Origin passes the API's origin check. Media discover lists
 * the clips in data/uploads with their file names, capture times and positions, so the guard refuses that route to
 * every client that is not this machine. Vite answers a refused request with 404 and never forwards it.
 *
 * - The client is the TCP peer address (req.socket.remoteAddress), never a header a client could write.
 * - A request from this machine (127.0.0.0/8, ::1 or ::ffff:127.x.x.x) always passes, so the laptop keeps the full
 *   experience and the default localhost-only run behaves as before. A request whose peer is unknown is treated as
 *   a network peer.
 * - For any other peer the path is read as the API would route it: decoded once, as uvicorn decodes it, with doubled
 *   slashes collapsed, dot segments resolved and letters lower-cased. Discover and every path below it, a trailing
 *   slash included, are refused. A path that does not decode is refused, since where it would go cannot be told.
 */
import path from 'node:path';

export const DISCOVER_PATH = '/api/media/discover';

/** True when the address is this machine's own loopback address. */
export function isLoopbackPeer(address) {
  if (typeof address !== 'string') return false;
  if (address === '::1') return true;
  const ipv4 = address.startsWith('::ffff:') ? address.slice('::ffff:'.length) : address;
  return /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(ipv4);
}

/** The request path as the API would route it, or null when it does not decode. */
function routedPath(url) {
  const raw = String(url ?? '').split(/[?#]/, 1)[0];
  let decoded;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return null;
  }
  return path.posix.normalize(decoded).toLowerCase();
}

/** Vite proxy bypass: false refuses the request (404, never forwarded); undefined lets the proxy forward it. */
export function refuseOnNetwork(req) {
  if (isLoopbackPeer(req?.socket?.remoteAddress)) return undefined;
  const routed = routedPath(req?.url);
  if (routed === null) return false;
  if (routed === DISCOVER_PATH || routed.startsWith(`${DISCOVER_PATH}/`)) return false;
  return undefined;
}
