import { useEffect, useRef } from 'react'
import { useSwarmStore } from '../store/swarmStore'
import type { SwarmEvent } from '../types/swarm'
import { fromCloudEvent, isCloudEvent } from '../protocol/cloudevents'

const WS_BASE = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000'

export function useSwarmSocket(runId: string | null) {
  const dispatch = useSwarmStore((s) => s.dispatch)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!runId) return

    const ws = new WebSocket(`${WS_BASE}/ws/${runId}`)
    wsRef.current = ws

    ws.onmessage = (e) => {
      try {
        const parsed = JSON.parse(e.data)
        // Backend emits CloudEvents 1.0 envelopes; unwrap to the internal shape.
        // Fall back to treating the message as a raw SwarmEvent for resilience.
        const event: SwarmEvent = isCloudEvent(parsed)
          ? fromCloudEvent(parsed)
          : (parsed as SwarmEvent)
        dispatch(event)
      } catch {
        console.error('Failed to parse WS event:', e.data)
      }
    }

    ws.onerror = (err) => {
      console.error('WebSocket error:', err)
    }

    // Keep-alive ping every 25s
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send('ping')
      }
    }, 25_000)

    return () => {
      clearInterval(ping)
      ws.close()
      wsRef.current = null
    }
  }, [runId, dispatch])
}
