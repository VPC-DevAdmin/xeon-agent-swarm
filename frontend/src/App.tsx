import { useCallback } from 'react'
import { QueryInput } from './components/QueryInput'
import { ABPanel } from './components/ABPanel'
import { useSwarmSocket } from './hooks/useSwarmSocket'
import { useSwarmStore } from './store/swarmStore'

export default function App() {
  const runId = useSwarmStore((s) => s.runId)
  const startRun = useSwarmStore((s) => s.startRun)
  const reset = useSwarmStore((s) => s.reset)

  // Connect WebSocket whenever runId is set
  useSwarmSocket(runId)

  const handleRunStart = useCallback(
    (newRunId: string, query: string) => {
      reset()
      startRun(newRunId, query)
    },
    [reset, startRun],
  )

  return (
    <div className="min-h-screen bg-gray-950 py-8">
      <QueryInput onRunStart={handleRunStart} />
      {runId && <ABPanel />}
    </div>
  )
}
