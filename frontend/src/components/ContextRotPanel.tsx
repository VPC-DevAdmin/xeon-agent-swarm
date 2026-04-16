import clsx from 'clsx'
import { useSwarmStore } from '../store/swarmStore'

// ── Context rot funnel ────────────────────────────────────────────────────────

function FunnelBar({
  label,
  value,
  max,
  color,
}: {
  label: string
  value: number
  max: number
  color: string
}) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0

  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs text-gray-400">
        <span>{label}</span>
        <span className="font-mono text-gray-200">{value}</span>
      </div>
      <div className="h-2 rounded-full bg-gray-800 overflow-hidden">
        <div
          className={clsx('h-full rounded-full transition-all duration-700', color)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function RotScoreBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100)
  const label = pct >= 70 ? 'High' : pct >= 40 ? 'Medium' : 'Low'
  const color =
    pct >= 70
      ? 'bg-red-900/50 border-red-700 text-red-300'
      : pct >= 40
      ? 'bg-amber-900/50 border-amber-700 text-amber-300'
      : 'bg-green-900/50 border-green-700 text-green-300'

  return (
    <div className={clsx('flex flex-col items-center px-4 py-3 rounded-lg border', color)}>
      <span className="text-2xl font-bold font-mono">{pct}%</span>
      <span className="text-xs mt-0.5">context wasted ({label})</span>
    </div>
  )
}

function ContextRotMeter() {
  const retrieved = useSwarmStore((s) => s.singleChunksRetrieved)
  const included = useSwarmStore((s) => s.singleChunksIncluded)
  const cited = useSwarmStore((s) => s.singleChunksCited)
  const tokenEstimate = useSwarmStore((s) => s.singleTokenEstimate)
  const rotScore = useSwarmStore((s) => s.singleRotScore)
  const completed = useSwarmStore((s) => s.singleCompleted)

  if (!retrieved && !included) return null

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
        Context Rot Analysis
      </h3>

      <div className="flex flex-col gap-3 mb-4">
        <FunnelBar
          label="Chunks retrieved from corpus"
          value={retrieved}
          max={retrieved}
          color="bg-blue-500"
        />
        <FunnelBar
          label="Chunks packed into context"
          value={included}
          max={retrieved}
          color="bg-amber-500"
        />
        {completed && (
          <FunnelBar
            label="Chunks actually cited in answer"
            value={cited}
            max={retrieved}
            color="bg-green-500"
          />
        )}
      </div>

      {tokenEstimate > 0 && (
        <div className="text-xs text-gray-500 mb-3">
          Context window: ~<span className="text-gray-300 font-mono">{tokenEstimate.toLocaleString()}</span> tokens
        </div>
      )}

      {rotScore != null && completed && (
        <div className="flex items-center gap-4">
          <RotScoreBadge score={rotScore} />
          <div className="flex-1 text-xs text-gray-400 leading-relaxed">
            The model received <span className="text-gray-200">{included}</span> context chunks
            but only referenced <span className="text-gray-200">{cited}</span> in its answer —{' '}
            <span className="text-red-300 font-medium">
              {included - cited} chunk{included - cited !== 1 ? 's' : ''} ({Math.round(rotScore * 100)}%) were wasted context
            </span>{' '}
            that cost tokens without improving the answer.
          </div>
        </div>
      )}

      {!completed && retrieved > 0 && (
        <div className="text-xs text-gray-500 italic">
          Citation analysis available after response completes…
        </div>
      )}
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────

export function ContextRotPanel() {
  const tokens = useSwarmStore((s) => s.singleTokens)
  const model = useSwarmStore((s) => s.singleModel)
  const hardware = useSwarmStore((s) => s.singleHardware)
  const completed = useSwarmStore((s) => s.singleCompleted)
  const latencyMs = useSwarmStore((s) => s.singleLatencyMs)
  const isRunning = useSwarmStore((s) => s.isRunning)
  const runId = useSwarmStore((s) => s.runId)
  const streaming = !!tokens && !completed

  const formatLatency = (ms: number) =>
    ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(1)}s`

  return (
    <div className="flex flex-col gap-4 min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-amber-400">Single Model A/B</h2>
        <span className="text-xs text-gray-500">no decomposition · context rot demo</span>
        {streaming && (
          <span className="ml-auto text-xs text-amber-400 animate-pulse">● streaming</span>
        )}
        {completed && (
          <span className="ml-auto text-xs text-green-400">
            ● done{latencyMs ? ` · ${formatLatency(latencyMs)}` : ''}
          </span>
        )}
      </div>

      {/* Model info */}
      {model && (
        <div className="text-xs text-gray-500 flex items-center gap-2">
          <span>Model: <span className="text-gray-300">{model.split('/').pop()}</span></span>
          {hardware && (
            <span
              className={clsx(
                'px-1 rounded border text-xs',
                hardware === 'gpu'
                  ? 'text-green-400 border-green-700'
                  : 'text-gray-400 border-gray-700',
              )}
            >
              {hardware.toUpperCase()}
            </span>
          )}
        </div>
      )}

      {/* Context rot meter */}
      <ContextRotMeter />

      {/* Streaming / completed answer */}
      {tokens && !completed && (
        <div className="rounded-lg border border-amber-900 bg-gray-900 p-4 flex-1">
          <div className="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">
            {tokens}
            <span className="inline-block w-0.5 h-4 bg-amber-400 ml-0.5 animate-pulse align-middle" />
          </div>
        </div>
      )}

      {completed && tokens && (
        <div className="rounded-lg border border-amber-700/50 bg-gray-900 p-4 flex-1">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold text-amber-400">Single Model Answer</h3>
            {latencyMs && (
              <span className="text-xs font-mono text-gray-500">{formatLatency(latencyMs)}</span>
            )}
          </div>
          <div className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">
            {tokens}
          </div>
        </div>
      )}

      {/* Empty states */}
      {!tokens && !isRunning && !runId && (
        <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
          Single model output will appear here
        </div>
      )}
      {!tokens && (isRunning || !!runId) && !completed && (
        <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
          <span className="animate-pulse">Waiting for single model response…</span>
        </div>
      )}
    </div>
  )
}
