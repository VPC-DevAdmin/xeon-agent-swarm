import { useSwarmStore } from '../store/swarmStore'

/** Split a numbered-list plan string into displayable task lines. */
export function planToTasks(plan: string | null): string[] {
  if (!plan) return []
  return plan
    .split(/\n+/)
    .map((line) => line.replace(/^\s*(?:\d+[.)]|[-*•])\s*/, '').trim())
    .filter((line) => line.length > 0)
}

/**
 * ApprovalModal — the plan-review gate.
 *
 * When "Review plan before running" is on, the orchestrator pauses after
 * decomposing the prompt and shows the proposed task breakdown here.
 * Approve dispatches the agents; Reject aborts the run.
 */
export function ApprovalModal() {
  const awaitingApproval = useSwarmStore((s) => s.awaitingApproval)
  const plan = useSwarmStore((s) => s.approvalPlan)
  const interrupt = useSwarmStore((s) => s.approvalInterrupt)
  const approveRun = useSwarmStore((s) => s.approveRun)

  if (!awaitingApproval) return null

  const tasks = planToTasks(plan)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-2xl mx-4 rounded-lg border border-amber-600 bg-gray-900 shadow-2xl">
        <div className="flex items-center gap-2 px-5 py-3 border-b border-gray-800">
          <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
          <span className="font-semibold text-amber-300 text-sm uppercase tracking-wider">
            Review the proposed plan
          </span>
        </div>

        <div className="px-5 py-4">
          <p className="text-sm text-gray-400 mb-3">
            Your prompt was broken down into the tasks below. Approve to dispatch
            an agent for each task, or reject to cancel the run.
          </p>
          {tasks.length > 0 ? (
            <ol className="space-y-2 max-h-72 overflow-y-auto">
              {tasks.map((t, i) => (
                <li
                  key={i}
                  className="flex items-start gap-3 rounded border border-gray-800 bg-gray-950 px-3 py-2"
                >
                  <span className="mt-0.5 flex-shrink-0 w-5 h-5 rounded-full bg-blue-900 text-blue-300 text-xs flex items-center justify-center font-mono">
                    {i + 1}
                  </span>
                  <span className="text-sm text-gray-200">{t}</span>
                </li>
              ))}
            </ol>
          ) : (
            <div className="text-xs text-gray-300 bg-gray-950 rounded p-3 max-h-64 overflow-y-auto whitespace-pre-wrap font-mono">
              {interrupt || '(no plan payload)'}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 px-5 py-3 border-t border-gray-800">
          <button
            onClick={() => approveRun('reject')}
            className="px-4 py-1.5 text-sm rounded border border-red-700 text-red-300 hover:bg-red-900/40 transition-colors"
          >
            Reject &amp; cancel
          </button>
          <button
            onClick={() => approveRun('approve')}
            className="px-4 py-1.5 text-sm rounded bg-green-700 hover:bg-green-600 text-white transition-colors"
          >
            Approve — run {tasks.length > 0 ? `${tasks.length} task${tasks.length === 1 ? '' : 's'}` : 'plan'}
          </button>
        </div>
      </div>
    </div>
  )
}
