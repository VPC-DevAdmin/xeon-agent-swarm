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
    <div className="min-h-screen bg-gray-950">
      <div className="sticky top-0 z-10 bg-gray-950/90 backdrop-blur border-b border-gray-900 py-4">
        <QueryInput onRunStart={handleRunStart} />
      </div>
      {runId && <ABPanel />}
      {!runId && (
        <div className="flex flex-col items-center justify-center h-[70vh] gap-4 text-center px-4">
          <h1 className="text-2xl font-bold text-white">Xeon Agent Swarm</h1>
          <p className="text-gray-400 max-w-xl text-sm leading-relaxed">
            Submit a complex query to see parallel specialist agents produce a structured
            intelligence report — while a single large model attempts the same task, revealing
            context rot and token inefficiency.
          </p>
          <div className="grid grid-cols-3 gap-6 mt-4 max-w-2xl w-full">
            {[
              { icon: '📋', title: 'Intelligence Report', desc: 'Multi-section document with code, diagrams, citations & TTS audio' },
              { icon: '⚡', title: 'Parallel Workers', desc: 'Research, analysis, code, vision, fact-check — all running concurrently' },
              { icon: '📉', title: 'Context Rot Demo', desc: 'See how a single model wastes tokens on irrelevant context chunks' },
            ].map((card) => (
              <div key={card.title} className="rounded-lg border border-gray-800 bg-gray-900/50 p-4 text-left">
                <div className="text-2xl mb-2">{card.icon}</div>
                <div className="text-sm font-semibold text-gray-200 mb-1">{card.title}</div>
                <div className="text-xs text-gray-500 leading-relaxed">{card.desc}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
