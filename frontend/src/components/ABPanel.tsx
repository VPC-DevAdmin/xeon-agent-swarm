import { DocumentViewer } from './DocumentViewer'
import { ContextRotPanel } from './ContextRotPanel'
import { TimingBar } from './TimingBar'
import { useSwarmStore } from '../store/swarmStore'

export function ABPanel() {
  const query = useSwarmStore((s) => s.query)
  const swarmDone = useSwarmStore((s) => !!s.finalAnswer)
  const singleDone = useSwarmStore((s) => s.singleCompleted)

  return (
    <div className="w-full max-w-screen-2xl mx-auto px-4 mt-6 pb-8">
      {/* Query echo */}
      {query && (
        <div className="mb-4 text-xs text-gray-500 border border-gray-800 rounded px-3 py-2">
          <span className="text-gray-400 font-semibold">Query: </span>
          {query}
        </div>
      )}

      {/* Two-panel layout: intelligence report (left, wider) + context rot (right) */}
      <div className="grid grid-cols-1 xl:grid-cols-[3fr_2fr] gap-5">
        {/* Left: intelligence report / live swarm */}
        <div className="rounded-xl border border-blue-900/50 bg-gray-950 p-5 min-w-0">
          <DocumentViewer />
        </div>

        {/* Right: single-model A/B with context rot */}
        <div className="rounded-xl border border-amber-900/50 bg-gray-950 p-5 min-w-0">
          <ContextRotPanel />
        </div>
      </div>

      {/* Latency comparison bar — appears once either side completes */}
      {(swarmDone || singleDone) && <TimingBar />}
    </div>
  )
}
