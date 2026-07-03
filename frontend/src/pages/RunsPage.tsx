import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { approveRun, runsApi } from '../api/client'
import type { RunDetail } from '../api/types'
import { planToTasks } from '../lib/thread'
import { Button, Card, Empty, StatusBadge } from '../components/ui'

const LIVE_STATUSES = ['pending', 'running', 'awaiting_approval']

const TIER_TEXT: Record<string, string> = {
  T1: 'text-emerald-400',
  T2: 'text-teal-400',
  T3: 'text-sky-400',
  T4: 'text-orange-400',
  T5: 'text-red-400',
}

const VERDICT_CHIP: Record<string, string> = {
  pass: 'bg-green-900/50 text-green-300 border-green-800',
  degraded: 'bg-amber-900/50 text-amber-300 border-amber-800',
  fail: 'bg-red-900/50 text-red-300 border-red-800',
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
    const active = run && LIVE_STATUSES.includes(run.status)
    const t = setInterval(refresh, active ? 2000 : 8000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, run?.status])

  if (loading) return <Empty message="Loading…" />
  if (error) return <div className="max-w-4xl mx-auto px-6 py-6 text-red-400 text-sm">{error}</div>
  if (!run) return <Empty message="Run not found." />

  const planTasks = planToTasks((run.task_graph?.plan as string) ?? null)
  const finalAnswer = (run.document?.final_answer as string) ?? null

  return (
    <div className="max-w-4xl mx-auto px-6 py-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <StatusBadge status={run.status} />
            <span className="text-[11px] text-gray-600">
              {run.trigger === 'schedule' ? 'scheduled run' : `${run.trigger} run`}
            </span>
          </div>
          <p className="text-sm text-gray-300 mt-1">{run.query}</p>
        </div>
        {['pending', 'running'].includes(run.status) && (
          <Button variant="danger" onClick={() => runsApi.kill(run.run_id).then(refresh)}>
            Stop run
          </Button>
        )}
      </div>

      {/* Plan approval banner — actionable from here, not just the live view */}
      {run.status === 'awaiting_approval' && (
        <Card className="p-4 border-amber-700">
          <div className="flex items-center gap-2 mb-2">
            <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
            <span className="text-xs font-semibold text-amber-300 uppercase tracking-wider">
              Waiting for your approval
            </span>
          </div>
          {planTasks.length > 0 && (
            <ol className="space-y-1.5 mb-3">
              {planTasks.map((t, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-gray-200">
                  <span className="mt-0.5 flex-shrink-0 w-5 h-5 rounded-full bg-blue-900 text-blue-300 text-xs flex items-center justify-center font-mono">
                    {i + 1}
                  </span>
                  {t}
                </li>
              ))}
            </ol>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => approveRun(run.run_id, 'approve').then(refresh)}
              className="px-4 py-1.5 text-sm rounded bg-green-700 hover:bg-green-600 text-white transition-colors"
            >
              Approve &amp; run
            </button>
            <button
              onClick={() => approveRun(run.run_id, 'reject').then(refresh)}
              className="px-4 py-1.5 text-sm rounded border border-red-700 text-red-300 hover:bg-red-900/40 transition-colors"
            >
              Reject
            </button>
          </div>
        </Card>
      )}

      {/* The approved plan, once past approval */}
      {run.status !== 'awaiting_approval' && planTasks.length > 0 && (
        <Card className="p-3">
          <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">Plan</div>
          <ol className="space-y-1">
            {planTasks.map((t, i) => (
              <li key={i} className="text-xs text-gray-400">
                <span className="text-gray-600 font-mono mr-1.5">{i + 1}.</span>{t}
              </li>
            ))}
          </ol>
        </Card>
      )}

      {/* Final answer */}
      {finalAnswer && (
        <Card className="p-4">
          <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">Result</div>
          <div className="text-sm text-gray-200 whitespace-pre-wrap">{finalAnswer}</div>
        </Card>
      )}

      {/* Per-agent breakdown */}
      <div className="space-y-2">
        {run.steps.filter((s) => s.step_key !== 'orchestrator').map((s) => (
          <Card key={s.step_key} className="p-3">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <StatusBadge status={s.status} />
              <span className="text-[11px] text-blue-400">{s.type} agent</span>
              {(() => {
                const tier = s.attempts.filter((a) => a.tier_observed).slice(-1)[0]?.tier_observed
                return tier ? (
                  <span className={`text-[11px] font-mono ${TIER_TEXT[tier] ?? 'text-gray-400'}`}>
                    {tier}
                  </span>
                ) : null
              })()}
              {(s.validations ?? []).slice(-1).map((v, i) => (
                <span
                  key={i}
                  className={`text-[10px] px-1.5 py-0.5 rounded-full border ${VERDICT_CHIP[v.verdict] ?? ''}`}
                >
                  {v.verdict === 'pass' ? '✓ verified' : v.verdict}
                </span>
              ))}
              <span className="ml-auto text-[11px] text-gray-600">
                {s.total_attempts} attempt{s.total_attempts === 1 ? '' : 's'}
                {s.latency_ms ? ` · ${(s.latency_ms / 1000).toFixed(1)}s` : ''}
              </span>
            </div>
            {s.objective && <p className="text-xs text-gray-500">{s.objective}</p>}
            {s.attempts.some((a) => a.correction_hint) && (
              <div className="mt-2 space-y-1">
                {s.attempts.filter((a) => a.correction_hint).map((a) => (
                  <p key={a.attempt_no} className="text-[11px] text-amber-400/80">
                    ↻ attempt {a.attempt_no}: {a.correction_hint}
                  </p>
                ))}
              </div>
            )}
            {typeof s.result?.text === 'string' && s.status === 'completed' && (
              <details className="mt-2">
                <summary className="text-[11px] text-gray-600 cursor-pointer hover:text-gray-400">
                  agent output
                </summary>
                <div className="mt-1 text-xs text-gray-400 bg-gray-950 rounded p-2 max-h-48 overflow-y-auto whitespace-pre-wrap">
                  {s.result.text as string}
                </div>
              </details>
            )}
          </Card>
        ))}
      </div>
    </div>
  )
}
