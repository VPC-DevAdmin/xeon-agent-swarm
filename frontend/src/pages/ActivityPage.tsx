import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { approveRun, jobsApi, runsApi } from '../api/client'
import type { Job, RunSummary } from '../api/types'
import { Card, Empty, StatusBadge, timeAgo } from '../components/ui'

type Tab = 'running' | 'scheduled' | 'history'

const TABS: { id: Tab; label: string }[] = [
  { id: 'running', label: 'Active' },
  { id: 'scheduled', label: 'Scheduled' },
  { id: 'history', label: 'History' },
]

const ACTIVE_STATUSES = ['pending', 'running', 'awaiting_approval']

/** Humanize the cron presets the composer writes (fall back to the raw cron). */
function cadenceLabel(cron?: string | null): string {
  if (!cron) return 'on demand'
  const known: Record<string, string> = {
    '*/15 * * * *': 'every 15 min',
    '0 * * * *': 'hourly',
    '0 9 * * *': 'daily 9:00',
    '0 9 * * 1-5': 'weekdays 9:00',
    '0 9 * * 1': 'weekly Mon 9:00',
  }
  return known[cron] ?? cron
}

/**
 * ActivityPage — everything in flight and everything scheduled, in one place.
 * Active: live runs (including ones paused for plan approval, approvable inline).
 * Scheduled: recurring tasks with cadence, next fire, pause/resume/run-now.
 * History: the full durable run list.
 */
export function ActivityPage() {
  const [params, setParams] = useSearchParams()
  const tab = (params.get('tab') as Tab) || 'running'
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)

  async function refresh() {
    try {
      const [allRuns, allJobs] = await Promise.all([
        runsApi.list({ limit: 200 }),
        jobsApi.list(),
      ])
      setRuns(allRuns)
      setJobs(allJobs.filter((j) => j.status !== 'archived'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, tab === 'running' ? 3000 : 8000)
    return () => clearInterval(t)
  }, [tab])

  const active = useMemo(
    () => runs.filter((r) => ACTIVE_STATUSES.includes(r.status)),
    [runs],
  )
  const scheduled = useMemo(() => jobs.filter((j) => j.schedule_cron), [jobs])

  const counts: Record<Tab, number> = {
    running: active.length,
    scheduled: scheduled.length,
    history: runs.length,
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-6">
      <div className="flex items-center gap-1 mb-5">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setParams({ tab: t.id })}
            className={`px-4 py-1.5 rounded-full text-sm transition-colors ${
              tab === t.id
                ? 'bg-gray-800 text-white'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {t.label}
            <span className="ml-1.5 text-xs text-gray-500">{counts[t.id]}</span>
          </button>
        ))}
      </div>

      {loading ? (
        <Empty message="Loading…" />
      ) : tab === 'running' ? (
        <ActiveRuns runs={active} onChanged={refresh} />
      ) : tab === 'scheduled' ? (
        <ScheduledJobs jobs={scheduled} runs={runs} onChanged={refresh} />
      ) : (
        <RunHistory runs={runs} />
      )}
    </div>
  )
}

function ActiveRuns({ runs, onChanged }: { runs: RunSummary[]; onChanged: () => void }) {
  const navigate = useNavigate()
  if (runs.length === 0) {
    return <Empty message="Nothing running right now. Start a prompt from the New Task page." />
  }
  return (
    <div className="space-y-2">
      {runs.map((r) => (
        <Card key={r.id} className="p-3">
          <div className="flex items-center gap-3">
            <StatusBadge status={r.status} />
            <button
              onClick={() => navigate(`/runs/${r.id}`)}
              className="text-sm text-gray-200 truncate flex-1 text-left hover:text-blue-300 transition-colors"
            >
              {r.query}
            </button>
            <span className="text-[11px] text-gray-600 shrink-0">
              started {timeAgo(r.started_at)}
            </span>
            {r.status === 'awaiting_approval' && (
              <div className="flex gap-2 shrink-0">
                <button
                  onClick={() => navigate(`/runs/${r.id}`)}
                  className="text-xs px-3 py-1 rounded bg-amber-700 hover:bg-amber-600 text-white transition-colors"
                >
                  Review plan
                </button>
                <button
                  onClick={() => approveRun(r.id, 'approve').then(onChanged)}
                  className="text-xs px-3 py-1 rounded border border-green-700 text-green-300 hover:bg-green-900/40 transition-colors"
                >
                  Approve
                </button>
              </div>
            )}
            {r.status === 'running' && (
              <button
                onClick={() => runsApi.kill(r.id).then(onChanged)}
                className="text-xs px-3 py-1 rounded border border-red-800 text-red-400 hover:bg-red-900/30 transition-colors shrink-0"
              >
                Stop
              </button>
            )}
          </div>
        </Card>
      ))}
    </div>
  )
}

function ScheduledJobs({ jobs, runs, onChanged }:
  { jobs: Job[]; runs: RunSummary[]; onChanged: () => void }) {
  const navigate = useNavigate()
  if (jobs.length === 0) {
    return <Empty message="No scheduled tasks. Pick a recurring interval when submitting a prompt." />
  }
  const lastRunOf = (j: Job) => runs.find((r) => r.id === j.last_run_id)
  return (
    <div className="space-y-2">
      {jobs.map((j) => {
        const last = lastRunOf(j)
        return (
          <Card key={j.id} className="p-3">
            <div className="flex items-center gap-3">
              <span className={`w-2 h-2 rounded-full shrink-0 ${
                j.status === 'active' ? 'bg-green-400' : 'bg-gray-500'
              }`} />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-200 truncate">{j.name}</p>
                <p className="text-[11px] text-gray-500 mt-0.5">
                  {cadenceLabel(j.schedule_cron)}
                  {j.next_fire_at && j.status === 'active' && (
                    <> · next {new Date(j.next_fire_at).toLocaleString()}</>
                  )}
                  {last && (
                    <>
                      {' · last run '}
                      <button
                        onClick={() => navigate(`/runs/${last.id}`)}
                        className="text-blue-400 hover:text-blue-300"
                      >
                        {last.status}
                      </button>
                    </>
                  )}
                </p>
              </div>
              <div className="flex gap-2 shrink-0">
                <button
                  onClick={() => jobsApi.runNow(j.id).then(onChanged)}
                  className="text-xs px-3 py-1 rounded border border-blue-800 text-blue-300 hover:bg-blue-900/30 transition-colors"
                >
                  Run now
                </button>
                {j.status === 'active' ? (
                  <button
                    onClick={() => jobsApi.pause(j.id).then(onChanged)}
                    className="text-xs px-3 py-1 rounded border border-gray-700 text-gray-400 hover:bg-gray-800 transition-colors"
                  >
                    Pause
                  </button>
                ) : (
                  <button
                    onClick={() => jobsApi.resume(j.id).then(onChanged)}
                    className="text-xs px-3 py-1 rounded border border-green-800 text-green-300 hover:bg-green-900/30 transition-colors"
                  >
                    Resume
                  </button>
                )}
                <button
                  onClick={() => jobsApi.archive(j.id).then(onChanged)}
                  className="text-xs px-3 py-1 rounded border border-gray-800 text-gray-500 hover:text-red-400 hover:border-red-900 transition-colors"
                >
                  Remove
                </button>
              </div>
            </div>
          </Card>
        )
      })}
    </div>
  )
}

function RunHistory({ runs }: { runs: RunSummary[] }) {
  const navigate = useNavigate()
  if (runs.length === 0) return <Empty message="No runs yet." />
  return (
    <div className="space-y-1.5">
      {runs.map((r) => (
        <button key={r.id} onClick={() => navigate(`/runs/${r.id}`)} className="w-full text-left">
          <Card className="p-3 hover:border-gray-700 transition-colors">
            <div className="flex items-center gap-3">
              <StatusBadge status={r.status} />
              <span className="text-xs text-gray-300 truncate flex-1">{r.query}</span>
              <span className="text-[11px] text-gray-600 font-mono shrink-0">
                {r.trigger === 'schedule' ? '🕘 scheduled' : r.trigger}
              </span>
              <span className="text-[11px] text-gray-600 shrink-0">{timeAgo(r.started_at)}</span>
            </div>
          </Card>
        </button>
      ))}
    </div>
  )
}
