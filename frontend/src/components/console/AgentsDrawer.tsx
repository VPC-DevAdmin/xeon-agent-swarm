import { useState } from 'react'
import clsx from 'clsx'
import { approveRun, jobsApi, runsApi } from '../../api/client'
import type { Job, RunSummary } from '../../api/types'
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
            <JobCard key={j.id} job={j} onChanged={onChanged} />
          ))}
        </div>
      </aside>
    </>
  )
}

function JobCard({ job, onChanged }: { job: Job; onChanged: () => void }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const paused = job.status === 'paused'

  async function setCadence(cron: string | null) {
    setMenuOpen(false)
    if (cron === null) {
      await jobsApi.archive(job.id) // "run once" from a job = retire the schedule
    } else {
      await jobsApi.update(job.id, { schedule_cron: cron })
    }
    onChanged()
  }

  return (
    <div className={clsx('console-card p-3', paused && 'opacity-70')}>
      <div className="font-display font-semibold text-[13.5px] truncate" title={job.query}>{job.name}</div>
      <div className="flex items-center gap-2 mt-1 text-[11.5px] text-[var(--muted)]">
        <span className="font-code">{cadenceLabel(job.schedule_cron)}</span>
        {job.next_fire_at && !paused && (
          <span className="text-[var(--faint)]">· next {new Date(job.next_fire_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        )}
        {paused && <span style={{ color: 'var(--warn)' }}>· paused</span>}
      </div>
      <div className="flex items-center gap-1.5 mt-2.5">
        <div className="relative flex-1">
          <button onClick={() => setMenuOpen((v) => !v)}
            className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg border font-code text-[11.5px] text-[var(--muted)] hover:text-[var(--text)]"
            style={{ background: 'var(--elev2)', borderColor: 'var(--line)' }}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M23 4v6h-6M1 20v-6h6" /><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15" />
            </svg>
            {cadenceLabel(job.schedule_cron)}
          </button>
          {menuOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
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
        <button title="Run now" onClick={() => jobsApi.runNow(job.id).then(onChanged)}
          className="w-8 h-8 grid place-items-center rounded-lg border text-[var(--faint)] hover:text-[var(--accent)]"
          style={{ borderColor: 'var(--line)' }}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
        </button>
        <button title={paused ? 'Resume' : 'Pause'}
          onClick={() => (paused ? jobsApi.resume(job.id) : jobsApi.pause(job.id)).then(onChanged)}
          className="w-8 h-8 grid place-items-center rounded-lg border text-[var(--faint)] hover:text-[var(--text)]"
          style={{ borderColor: 'var(--line)' }}>
          {paused
            ? <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
            : <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4h4v16H6zM14 4h4v16h-4z" /></svg>}
        </button>
      </div>
    </div>
  )
}
