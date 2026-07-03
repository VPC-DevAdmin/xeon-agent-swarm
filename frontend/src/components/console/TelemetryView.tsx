import { useEffect, useState } from 'react'
import { runsApi } from '../../api/client'
import type { Job, RunSummary } from '../../api/types'
import type { RunMetrics } from '../../types/swarm'
import { TIER_ORDER, tierColor, dayGroup } from '../../lib/thread'

interface Props {
  runs: RunSummary[]
  jobs: Job[]
}

interface RunTelemetry {
  id: string
  query: string
  metrics: RunMetrics
}

/**
 * TelemetryView — the operator's picture, from real data only:
 * live counts up top, then the routing distribution of recent runs
 * (which tiers the semantic router actually served, per run).
 */
export function TelemetryView({ runs, jobs }: Props) {
  const [recent, setRecent] = useState<RunTelemetry[]>([])
  const [loading, setLoading] = useState(true)

  const activeNow = runs.filter((r) => ['pending', 'running', 'awaiting_approval'].includes(r.status)).length
  const completedToday = runs.filter((r) => r.status === 'completed' && dayGroup(r.started_at) === 'Today').length
  const next24h = jobs.filter((j) => {
    if (j.status !== 'active' || !j.next_fire_at) return false
    const dt = new Date(j.next_fire_at).getTime() - Date.now()
    return dt >= 0 && dt <= 86_400_000
  }).length

  useEffect(() => {
    let cancelled = false
    const ids = runs.filter((r) => r.status === 'completed').slice(0, 12).map((r) => r.id)
    Promise.all(ids.map((id) => runsApi.get(id).catch(() => null)))
      .then((details) => {
        if (cancelled) return
        const rows: RunTelemetry[] = []
        for (const d of details) {
          const m = (d?.metrics ?? null) as RunMetrics | null
          if (d && m?.tier_calls && Object.keys(m.tier_calls).length) {
            rows.push({ id: d.run_id, query: d.query, metrics: m })
          }
        }
        setRecent(rows)
        setLoading(false)
      })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs.map((r) => r.id + r.status).join(',')])

  const totals: Record<string, number> = {}
  let grandTotal = 0
  for (const r of recent) {
    for (const t of TIER_ORDER) {
      const n = r.metrics.tier_calls?.[t] ?? 0
      totals[t] = (totals[t] ?? 0) + n
      grandTotal += n
    }
  }

  return (
    <div className="max-w-[760px] mx-auto pb-8">
      {/* stat cards */}
      <div className="flex gap-3 flex-wrap mb-4">
        <Stat label="Active now" value={String(activeNow)} live={activeNow > 0} />
        <Stat label="Completed today" value={String(completedToday)} />
        <Stat label="Scheduled next 24h" value={String(next24h)} suffix="runs" />
        <Stat label="Agents in recent runs" value={String(recent.reduce((a, r) => a + (r.metrics.task_count ?? 0), 0))} />
      </div>

      {/* aggregate tier mix */}
      <div className="console-panel p-4 mb-4">
        <h3 className="font-display font-semibold text-[15px] m-0">Routing distribution</h3>
        <p className="text-[12.5px] text-[var(--muted)] mt-0.5 mb-3">
          Which tiers the semantic router actually served across the last {recent.length} completed runs.
        </p>
        {grandTotal > 0 ? (
          <>
            <div className="flex h-2.5 rounded-md overflow-hidden" style={{ background: 'var(--line)' }}>
              {TIER_ORDER.filter((t) => totals[t]).map((t) => (
                <div key={t} title={`${t}: ${totals[t]} calls`}
                  style={{ width: `${(totals[t] / grandTotal) * 100}%`, background: tierColor(t) }} />
              ))}
            </div>
            <div className="flex gap-4 flex-wrap mt-3 pt-3 border-t" style={{ borderColor: 'var(--line-soft)' }}>
              {TIER_ORDER.filter((t) => totals[t]).map((t) => (
                <div key={t} className="flex items-center gap-1.5 font-code text-[12px] text-[var(--muted)]">
                  <i className="w-2.5 h-2.5 rounded-[3px]" style={{ background: tierColor(t) }} />
                  {t} · {totals[t]} ({Math.round((totals[t] / grandTotal) * 100)}%)
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="text-[13px] text-[var(--faint)]">{loading ? 'Loading…' : 'No completed runs with telemetry yet.'}</p>
        )}
      </div>

      {/* per-run bars */}
      {recent.length > 0 && (
        <div className="console-panel p-4">
          <h3 className="font-display font-semibold text-[15px] m-0 mb-3">Recent runs</h3>
          <div className="flex flex-col gap-2.5">
            {recent.map((r) => {
              const total = TIER_ORDER.reduce((a, t) => a + (r.metrics.tier_calls?.[t] ?? 0), 0) || 1
              return (
                <div key={r.id} className="flex items-center gap-3">
                  <span className="w-[46%] truncate text-[12.5px] text-[var(--muted)]" title={r.query}>
                    {r.query}
                  </span>
                  <span className="flex h-2 flex-1 rounded overflow-hidden" style={{ background: 'var(--line)' }}>
                    {TIER_ORDER.filter((t) => r.metrics.tier_calls?.[t]).map((t) => (
                      <span key={t} title={`${t}: ${r.metrics.tier_calls[t]}`}
                        style={{ width: `${((r.metrics.tier_calls?.[t] ?? 0) / total) * 100}%`, background: tierColor(t) }} />
                    ))}
                  </span>
                  <span className="w-16 text-right font-code text-[11px] text-[var(--faint)]">
                    {r.metrics.task_count} agents
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, suffix, live }: { label: string; value: string; suffix?: string; live?: boolean }) {
  return (
    <div className="console-panel flex-1 min-w-[150px] px-4 py-3.5">
      <div className="flex items-center gap-2 text-[12px] text-[var(--muted)]">
        {live && <span className="w-1.5 h-1.5 rounded-full anim-dot-pulse" style={{ background: 'var(--accent)' }} />}
        {label}
      </div>
      <div className="font-display font-semibold text-[24px] tracking-[-0.02em] mt-1">
        {value} {suffix && <small className="text-[13px] font-body font-medium text-[var(--faint)]">{suffix}</small>}
      </div>
    </div>
  )
}
