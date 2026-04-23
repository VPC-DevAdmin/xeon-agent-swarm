import { useSwarmStore } from '../store/swarmStore'

/**
 * MetricsHUD — shown after a run completes when the validator was enabled.
 * Displays key quality metrics: retries triggered, validation pass rate, token cost.
 */
export function MetricsHUD() {
  const metrics = useSwarmStore((s) => s.runMetrics)
  const runCompleted = useSwarmStore((s) => s.runCompleted)
  const validatorEnabled = useSwarmStore((s) => s.validatorEnabled)

  if (!runCompleted || !metrics || !validatorEnabled) return null

  const retryRate = metrics.total_tasks > 0
    ? ((metrics.total_retries / metrics.total_tasks) * 100).toFixed(0)
    : '0'

  const passRate = metrics.validations_run > 0
    ? ((metrics.validations_passed / metrics.validations_run) * 100).toFixed(0)
    : '—'

  const totalTokens = metrics.total_tokens_in + metrics.total_tokens_out + metrics.total_tokens_validator

  const wallClock = metrics.wall_clock_ms > 0
    ? metrics.wall_clock_ms > 60_000
      ? `${(metrics.wall_clock_ms / 60_000).toFixed(1)}m`
      : `${(metrics.wall_clock_ms / 1000).toFixed(1)}s`
    : '—'

  return (
    <div className="mt-4 bg-gray-900 border border-gray-700 rounded-lg p-4 text-sm">
      <div className="flex items-center gap-2 mb-3">
        <span className="w-2 h-2 rounded-full bg-blue-400" />
        <span className="font-semibold text-gray-200 text-xs uppercase tracking-wider">
          Contract Enforcement Metrics
        </span>
        <span className="ml-auto text-xs text-gray-500">{wallClock}</span>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
        <div className="flex justify-between">
          <span className="text-gray-500">Workers</span>
          <span className="text-gray-200 font-mono">{metrics.total_tasks}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Total attempts</span>
          <span className="text-gray-200 font-mono">{metrics.total_attempts}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Validations run</span>
          <span className="text-gray-200 font-mono">{metrics.validations_run}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Pass rate</span>
          <span className={`font-mono ${
            metrics.validations_passed === metrics.validations_run
              ? 'text-green-400'
              : 'text-amber-400'
          }`}>{passRate}%</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Retries triggered</span>
          <span className={`font-mono ${
            metrics.total_retries > 0 ? 'text-orange-400' : 'text-gray-400'
          }`}>{metrics.total_retries} ({retryRate}%)</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Committed w/ warnings</span>
          <span className={`font-mono ${
            metrics.workers_rejected_committed > 0 ? 'text-red-400' : 'text-gray-400'
          }`}>{metrics.workers_rejected_committed}</span>
        </div>
      </div>

      {totalTokens > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-800">
          <div className="flex justify-between text-xs">
            <span className="text-gray-500">Total tokens (incl. validator)</span>
            <span className="text-gray-300 font-mono">{totalTokens.toLocaleString()}</span>
          </div>
          {metrics.total_tokens_validator > 0 && (
            <div className="flex justify-between text-xs mt-1">
              <span className="text-gray-600">  └ validator overhead</span>
              <span className="text-gray-500 font-mono">
                +{metrics.total_tokens_validator.toLocaleString()} (
                {((metrics.total_tokens_validator / totalTokens) * 100).toFixed(0)}%)
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
