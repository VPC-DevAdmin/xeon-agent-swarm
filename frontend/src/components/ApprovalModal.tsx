import { useSwarmStore } from '../store/swarmStore'

/**
 * ApprovalModal — the HITL plan-approval gate.
 *
 * When ADL_PLAN_APPROVAL is enabled server-side, the run pauses after planning
 * and emits `awaiting_approval` with the proposed plan. This modal surfaces it:
 * Approve resumes the run, Reject aborts it (POST /run/{id}/approve).
 */
export function ApprovalModal() {
  const awaitingApproval = useSwarmStore((s) => s.awaitingApproval)
  const interrupt = useSwarmStore((s) => s.approvalInterrupt)
  const approveRun = useSwarmStore((s) => s.approveRun)

  if (!awaitingApproval) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-2xl mx-4 rounded-lg border border-amber-600 bg-gray-900 shadow-2xl">
        <div className="flex items-center gap-2 px-5 py-3 border-b border-gray-800">
          <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
          <span className="font-semibold text-amber-300 text-sm uppercase tracking-wider">
            Plan approval required
          </span>
        </div>

        <div className="px-5 py-4">
          <p className="text-sm text-gray-400 mb-3">
            The orchestrator has planned this run and is paused awaiting your
            decision. Approve to dispatch the agents, or reject to abort the run.
          </p>
          <div className="text-xs text-gray-300 bg-gray-950 rounded p-3 max-h-64 overflow-y-auto whitespace-pre-wrap font-mono">
            {interrupt || '(no plan payload)'}
          </div>
        </div>

        <div className="flex justify-end gap-3 px-5 py-3 border-t border-gray-800">
          <button
            onClick={() => approveRun('reject')}
            className="px-4 py-1.5 text-sm rounded border border-red-700 text-red-300 hover:bg-red-900/40 transition-colors"
          >
            Reject &amp; abort
          </button>
          <button
            onClick={() => approveRun('approve')}
            className="px-4 py-1.5 text-sm rounded bg-green-700 hover:bg-green-600 text-white transition-colors"
          >
            Approve plan
          </button>
        </div>
      </div>
    </div>
  )
}
