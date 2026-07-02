import { useSwarmStore } from '../store/swarmStore'

const TIER_BAR_COLORS: Record<string, string> = {
  T1: 'bg-emerald-500',
  T2: 'bg-teal-500',
  T3: 'bg-sky-500',
  T4: 'bg-orange-500',
  T5: 'bg-red-500',
  unknown: 'bg-gray-600',
}

const TIER_ORDER = ['T1', 'T2', 'T3', 'T4', 'T5', 'unknown']

/**
 * MetricsHUD — shown after a run completes.
 * The run report card: how the swarm decomposed, how the semantic router
 * distributed the work across tiers, and what validation cost on top.
 */
export function MetricsHUD() {
  const metrics = useSwarmStore((s) => s.runMetrics)
  const runCompleted = useSwarmStore((s) => s.runCompleted)

  if (!runCompleted || !metrics) return null

  const tierEntries = TIER_ORDER
    .filter((t) => (metrics.tier_calls?.[t] ?? 0) > 0)
    .map((t) => [t, metrics.tier_calls[t]] as const)
  const maxTierCalls = Math.max(1, ...tierEntries.map(([, n]) => n))

  const validationShare = metrics.total_tokens + metrics.validation_tokens > 0
    ? ((metrics.validation_tokens /
        (metrics.total_tokens + metrics.validation_tokens)) * 100).toFixed(0)
    : '0'

  return (
    <div className="mt-4 bg-gray-900 border border-gray-700 rounded-lg p-4 text-sm">
      <div className="flex items-center gap-2 mb-3">
        <span className="w-2 h-2 rounded-full bg-blue-400" />
        <span className="font-semibold text-gray-200 text-xs uppercase tracking-wider">
          Run report — agents · routing · validation
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
        <div className="flex justify-between">
          <span className="text-gray-500">Agents dispatched</span>
          <span className="text-gray-200 font-mono">{metrics.task_count}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Model calls</span>
          <span className="text-gray-200 font-mono">{metrics.call_count}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Router cache hits</span>
          <span className={`font-mono ${
            metrics.cached_calls > 0 ? 'text-blue-400' : 'text-gray-400'
          }`}>{metrics.cached_calls}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Generation tokens</span>
          <span className="text-gray-200 font-mono">
            {metrics.total_tokens.toLocaleString()}
          </span>
        </div>
      </div>

      {/* Routing distribution: which tiers the semantic router actually served */}
      {tierEntries.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-800">
          <div className="text-xs text-gray-500 mb-2">
            Tier distribution (router decisions)
          </div>
          <div className="space-y-1">
            {tierEntries.map(([tier, calls]) => (
              <div key={tier} className="flex items-center gap-2 text-xs">
                <span className="w-14 font-mono text-gray-400">{tier}</span>
                <div className="flex-1 h-2 bg-gray-800 rounded overflow-hidden">
                  <div
                    className={`h-full rounded ${TIER_BAR_COLORS[tier] ?? TIER_BAR_COLORS.unknown}`}
                    style={{ width: `${(calls / maxTierCalls) * 100}%` }}
                  />
                </div>
                <span className="w-20 text-right font-mono text-gray-400">
                  {calls} call{calls === 1 ? '' : 's'}
                </span>
                <span className="w-20 text-right font-mono text-gray-600">
                  {(metrics.tier_tokens_out?.[tier] ?? 0).toLocaleString()} tok
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {metrics.validation_tokens > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-800">
          <div className="flex justify-between text-xs">
            <span className="text-gray-500">Validator overhead</span>
            <span className="text-gray-400 font-mono">
              +{metrics.validation_tokens.toLocaleString()} tok ({validationShare}%)
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
