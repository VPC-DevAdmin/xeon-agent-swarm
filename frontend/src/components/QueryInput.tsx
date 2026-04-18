import { useState } from 'react'
import { useSwarmStore } from '../store/swarmStore'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const DEFAULT_PRESETS = [
  'Compare the energy efficiency of nuclear, wind, and solar power generation, including current costs per MWh and carbon footprint.',
  'Explain the differences between transformer and LSTM architectures for NLP, with code examples of each.',
  'Analyze the economic impacts of remote work on urban real estate markets since 2020.',
]

interface Props {
  onRunStart: (runId: string, query: string) => void
  presets?: string[]
}

export function QueryInput({ onRunStart, presets }: Props) {
  const SAMPLE_QUERIES = presets ?? DEFAULT_PRESETS
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isRunning = useSwarmStore((s) => s.isRunning)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!query.trim() || loading) return
    setLoading(true)
    setError(null)
    try {
      const resp = await fetch(`${API_BASE}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim() }),
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = await resp.json()
      onRunStart(data.run_id, query.trim())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start run')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="w-full max-w-4xl mx-auto px-4">
      <div className="mb-2 text-center">
        <h1 className="text-3xl font-bold text-blue-400 tracking-tight">Xeon Agent Swarm</h1>
        <p className="text-gray-400 text-sm mt-1">
          Parallel specialist agents · typed artifact outputs · real-time pipeline visualization
        </p>
      </div>

      <form onSubmit={handleSubmit} className="mt-6">
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Enter a complex query to decompose and execute in parallel…"
          rows={3}
          disabled={loading || isRunning}
          className="w-full bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 resize-none disabled:opacity-50"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit(e)
          }}
        />
        <div className="flex items-center justify-between mt-3">
          <div className="flex gap-2 flex-wrap">
            {SAMPLE_QUERIES.map((q, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setQuery(q)}
                disabled={loading || isRunning}
                className="text-xs text-gray-500 hover:text-blue-400 border border-gray-800 hover:border-blue-800 rounded px-2 py-1 transition-colors disabled:opacity-40"
              >
                Sample {i + 1}
              </button>
            ))}
          </div>
          <button
            type="submit"
            disabled={!query.trim() || loading || isRunning}
            className="px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg font-medium transition-colors"
          >
            {loading ? 'Starting…' : isRunning ? 'Running…' : 'Run Swarm ⚡'}
          </button>
        </div>
        {error && (
          <p className="mt-2 text-red-400 text-sm">{error}</p>
        )}
      </form>
    </div>
  )
}
