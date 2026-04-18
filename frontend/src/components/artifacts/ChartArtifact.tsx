import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  Cell,
} from 'recharts'
import type { Artifact, ChartContent } from '../../types/swarm'

// Palette that works against the dark bg
const COLORS = ['#60a5fa', '#a78bfa', '#34d399', '#fb923c', '#f472b6', '#38bdf8']

interface NormalizedPoint { x: string | number; [seriesName: string]: string | number }

function normalizeData(series: ChartContent['series']): NormalizedPoint[] {
  // Recharts wants one object per x-value with each series as a key
  const byX = new Map<string | number, NormalizedPoint>()
  for (const s of series) {
    for (const pt of s.data) {
      const key = String(pt.x)
      if (!byX.has(key)) byX.set(key, { x: pt.x })
      byX.get(key)![s.name] = pt.y
    }
  }
  return Array.from(byX.values())
}

const tooltipStyle = {
  backgroundColor: '#111827',
  border: '1px solid #374151',
  borderRadius: 6,
  fontSize: 11,
  color: '#e5e7eb',
}

const axisStyle = { fontSize: 10, fill: '#6b7280' }

export function ChartArtifact({ artifact }: { artifact: Artifact }) {
  const c = artifact.content as unknown as ChartContent
  if (!c?.series?.length) return null

  const data = normalizeData(c.series)
  const seriesNames = c.series.map((s) => s.name)
  const isLine = c.chart_type === 'line'

  const commonProps = {
    data,
    margin: { top: 4, right: 16, left: 0, bottom: 4 },
  }

  return (
    <div className="rounded-lg border border-indigo-900/50 bg-gray-950 overflow-hidden">
      {c.caption && (
        <div className="px-4 py-2 border-b border-gray-800 text-xs text-indigo-300 font-semibold">
          📈 {c.caption}
        </div>
      )}

      <div className="p-4" style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          {isLine ? (
            <LineChart {...commonProps}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="x" tick={axisStyle} label={c.x_label ? { value: c.x_label, position: 'insideBottom', offset: -2, style: axisStyle } : undefined} />
              <YAxis tick={axisStyle} label={c.y_label ? { value: c.y_label, angle: -90, position: 'insideLeft', style: axisStyle } : undefined} />
              <Tooltip contentStyle={tooltipStyle} />
              <Legend wrapperStyle={{ fontSize: 10, color: '#9ca3af' }} />
              {seriesNames.map((name, i) => (
                <Line
                  key={name}
                  type="monotone"
                  dataKey={name}
                  stroke={COLORS[i % COLORS.length]}
                  strokeWidth={2}
                  dot={{ r: 3, fill: COLORS[i % COLORS.length] }}
                  activeDot={{ r: 5 }}
                />
              ))}
            </LineChart>
          ) : (
            <BarChart {...commonProps}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="x" tick={axisStyle} label={c.x_label ? { value: c.x_label, position: 'insideBottom', offset: -2, style: axisStyle } : undefined} />
              <YAxis tick={axisStyle} label={c.y_label ? { value: c.y_label, angle: -90, position: 'insideLeft', style: axisStyle } : undefined} />
              <Tooltip contentStyle={tooltipStyle} />
              {seriesNames.length > 1 && <Legend wrapperStyle={{ fontSize: 10, color: '#9ca3af' }} />}
              {seriesNames.map((name, i) => (
                <Bar key={name} dataKey={name} fill={COLORS[i % COLORS.length]} radius={[3, 3, 0, 0]} maxBarSize={40}>
                  {/* Single-series bar: colour each bar differently for visual variety */}
                  {seriesNames.length === 1 &&
                    data.map((_, j) => (
                      <Cell key={j} fill={COLORS[j % COLORS.length]} />
                    ))}
                </Bar>
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>

      <div className="px-3 py-1.5 text-[10px] text-gray-600 border-t border-gray-900">
        Produced by {artifact.worker_id} · conf {(artifact.confidence * 100).toFixed(0)}%
      </div>
    </div>
  )
}
