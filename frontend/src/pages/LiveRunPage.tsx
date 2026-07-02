import { useCallback } from 'react'
import { PromptComposer } from '../components/PromptComposer'
import { FlowCanvas } from '../components/FlowCanvas'
import { OutputPanel } from '../components/OutputPanel'
import { ApprovalModal } from '../components/ApprovalModal'
import { useSwarmSocket } from '../hooks/useSwarmSocket'
import { useSwarmStore } from '../store/swarmStore'

export function LiveRunPage() {
  const runId = useSwarmStore((s) => s.runId)
  const startRun = useSwarmStore((s) => s.startRun)
  const reset = useSwarmStore((s) => s.reset)

  useSwarmSocket(runId)

  const handleRunStart = useCallback(
    (newRunId: string, query: string) => {
      reset()
      startRun(newRunId, query)
    },
    [reset, startRun],
  )

  return (
    <div className="flex flex-col">
      <ApprovalModal />
      <div className="sticky top-12 z-10 bg-gray-950/95 backdrop-blur border-b border-gray-800 px-6 py-3">
        <PromptComposer onRunStart={handleRunStart} />
      </div>

      <div className="flex-none" style={{ height: '58vh', minHeight: 420 }}>
        {runId ? <FlowCanvas /> : <LandingHero />}
      </div>

      {runId && (
        <div className="flex-1 border-t border-gray-800 bg-gray-950">
          <OutputPanel />
        </div>
      )}
    </div>
  )
}

function LandingHero() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-6 px-8 text-center">
      <div>
        <h1 className="text-3xl font-bold text-white mb-2">What do you want done?</h1>
        <p className="text-gray-400 max-w-2xl text-sm leading-relaxed">
          Describe an objective — it is broken into tasks, each handled by a
          specialist agent, every output checked before the final answer is
          composed. Review the plan before it runs, or put it on a schedule.
        </p>
      </div>
      <div className="grid grid-cols-5 gap-4 max-w-3xl w-full text-left">
        {[
          { label: 'Plan', color: 'border-blue-700', icon: '🧭', hint: 'your prompt becomes tasks' },
          { label: 'Approve', color: 'border-amber-700', icon: '✅', hint: 'you review the breakdown' },
          { label: 'Delegate', color: 'border-purple-700', icon: '⚡', hint: 'one agent per task' },
          { label: 'Verify', color: 'border-cyan-700', icon: '🔍', hint: 'every output checked' },
          { label: 'Deliver', color: 'border-green-700', icon: '📋', hint: 'one composed answer' },
        ].map((step, i) => (
          <div key={step.label} className={`rounded-lg border ${step.color} bg-gray-900/50 p-3`}>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs text-gray-500">{i + 1}</span>
              <span className="text-base">{step.icon}</span>
            </div>
            <div className="text-xs font-semibold text-gray-300">{step.label}</div>
            <div className="text-[10px] text-gray-500 mt-0.5">{step.hint}</div>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-600">
        Enter a prompt above, or browse the sample library to see what decomposes well
      </p>
    </div>
  )
}
