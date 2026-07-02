import { useCallback } from 'react'
import { QueryInput } from '../components/QueryInput'
import { FlowCanvas } from '../components/FlowCanvas'
import { OutputPanel } from '../components/OutputPanel'
import { ApprovalModal } from '../components/ApprovalModal'
import { useSwarmSocket } from '../hooks/useSwarmSocket'
import { useSwarmStore } from '../store/swarmStore'

const PRESET_QUERIES = [
  'How does vLLM improve LLM inference throughput on Intel Xeon?',
  'Compare transformer attention mechanisms across model families',
  'What are the key architectural innovations in modern LLM training?',
  'Explain PagedAttention and its impact on GPU memory efficiency',
]

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
        <QueryInput onRunStart={handleRunStart} presets={PRESET_QUERIES} />
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
        <h1 className="text-3xl font-bold text-white mb-2">Agent Orchestrator</h1>
        <p className="text-gray-400 max-w-2xl text-sm leading-relaxed">
          Parallel specialist agents decompose a prompt and produce a structured,
          contract-validated result in real time. Watch the pipeline operate, or
          define a scheduled Job to run it automatically.
        </p>
      </div>
      <div className="grid grid-cols-5 gap-4 max-w-3xl w-full text-left">
        {[
          { label: 'Decompose', color: 'border-blue-700', icon: '🧭' },
          { label: 'Route', color: 'border-cyan-700', icon: '🚦' },
          { label: 'Delegate', color: 'border-purple-700', icon: '⚡' },
          { label: 'Validate', color: 'border-amber-700', icon: '🔍' },
          { label: 'Synthesize', color: 'border-green-700', icon: '📋' },
        ].map((step, i) => (
          <div key={step.label} className={`rounded-lg border ${step.color} bg-gray-900/50 p-3`}>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs text-gray-500">{i + 1}</span>
              <span className="text-base">{step.icon}</span>
            </div>
            <div className="text-xs font-semibold text-gray-300">{step.label}</div>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-600">Enter a query above to start an ad-hoc run</p>
    </div>
  )
}
