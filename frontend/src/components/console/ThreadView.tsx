import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import type { ThreadModel, ThreadTask, TaskState, ThreadPhase } from '../../lib/thread'
import { TIER_ORDER, tierColor } from '../../lib/thread'

interface Props {
  thread: ThreadModel
  onApprove?: (decision: 'approve' | 'reject') => void
}

/**
 * ThreadView — one run rendered as a conversation turn:
 * prompt bubble → phase rail → plan approval (when paused) → task flow
 * timeline → synthesis → answer (typewriter for live runs) → run metrics.
 */
export function ThreadView({ thread, onApprove }: Props) {
  return (
    <div className="max-w-[760px] mx-auto flex flex-col gap-5 pb-6">
      {/* user bubble */}
      <div className="flex justify-end">
        <div
          className="max-w-[82%] px-4 py-2.5 text-[14.5px] rounded-[15px] rounded-br-[5px] border"
          style={{
            background: 'linear-gradient(180deg,rgba(124,135,245,.16),rgba(124,135,245,.07))',
            borderColor: 'rgba(124,135,245,.32)',
          }}
        >
          {thread.prompt}
        </div>
      </div>

      <div className="flex flex-col gap-4">
        <PhaseRail phase={thread.phase} hasApproval={thread.plan.length > 0 || thread.phase === 'awaiting_approval'} />

        {thread.phase === 'awaiting_approval' && (
          <ApprovalCard plan={thread.plan} onApprove={onApprove} live={thread.live} />
        )}

        {thread.phase === 'planning' && <PlanningCard />}

        {(thread.tasks.length > 0 || thread.phase === 'synthesizing' || thread.phase === 'done') && (
          <TaskFlow thread={thread} />
        )}

        {thread.error && (
          <div className="console-card p-3 text-[13px]" style={{ borderColor: 'rgba(229,106,130,.4)', color: 'var(--bad)' }}>
            {thread.phase === 'aborted' ? 'Run stopped: ' : 'Run failed: '}
            {thread.error}
          </div>
        )}

        {thread.answer && (thread.phase === 'done' || !thread.live) && (
          <AnswerBlock text={thread.answer} animate={thread.live} />
        )}

        {thread.metrics && thread.phase === 'done' && <MetricsRow m={thread.metrics} />}
      </div>
    </div>
  )
}

/* ── phase rail ─────────────────────────────────────────────────────────────── */

function PhaseRail({ phase, hasApproval }: { phase: ThreadPhase; hasApproval: boolean }) {
  const phases: { id: string; label: string }[] = [
    { id: 'plan', label: 'Plan' },
    ...(hasApproval ? [{ id: 'approve', label: 'Approve' }] : []),
    { id: 'execute', label: 'Execute' },
    { id: 'synthesize', label: 'Synthesize' },
    { id: 'deliver', label: 'Deliver' },
  ]
  const activeId: string =
    phase === 'planning' ? 'plan'
    : phase === 'awaiting_approval' ? 'approve'
    : phase === 'executing' ? 'execute'
    : phase === 'synthesizing' ? 'synthesize'
    : 'deliver'
  const activeIdx = phases.findIndex((p) => p.id === activeId)
  const settled = phase === 'done' || phase === 'failed' || phase === 'aborted'

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {phases.map((p, i) => {
        const done = settled || i < activeIdx
        const active = !settled && i === activeIdx
        return (
          <div key={p.id} className="flex items-center gap-1.5">
            <div
              className={clsx('flex items-center gap-2 font-code text-[12.5px]',
                active ? 'text-[var(--text)]' : done ? 'text-[var(--muted)]' : 'text-[var(--faint)]')}
            >
              <span
                className="w-2 h-2 rounded-full border-[1.5px] transition-all"
                style={{
                  borderColor: active ? 'var(--accent)' : done ? 'var(--muted)' : 'var(--faint)',
                  background: active ? 'var(--accent)' : done ? 'var(--muted)' : 'transparent',
                  boxShadow: active ? '0 0 0 4px rgba(124,135,245,.15)' : 'none',
                }}
              />
              {p.label}
            </div>
            {i < phases.length - 1 && <span className="w-5 h-px" style={{ background: 'var(--line)' }} />}
          </div>
        )
      })}
    </div>
  )
}

/* ── planning skeleton ──────────────────────────────────────────────────────── */

function PlanningCard() {
  return (
    <div className="console-card p-3.5 flex items-center gap-3">
      <span className="w-3.5 h-3.5 rounded-full border-2 anim-spin"
        style={{ borderColor: 'var(--accent)', borderRightColor: 'transparent' }} />
      <span className="text-[13.5px] text-[var(--muted)]">
        Breaking your prompt into tasks…
      </span>
    </div>
  )
}

/* ── approval card ──────────────────────────────────────────────────────────── */

function ApprovalCard({ plan, onApprove, live }:
  { plan: string[]; onApprove?: (d: 'approve' | 'reject') => void; live: boolean }) {
  return (
    <div className="console-card p-4" style={{ borderColor: 'rgba(228,197,106,.45)' }}>
      <div className="flex items-center gap-2 mb-2.5">
        <span className="w-2 h-2 rounded-full anim-dot-pulse" style={{ background: 'var(--warn)' }} />
        <span className="eyebrow" style={{ color: 'var(--warn)' }}>Plan ready — approval needed</span>
      </div>
      {plan.length > 0 ? (
        <ol className="space-y-1.5 mb-3">
          {plan.map((t, i) => (
            <li key={i} className="flex items-start gap-2.5 text-[13.5px]">
              <span className="mt-0.5 flex-none w-5 h-5 rounded-full grid place-items-center font-code text-[11px]"
                style={{ background: 'rgba(124,135,245,.15)', color: 'var(--accent)' }}>
                {i + 1}
              </span>
              {t}
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-[13px] text-[var(--muted)] mb-3">The orchestrator paused for your decision.</p>
      )}
      {onApprove && (
        <div className="flex gap-2">
          <button
            onClick={() => onApprove('approve')}
            className="px-4 py-1.5 rounded-lg text-[13px] font-medium transition-colors"
            style={{ background: 'var(--ok)', color: '#0b0f18' }}
          >
            Approve &amp; run
          </button>
          <button
            onClick={() => onApprove('reject')}
            className="px-4 py-1.5 rounded-lg text-[13px] border transition-colors hover:bg-[rgba(229,106,130,.09)]"
            style={{ borderColor: 'rgba(229,106,130,.4)', color: 'var(--bad)' }}
          >
            Reject
          </button>
        </div>
      )}
      {!live && !onApprove && (
        <p className="text-[12px] text-[var(--faint)]">Waiting for approval.</p>
      )}
    </div>
  )
}

/* ── task flow timeline ─────────────────────────────────────────────────────── */

const SEG_INDEX: Record<TaskState, number> = {
  queued: 0, running: 1, retrying: 1, validating: 2, done: 3, degraded: 3, failed: 3,
}

function stateLabel(t: ThreadTask): string {
  switch (t.state) {
    case 'queued': return 'queued'
    case 'running': return t.tier ? `running · ${t.tier}` : 'running'
    case 'retrying': return `retry ${t.attempts}`
    case 'validating': return 'validating'
    case 'degraded': return 'degraded'
    case 'failed': return 'failed'
    default: return t.tier ? `done · ${t.tier}` : 'done'
  }
}

function TaskFlow({ thread }: { thread: ThreadModel }) {
  const synthActive = thread.phase === 'synthesizing'
  const synthDone = thread.phase === 'done'
  return (
    <div className="relative pl-[26px]">
      {/* spine */}
      <span className="absolute left-[7px] top-1.5 bottom-1.5 w-0.5 rounded"
        style={{ background: 'linear-gradient(var(--line),var(--line-soft))' }} />
      {thread.tasks.map((t, i) => (
        <TaskNode key={t.id} task={t} index={i} />
      ))}
      {(synthActive || synthDone) && (
        <div className="relative mb-3">
          <span className={clsx('absolute -left-[26px] top-3.5 w-3 h-3 rounded-full border-2 z-[1]',
            synthActive && 'anim-mk-pulse')}
            style={{ background: 'var(--ink)', borderColor: 'var(--t5)' }} />
          <div className="console-card px-3.5 py-3 flex items-center gap-2.5 text-[13.5px]">
            {synthActive ? (
              <>
                <span className="w-3.5 h-3.5 rounded-full border-2 anim-spin flex-none"
                  style={{ borderColor: 'var(--t5)', borderRightColor: 'transparent' }} />
                <span>Synthesizing {thread.tasks.length} verified result{thread.tasks.length === 1 ? '' : 's'}…</span>
              </>
            ) : (
              <>
                <span className="w-2.5 h-2.5 rounded-full flex-none" style={{ background: 'var(--ok)' }} />
                <span className="text-[var(--muted)]">
                  Synthesis complete — every result validated before roll-up
                </span>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function TaskNode({ task, index }: { task: ThreadTask; index: number }) {
  const busy = task.state === 'running' || task.state === 'validating' || task.state === 'retrying'
  const segIdx = SEG_INDEX[task.state]
  const terminal = segIdx === 3
  const color = tierColor(task.tier)
  return (
    <div className="relative mb-3">
      <span
        className={clsx('absolute -left-[26px] top-3.5 w-3 h-3 rounded-full border-2 z-[1]', busy && 'anim-mk-pulse')}
        style={{ background: 'var(--ink)', borderColor: task.tier ? color : 'var(--muted)' }}
      />
      <div className="console-card px-3.5 py-3 anim-task-in" style={{ animationDelay: `${index * 70}ms` }}>
        <div className="flex items-center gap-2.5">
          <span className="flex-1 text-[13.5px] truncate" title={task.name}>{task.name}</span>
          <span className="flex-none font-code text-[10.5px] px-1.5 py-0.5 rounded-md"
            style={{ color: 'var(--muted)', background: 'var(--elev2)' }}>
            {task.role}
          </span>
          {task.tier && (
            <span className="flex-none font-code text-[11px] font-medium px-1.5 py-0.5 rounded-md"
              style={{ color, background: `color-mix(in srgb, ${color} 13%, transparent)` }}>
              {task.tier}
            </span>
          )}
          <span className={clsx('flex-none font-code text-[11px] min-w-[92px] text-right')}
            style={{
              color: task.state === 'failed' ? 'var(--bad)'
                : task.state === 'degraded' ? 'var(--warn)'
                : task.state === 'retrying' ? 'var(--t4)'
                : 'var(--muted)',
            }}>
            {stateLabel(task)}
          </span>
        </div>
        <div className="flex gap-1 mt-2.5">
          {[0, 1, 2].map((s) => (
            <span key={s}
              className={clsx('h-[3px] flex-1 rounded transition-colors',
                s === segIdx && !terminal && 'anim-seg-pulse')}
              style={{
                background:
                  task.state === 'failed' ? 'rgba(229,106,130,.55)'
                  : s < segIdx || terminal ? 'var(--muted)'
                  : s === segIdx ? color
                  : 'var(--line)',
              }}
            />
          ))}
        </div>
        {task.hint && (task.state === 'retrying' || task.state === 'degraded' || task.state === 'failed') && (
          <p className="mt-2 text-[11.5px]" style={{ color: 'var(--warn)' }}>
            ↻ validator: {task.hint.slice(0, 110)}{task.hint.length > 110 ? '…' : ''}
          </p>
        )}
      </div>
    </div>
  )
}

/* ── answer with typewriter ─────────────────────────────────────────────────── */

function AnswerBlock({ text, animate }: { text: string; animate: boolean }) {
  const [shown, setShown] = useState(animate ? 0 : text.length)
  const done = shown >= text.length
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!animate) { setShown(text.length); return }
    setShown(0)
    const timer = setInterval(() => {
      setShown((n) => {
        const next = n + 2 + Math.floor(Math.random() * 3)
        if (next >= text.length) clearInterval(timer)
        return Math.min(next, text.length)
      })
    }, 18)
    return () => clearInterval(timer)
  }, [text, animate])

  useEffect(() => {
    if (!done) ref.current?.scrollIntoView({ block: 'end' })
  }, [shown, done])

  return (
    <div ref={ref}>
      <div className="eyebrow mb-2.5">Answer</div>
      <div className="text-[14.5px] leading-[1.7] whitespace-pre-wrap min-h-[18px]">
        {text.slice(0, shown)}
        {!done && <span className="answer-caret" />}
      </div>
    </div>
  )
}

/* ── run metrics row ────────────────────────────────────────────────────────── */

function MetricsRow({ m }: { m: NonNullable<ThreadModel['metrics']> }) {
  const tiers = TIER_ORDER.filter((t) => (m.tier_calls?.[t] ?? 0) > 0)
  const totalCalls = tiers.reduce((a, t) => a + (m.tier_calls?.[t] ?? 0), 0) || 1
  return (
    <div className="console-card px-4 py-3 flex items-center gap-5 flex-wrap text-[12px]" style={{ color: 'var(--muted)' }}>
      <span><b className="text-[var(--text)] font-semibold">{m.task_count}</b> agents</span>
      <span><b className="text-[var(--text)] font-semibold">{m.call_count}</b> model calls</span>
      {m.cached_calls > 0 && <span><b className="text-[var(--text)] font-semibold">{m.cached_calls}</b> cached</span>}
      <span><b className="text-[var(--text)] font-semibold">{(m.total_tokens ?? 0).toLocaleString()}</b> tokens</span>
      {/* tier distribution strip */}
      <span className="flex items-center gap-2 flex-1 min-w-[140px]">
        <span className="flex h-1.5 flex-1 rounded overflow-hidden" style={{ background: 'var(--line)' }}>
          {tiers.map((t) => (
            <span key={t} style={{
              width: `${((m.tier_calls?.[t] ?? 0) / totalCalls) * 100}%`,
              background: tierColor(t),
            }} />
          ))}
        </span>
        <span className="font-code text-[10.5px] whitespace-nowrap" style={{ color: 'var(--faint)' }}>
          {tiers.map((t) => `${t}·${m.tier_calls?.[t]}`).join('  ')}
        </span>
      </span>
    </div>
  )
}
