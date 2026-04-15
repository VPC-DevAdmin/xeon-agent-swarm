import { useSwarmStore } from '../store/swarmStore'
import { ResultCard } from './ResultCard'

export function SingleModelPanel() {
  const tokens = useSwarmStore((s) => s.singleTokens)
  const model = useSwarmStore((s) => s.singleModel)
  const hardware = useSwarmStore((s) => s.singleHardware)
  const completed = useSwarmStore((s) => s.singleCompleted)
  const latencyMs = useSwarmStore((s) => s.singleLatencyMs)
  const isRunning = useSwarmStore((s) => s.isRunning)
  const runId = useSwarmStore((s) => s.runId)
  const streaming = !!tokens && !completed

  return (
    <div className="flex flex-col gap-4 min-h-0">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-amber-400">Single Model</h2>
        <span className="text-xs text-gray-500">A/B baseline — no decomposition</span>
        {streaming && (
          <span className="ml-auto text-xs text-amber-400 animate-pulse">● streaming</span>
        )}
        {completed && (
          <span className="ml-auto text-xs text-green-400">● done</span>
        )}
      </div>

      {model && (
        <div className="text-xs text-gray-500">
          Model: <span className="text-gray-300">{model}</span>
          {hardware && (
            <span className={`ml-2 px-1 rounded border ${
              hardware === 'gpu'
                ? 'text-green-400 border-green-700'
                : 'text-gray-400 border-gray-700'
            }`}>
              {hardware.toUpperCase()}
            </span>
          )}
        </div>
      )}

      {/* Streaming output */}
      {tokens && !completed && (
        <div className="rounded-lg border border-amber-900 bg-gray-900 p-4">
          <div className="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto cursor-blink">
            {tokens}
          </div>
        </div>
      )}

      {/* Completed result */}
      {completed && tokens && (
        <ResultCard
          title="Single Model Answer"
          answer={tokens}
          latencyMs={latencyMs}
          model={model}
          hardware={hardware}
        />
      )}

      {/* Empty state */}
      {!tokens && !isRunning && !runId && (
        <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
          Single model output will appear here
        </div>
      )}
      {!tokens && (isRunning || !!runId) && !completed && (
        <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
          <span className="animate-pulse">Waiting for model response…</span>
        </div>
      )}
    </div>
  )
}
