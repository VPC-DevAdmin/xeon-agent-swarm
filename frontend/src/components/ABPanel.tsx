import { SwarmPanel } from './SwarmPanel'
import { SingleModelPanel } from './SingleModelPanel'
import { TimingBar } from './TimingBar'
import { useSwarmStore } from '../store/swarmStore'

export function ABPanel() {
  const query = useSwarmStore((s) => s.query)
  const swarmDone = useSwarmStore((s) => !!s.finalAnswer)
  const singleDone = useSwarmStore((s) => s.singleCompleted)

  return (
    <div className="w-full max-w-7xl mx-auto px-4 mt-6">
      {query && (
        <div className="mb-4 text-xs text-gray-500 border border-gray-800 rounded px-3 py-2">
          <span className="text-gray-400 font-semibold">Query: </span>
          {query}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-xl border border-blue-900/50 bg-gray-950 p-4">
          <SwarmPanel />
        </div>
        <div className="rounded-xl border border-amber-900/50 bg-gray-950 p-4">
          <SingleModelPanel />
        </div>
      </div>

      {(swarmDone || singleDone) && <TimingBar />}
    </div>
  )
}
