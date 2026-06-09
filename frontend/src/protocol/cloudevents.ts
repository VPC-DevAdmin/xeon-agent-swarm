import type { SwarmEvent, EventType } from '../types/swarm'

/**
 * CloudEvents 1.0 structured-mode envelope as emitted by the backend.
 * See docs/standards.md §2.2.
 */
export interface CloudEvent {
  specversion: '1.0'
  type: string
  source: string
  id: string
  time: string
  subject?: string | null
  datacontenttype?: string
  data: {
    _event: string // short internal event name (e.g. "task_completed")
    run_id: string
    [key: string]: unknown
  }
}

/**
 * Convert a CloudEvents envelope back to the internal SwarmEvent shape the
 * store's dispatch switch understands.
 *
 * The backend stamps the short event name into `data._event`, so we use that
 * directly rather than reverse-mapping the reverse-DNS `type`. The remaining
 * data fields (minus _event and run_id) become the legacy `payload`.
 */
export function fromCloudEvent(ce: CloudEvent): SwarmEvent {
  const { _event, run_id, ...payload } = ce.data
  return {
    event: _event as EventType,
    run_id,
    payload: payload as Record<string, unknown>,
    timestamp: ce.time,
  }
}

/** Type guard: is this parsed JSON a CloudEvents envelope? */
export function isCloudEvent(value: unknown): value is CloudEvent {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as { specversion?: unknown }).specversion === '1.0' &&
    typeof (value as { data?: unknown }).data === 'object'
  )
}
