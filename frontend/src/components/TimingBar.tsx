import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  LabelList,
} from 'recharts'
import { useSwarmStore } from '../store/swarmStore'

function formatMs(ms: number): string {
  return ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(1)}s`
}

export function TimingBar() {
  const swarmLatencyMs = useSwarmStore((s) => s.swarmLatencyMs)
  const singleLatencyMs = useSwarmStore((s) => s.singleLatencyMs)
  const taskResults = useSwarmStore((s) => s.taskResults)
  const taskGraph = useSwarmStore((s) => s.taskGraph)

  if (!swarmLatencyMs && !singleLatencyMs) return null

  const data = [
    ...(swarmLatencyMs != null
      ? [{ name: 'Swarm (parallel)', ms: swarmLatencyMs, color: '#3b82f6' }]
      : []),
    ...(singleLatencyMs != null
      ? [{ name: 'Single model', ms: singleLatencyMs, color: '#f59e0b' }]
      : []),
  ]

  const taskBreakdown = taskGraph?.tasks
    .map((t) => {
      const r = taskResults[t.id]
      return r ? { name: t.description.slice(0, 30) + '…', ms: r.latency_ms, type: t.type } : null
    })
    .filter(Boolean) as Array<{ name: string; ms: number; type: string }> | undefined

  const TYPE_COLORS: Record<string, string> = {
    research:      '#7c3aed',
    analysis:      '#d97706',
    code:          '#0d9488',
    summarization: '#2563eb',
    vision:        '#db2777',
    fact_check:    '#0891b2',
    writing:       '#16a34a',
    general:       '#6b7280',
  }

  return (
    <div className="w-full max-w-4xl mx-auto px-4 mt-6">
      <h3 className="text-sm font-semibold text-gray-400 mb-3">Latency Comparison</h3>

      {/* Main comparison bar */}
      <div className="h-24">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 120, right: 60 }}>
            <XAxis type="number" hide domain={[0, 'dataMax']} />
            <YAxis type="category" dataKey="name" tick={{ fill: '#9ca3af', fontSize: 12 }} width={110} />
            <Tooltip
              formatter={(val: number) => formatMs(val)}
              contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8 }}
              labelStyle={{ color: '#e5e7eb' }}
            />
            <Bar dataKey="ms" radius={[0, 4, 4, 0]}>
              {data.map((entry) => (
                <Cell key={entry.name} fill={entry.color} />
              ))}
              <LabelList
                dataKey="ms"
                position="right"
                formatter={(val: number) => formatMs(val)}
                style={{ fill: '#d1d5db', fontSize: 11 }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Per-task breakdown */}
      {taskBreakdown && taskBreakdown.length > 0 && (
        <>
          <h4 className="text-xs text-gray-500 mt-4 mb-2">Per-task breakdown (swarm)</h4>
          <div className="h-6 flex rounded overflow-hidden">
            {taskBreakdown.map((t, i) => {
              const total = taskBreakdown.reduce((s, x) => s + x.ms, 0)
              const pct = ((t.ms / total) * 100).toFixed(1)
              return (
                <div
                  key={i}
                  title={`${t.name}: ${formatMs(t.ms)}`}
                  className="flex items-center justify-center text-xs overflow-hidden"
                  style={{
                    width: `${pct}%`,
                    background: TYPE_COLORS[t.type] ?? '#6b7280',
                    opacity: 0.8,
                  }}
                >
                  {parseFloat(pct) > 8 && <span className="text-white truncate px-1">{formatMs(t.ms)}</span>}
                </div>
              )
            })}
          </div>
          <div className="flex flex-wrap gap-x-3 mt-1.5">
            {taskBreakdown.map((t, i) => (
              <span key={i} className="text-xs text-gray-500">
                <span
                  className="inline-block w-2 h-2 rounded-sm mr-1"
                  style={{ background: TYPE_COLORS[t.type] }}
                />
                {t.type}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
