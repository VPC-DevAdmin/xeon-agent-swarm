import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { runsApi } from '../api/client'
import type { RunDetail, RunSummary } from '../api/types'
import { Button, Card, Empty, StatusBadge, timeAgo } from '../components/ui'

export function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  async function refresh() {
    try {
      setRuns(await runsApi.list({ limit: 100 }))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="max-w-5xl mx-auto px-6 py-6">
      <h1 className="text-lg font-semibold text-white mb-4">Run History</h1>
      {loading ? (
        <Empty message="Loading…" />
      ) : runs.length === 0 ? (
        <Empty message="No runs yet." />
      ) : (
        <div className="space-y-1.5">
          {runs.map((r) => (
            <button key={r.id}
              onClick={() => navigate(`/runs/${r.id}`)}
              className="w-full text-left">
              <Card className="p-3 hover:border-gray-700 transition-colors">
                <div className="flex items-center gap-3">
                  <StatusBadge status={r.status} />
                  <span className="text-xs text-gray-300 truncate flex-1">{r.query}</span>
                  <span className="text-[11px] text-gray-600 font-mono shrink-0">{r.trigger}</span>
                  <span className="text-[11px] text-gray-600 shrink-0">{timeAgo(r.started_at)}</span>
                </div>
              </Card>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const [run, setRun] = useState<RunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    if (!runId) return
    try {
      setRun(await runsApi.get(runId))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    const active = run && ['pending', 'orchestrating', 'running', 'reducing'].includes(run.status)
    const t = setInterval(refresh, active ? 2000 : 8000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, run?.status])

  if (loading) return <Empty message="Loading…" />
  if (error) return <div className="max-w-4xl mx-auto px-6 py-6 text-red-400 text-sm">{error}</div>
  if (!run) return <Empty message="Run not found." />

  const evals = (run.metrics?.evals ?? null) as
    | { avg_score: number; pass_rate: number; steps_evaluated: number }
    | null

  return (
    <div className="max-w-4xl mx-auto px-6 py-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <StatusBadge status={run.status} />
            <span className="text-xs text-gray-500 font-mono">{run.run_id.slice(0, 8)}</span>
            <span className="text-[11px] text-gray-600">{run.trigger}</span>
          </div>
          <p className="text-sm text-gray-300 mt-1">{run.query}</p>
        </div>
        {['pending', 'orchestrating', 'running', 'reducing'].includes(run.status) && (
          <Button variant="danger" onClick={() => runsApi.kill(run.run_id).then(refresh)}>
            Kill run
          </Button>
        )}
      </div>

      {evals && (
        <Card className="p-3">
          <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">Quality eval</div>
          <div className="flex gap-6 text-xs">
            <Metric label="avg score" value={evals.avg_score.toFixed(2)}
              good={evals.avg_score >= 0.7} />
            <Metric label="pass rate" value={`${Math.round(evals.pass_rate * 100)}%`}
              good={evals.pass_rate >= 0.7} />
            <Metric label="steps" value={String(evals.steps_evaluated)} />
          </div>
        </Card>
      )}

      <div className="space-y-2">
        {run.steps.map((s) => (
          <Card key={s.step_key} className="p-3">
            <div className="flex items-center gap-2 mb-1">
              <StatusBadge status={s.status} />
              <span className="text-xs font-mono text-gray-400">{s.step_key}</span>
              <span className="text-[11px] text-blue-400">{s.type}</span>
              {s.deliverable_format && (
                <span className="text-[11px] text-gray-600 font-mono">{s.deliverable_format}</span>
              )}
              <span className="ml-auto text-[11px] text-gray-600">
                {s.total_attempts} attempt{s.total_attempts === 1 ? '' : 's'}
                {s.latency_ms ? ` · ${(s.latency_ms / 1000).toFixed(1)}s` : ''}
                {s.confidence != null ? ` · conf ${s.confidence.toFixed(2)}` : ''}
              </span>
            </div>
            {s.objective && <p className="text-xs text-gray-500">{s.objective}</p>}
            {s.dependencies.length > 0 && (
              <p className="text-[11px] text-gray-600 mt-1">deps: {s.dependencies.join(', ')}</p>
            )}
            {s.attempts.some((a) => a.correction_hint) && (
              <div className="mt-2 space-y-1">
                {s.attempts.filter((a) => a.correction_hint).map((a) => (
                  <p key={a.attempt_no} className="text-[11px] text-amber-400/80">
                    ↻ attempt {a.attempt_no}: {a.correction_hint}
                  </p>
                ))}
              </div>
            )}
          </Card>
        ))}
      </div>

      {run.langfuse_trace_id && (
        <p className="text-[11px] text-gray-600">
          Langfuse trace: <span className="font-mono">{run.langfuse_trace_id.slice(0, 16)}…</span>
        </p>
      )}
    </div>
  )
}

function Metric({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div>
      <div className="text-gray-600 text-[10px] uppercase">{label}</div>
      <div className={`font-mono ${good === undefined ? 'text-gray-300' : good ? 'text-green-400' : 'text-amber-400'}`}>
        {value}
      </div>
    </div>
  )
}
