// Where the API and WebSocket live.
//
// Dev (`make demo`): VITE_API_URL / VITE_WS_URL are set explicitly to the backend
// port, so the Vite dev server and the API can live on different ports.
//
// Production single-origin build: both are UNSET, so we fall back to the page's own
// origin. The backend serves the built SPA, so the UI, REST, and WS share one origin
// — which means a Cloudflare Tunnel / subdomain / any hostname works with NO rebuild,
// and there is no CORS or mixed-content (ws vs wss) problem.

const RAW_API = (import.meta.env.VITE_API_URL ?? '').trim()
const RAW_WS = (import.meta.env.VITE_WS_URL ?? '').trim()

function sameOriginHttp(): string {
  return typeof window !== 'undefined' ? window.location.origin : ''
}

function sameOriginWs(): string {
  if (typeof window === 'undefined') return ''
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}`
}

/** Base URL for REST calls ('' → same-origin relative paths still work). */
export const API_BASE = RAW_API || sameOriginHttp()

/** Base URL for WebSocket connections (wss when the page is https). */
export const WS_BASE = RAW_WS || sameOriginWs()
