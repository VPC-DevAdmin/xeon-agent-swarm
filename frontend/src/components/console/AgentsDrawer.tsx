import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { approveRun, jobsApi, runsApi } from '../../api/client'
import type { Job, RunSummary } from '../../api/types'
import { parseServerDate, planToTasks, tierColor } from '../../lib/thread'
import { timeAgo } from '../ui'
import { SCHEDULE_PRESETS } from './Composer'

interface Props {
  open: boolean
  runs: RunSummary[]
  jobs: Job[]
  focusId: string | null
  onClose: () => void
  onFocus: (runId: string) => void
  onChanged: () => void
}

const ACTIVE = ['pending', 'running', 'awaiting_approval']

function cadenceLabel(cron?: string | null): string {
  const preset = SCHEDULE_PRESETS.find((p) => p.cron === cron)
  return preset ? preset.label : cron || 'on demand'
}

/**
 * AgentsDrawer — the fleet view: everything active right now, and every
 * recurring task with its cadence (editable inline), next fire, and controls.
 */
export function AgentsDrawer({ open, runs, jobs, focusId, onClose, onFocus, onChanged }: Props) {
  const active = runs.filter((r) => ACTIVE.includes(r.status))
  const scheduled = jobs.filter((j) => j.schedule_cron && j.status !== 'archived')

  return (
    <>
      <div
        className={clsx('fixed inset-0 z-[31] transition-opacity',
          open ? 'opacity-100' : 'opacity-0 pointer-events-none')}
        style={{ background: 'rgba(6,9,14,.4)' }}
        onClick={onClose}
      />
      <aside
        className={clsx('fixed top-0 right-0 h-screen w-[340px] z-[32] flex flex-col border-l transition-transform duration-[260ms]',
          open ? 'translate-x-0' : 'translate-x-full')}
        style={{ background: 'var(--panel)', borderColor: 'var(--line)', boxShadow: '-24px 0 60px -30px rgba(0,0,0,.8)' }}
      >
        <div className="flex items-center gap-2.5 px-4 pt-4 pb-3 border-b" style={{ borderColor: 'var(--line-soft)' }}>
          <h3 className="font-display font-semibold text-[15px]">Agents</h3>
          <span className="font-code text-[12px] text-[var(--faint)]">
            {active.length} active · {scheduled.length} scheduled
          </span>
          <button onClick={onClose}
            className="ml-auto w-7 h-7 grid place-items-center rounded-[7px] text-[var(--faint)] hover:text-[var(--text)] hover:bg-[var(--elev)]">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2.5 flex flex-col gap-2">
          {active.length > 0 && <div className="eyebrow px-1.5 pt-1">Running now</div>}
          {active.map((r) => (
            <div key={r.id}
              className={clsx('console-card p-3 cursor-pointer transition-colors hover:!border-[#39445c]',
                r.id === focusId && '!border-[rgba(124,135,245,.5)]')}
              onClick={() => onFocus(r.id)}
            >
              <div className="flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full anim-dot-pulse flex-none"
                  style={{ background: r.status === 'awaiting_approval' ? 'var(--warn)' : 'var(--accent)' }} />
                <span className="text-[13px] truncate flex-1">{r.query}</span>
              </div>
              <div className="flex items-center gap-2 mt-2.5" onClick={(e) => e.stopPropagation()}>
                {r.status === 'awaiting_approval' ? (
                  <button onClick={() => approveRun(r.id, 'approve').then(onChanged)}
                    className="flex-1 py-1.5 rounded-lg text-[12px] font-code font-medium"
                    style={{ background: 'var(--ok)', color: '#0b0f18' }}>
                    Approve plan
                  </button>
                ) : (
                  <span className="flex-1 font-code text-[11.5px] text-[var(--faint)]">{r.status}</span>
                )}
                <button title="Stop run"
                  onClick={() => runsApi.kill(r.id).then(onChanged)}
                  className="w-8 h-8 grid place-items-center rounded-lg border text-[var(--faint)] hover:!text-[var(--bad)] hover:!border-[rgba(229,106,130,.3)] hover:bg-[rgba(229,106,130,.09)]"
                  style={{ borderColor: 'var(--line)' }}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
                </button>
              </div>
            </div>
          ))}

          <div className="eyebrow px-1.5 pt-2">Scheduled</div>
          {scheduled.length === 0 && (
            <p className="px-1.5 text-[12.5px] text-[var(--faint)]">
              Nothing recurring yet — pick an interval in the composer.
            </p>
          )}
          {scheduled.map((j) => (
            <JobCard key={j.id} job={j} onFocus={onFocus} onChanged={onChanged} />
          ))}
        </div>
      </aside>
    </>
  )
}

type Section = 'schedule' | 'tasks' | 'history'

interface JobTasks {
  plan: string[]
  steps: { role: string; name: string; tier: string | null }[]
}

function JobCard({ job, onFocus, onChanged }:
  { job: Job; onFocus: (runId: string) => void; onChanged: () => void }) {
  const [open, setOpen] = useState<Section | null>(null)
  const [history, setHistory] = useState<RunSummary[] | null>(null)
  const [tasks, setTasks] = useState<JobTasks | null>(null)
  const [cadenceMenu, setCadenceMenu] = useState(false)
  const paused = job.status === 'paused'

  // Load the open section's data; refetch when a new run lands (last_run_id changes).
  useEffect(() => {
    if (open === 'history') {
      runsApi.list({ job_id: job.id, limit: 25 }).then(setHistory).catch(() => setHistory([]))
    } else if (open === 'tasks') {
      if (!job.last_run_id) { setTasks({ plan: [], steps: [] }); return }
      runsApi.get(job.last_run_id).then((d) => {
        setTasks({
          plan: planToTasks((d.task_graph?.plan as string) ?? null),
          steps: d.steps.filter((s) => s.step_key !== 'orchestrator').map((s) => ({
            role: s.type,
            name: s.objective || s.step_key,
            tier: s.attempts.filter((a) => a.tier_observed).slice(-1)[0]?.tier_observed ?? null,
          })),
        })
      }).catch(() => setTasks({ plan: [], steps: [] }))
    }
  }, [open, job.id, job.last_run_id])

  async function setCadence(cron: string | null) {
    setCadenceMenu(false)
    if (cron === null) await jobsApi.archive(job.id)
    else await jobsApi.update(job.id, { schedule_cron: cron })
    onChanged()
  }

  const toggle = (s: Section) => setOpen((cur) => (cur === s ? null : s))

  return (
    <div className={clsx('console-card p-3', paused && 'opacity-70')}>
      {/* header: name + quick actions */}
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="font-display font-semibold text-[13.5px] truncate" title={job.query}>{job.name}</div>
          <div className="flex items-center gap-1.5 mt-0.5 text-[11.5px] text-[var(--muted)]">
            <span className="font-code">{cadenceLabel(job.schedule_cron)}</span>
            {job.next_fire_at && !paused && (
              <span className="text-[var(--faint)]">· next {parseServerDate(job.next_fire_at)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            )}
            {paused && <span style={{ color: 'var(--warn)' }}>· paused</span>}
          </div>
        </div>
        <button title="Run now" onClick={() => jobsApi.runNow(job.id).then(onChanged)}
          className="flex-none w-7 h-7 grid place-items-center rounded-lg border text-[var(--faint)] hover:text-[var(--accent)]"
          style={{ borderColor: 'var(--line)' }}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
        </button>
        <button title={paused ? 'Resume' : 'Pause'}
          onClick={() => (paused ? jobsApi.resume(job.id) : jobsApi.pause(job.id)).then(onChanged)}
          className="flex-none w-7 h-7 grid place-items-center rounded-lg border text-[var(--faint)] hover:text-[var(--text)]"
          style={{ borderColor: 'var(--line)' }}>
          {paused
            ? <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
            : <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4h4v16H6zM14 4h4v16h-4z" /></svg>}
        </button>
      </div>

      {/* section toggles */}
      <div className="flex gap-1 mt-2.5">
        {(['schedule', 'tasks', 'history'] as Section[]).map((s) => (
          <button key={s} onClick={() => toggle(s)}
            className={clsx('flex-1 flex items-center justify-center gap-1 py-1.5 rounded-lg border font-code text-[11px] capitalize transition-colors',
              open === s ? 'text-[var(--text)]' : 'text-[var(--muted)] hover:text-[var(--text)]')}
            style={{ background: 'var(--elev2)', borderColor: open === s ? 'rgba(124,135,245,.4)' : 'var(--line)' }}>
            {s}
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
              style={{ transform: open === s ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }}>
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        ))}
      </div>

      {/* expanded content */}
      {open && (
        <div className="mt-2.5 pt-2.5 border-t anim-task-in" style={{ borderColor: 'var(--line-soft)' }}>
          {open === 'schedule' && (
            <div className="flex flex-col gap-1.5 text-[12px]">
              <Kv k="Cadence" v={cadenceLabel(job.schedule_cron)} />
              <Kv k="Cron" v={job.schedule_cron || '—'} mono />
              <Kv k="Next fire" v={parseServerDate(job.next_fire_at)?.toLocaleString() ?? (paused ? 'paused' : '—')} />
              <Kv k="Overlap" v={job.overlap_policy} />
              <div className="relative mt-1.5">
                <button onClick={() => setCadenceMenu((v) => !v)}
                  className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg border font-code text-[11.5px] text-[var(--muted)] hover:text-[var(--text)]"
                  style={{ background: 'var(--elev2)', borderColor: 'var(--line)' }}>
                  Change cadence
                </button>
                {cadenceMenu && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setCadenceMenu(false)} />
                    <div className="absolute left-0 right-0 bottom-[calc(100%+6px)] z-20 p-1.5 rounded-[10px] anim-pop"
                      style={{ background: 'var(--elev2)', border: '1px solid var(--line)', boxShadow: 'var(--shadow)' }}>
                      {SCHEDULE_PRESETS.filter((p) => p.cron !== null).map((p) => (
                        <button key={p.id} onClick={() => setCadence(p.cron)}
                          className="flex justify-between items-center w-full px-2.5 py-1.5 rounded-md text-[12px] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--panel)]">
                          {p.label}
                          <span style={{ color: 'var(--accent)', opacity: job.schedule_cron === p.cron ? 1 : 0 }}>✓</span>
                        </button>
                      ))}
                      <button onClick={() => setCadence(null)}
                        className="w-full px-2.5 py-1.5 rounded-md text-left text-[12px] hover:bg-[var(--panel)]"
                        style={{ color: 'var(--bad)' }}>
                        Remove schedule
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {open === 'tasks' && (
            tasks === null ? <Loading />
            : tasks.plan.length === 0 && tasks.steps.length === 0 ? (
              <p className="text-[12px] text-[var(--faint)]">Tasks appear after the first run.</p>
            ) : (
              <>
                <div className="eyebrow mb-1.5">Latest decomposition</div>
                <ol className="flex flex-col gap-1.5">
                  {(tasks.steps.length ? tasks.steps : tasks.plan.map((p) => ({ role: '', name: p, tier: null }))).map((t, i) => (
                    <li key={i} className="flex items-start gap-2 text-[12px]">
                      <span className="mt-0.5 flex-none w-4 h-4 rounded-full grid place-items-center font-code text-[9px]"
                        style={{ background: 'rgba(124,135,245,.15)', color: 'var(--accent)' }}>{i + 1}</span>
                      <span className="flex-1 text-[var(--text)]">{t.name}</span>
                      {t.tier && (
                        <span className="flex-none font-code text-[10px] px-1 rounded"
                          style={{ color: tierColor(t.tier), background: `color-mix(in srgb, ${tierColor(t.tier)} 13%, transparent)` }}>
                          {t.tier}
                        </span>
                      )}
                    </li>
                  ))}
                </ol>
              </>
            )
          )}

          {open === 'history' && (
            history === null ? <Loading />
            : history.length === 0 ? (
              <p className="text-[12px] text-[var(--faint)]">No runs yet.</p>
            ) : (
              <div className="flex flex-col gap-0.5">
                {history.map((r) => (
                  <button key={r.id} onClick={() => onFocus(r.id)}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-md text-left hover:bg-[var(--elev2)] transition-colors">
                    <span className="w-1.5 h-1.5 rounded-full flex-none" style={{ background: runDot(r.status) }} />
                    <span className="flex-1 text-[12px] text-[var(--muted)] truncate">{timeAgo(r.started_at)}</span>
                    <span className="font-code text-[10.5px] text-[var(--faint)]">{r.status}</span>
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--faint)" strokeWidth="2"><path d="M9 18l6-6-6-6" /></svg>
                  </button>
                ))}
              </div>
            )
          )}
        </div>
      )}
    </div>
  )
}

function Kv({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-[var(--faint)]">{k}</span>
      <span className={clsx('text-[var(--muted)] text-right truncate', mono && 'font-code text-[11px]')}>{v}</span>
    </div>
  )
}

function Loading() {
  return <p className="text-[12px] text-[var(--faint)]">Loading…</p>
}

function runDot(status: string): string {
  if (status === 'completed') return 'var(--ok)'
  if (status === 'failed') return 'var(--bad)'
  if (['running', 'pending', 'awaiting_approval'].includes(status)) return 'var(--accent)'
  return 'var(--faint)'
}
