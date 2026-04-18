import { useCallback } from 'react'
import { QueryInput } from './components/QueryInput'
import { FlowCanvas } from './components/FlowCanvas'
import { OutputPanel } from './components/OutputPanel'
import { useSwarmSocket } from './hooks/useSwarmSocket'
import { useSwarmStore } from './store/swarmStore'

const PRESET_QUERIES = [
  'How does vLLM improve LLM inference throughput on Intel Xeon?',
  'Compare transformer attention mechanisms across model families',
  'What are the key architectural innovations in modern LLM training?',
  'Explain PagedAttention and its impact on GPU memory efficiency',
]

export default function App() {
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
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* ── Query bar (sticky) ─────────────────────────────────────── */}
      <div className="sticky top-0 z-20 bg-gray-950/95 backdrop-blur border-b border-gray-800 px-6 py-3 flex items-center gap-4">
        <span className="text-xs font-semibold text-blue-400 uppercase tracking-widest shrink-0">
          Xeon Agent Swarm
        </span>
        <div className="flex-1">
          <QueryInput onRunStart={handleRunStart} presets={PRESET_QUERIES} />
        </div>
      </div>

      {/* ── Flow canvas (middle zone) ─────────────────────────────── */}
      <div className="flex-none" style={{ height: '62vh', minHeight: 420 }}>
        {runId ? (
          <FlowCanvas />
        ) : (
          <LandingHero />
        )}
      </div>

      {/* ── Output panel (bottom zone, grows) ────────────────────── */}
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
        <h1 className="text-3xl font-bold text-white mb-2">Intel Xeon Agent Swarm</h1>
        <p className="text-gray-400 max-w-2xl text-sm leading-relaxed">
          Parallel specialist agents produce a structured intelligence report in real time.
          Each stage of the pipeline is explained as it runs — watch the architecture operate.
        </p>
      </div>
      <div className="grid grid-cols-5 gap-4 max-w-3xl w-full text-left">
        {[
          { label: 'Query', color: 'border-gray-600', icon: '❓' },
          { label: 'Orchestrate', color: 'border-blue-700', icon: '🧭' },
          { label: 'Fan-out', color: 'border-purple-700', icon: '⚡' },
          { label: 'Fact-check', color: 'border-amber-700', icon: '🔍' },
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
      <p className="text-xs text-gray-600">Enter a query above to start the demo</p>
    </div>
  )
}
