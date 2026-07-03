import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { approveRun as approveRunApi, jobsApi, runsApi } from '../api/client'
import type { Job, RunDetail, RunSummary } from '../api/types'
import { useSwarmSocket } from '../hooks/useSwarmSocket'
import { useSwarmStore } from '../store/swarmStore'
import { buildDetailThread, buildLiveThread, type ThreadModel } from '../lib/thread'
import { HistorySidebar } from '../components/console/HistorySidebar'
import { ThreadView } from '../components/console/ThreadView'
import { Composer } from '../components/console/Composer'
import { AgentsDrawer } from '../components/console/AgentsDrawer'
import { TelemetryView } from '../components/console/TelemetryView'

const ACTIVE_STATUSES = ['pending', 'running', 'awaiting_approval']

/**
 * ConsolePage — the whole product on one screen.
 * Left: prompt history. Center: the focused run as a conversation (or the
 * telemetry view). Bottom: the composer. Right (slide-over): the agent fleet.
 */
export function ConsolePage() {
  const store = useSwarmStore()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [focusId, setFocusId] = useState<string | null>(null)
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [view, setView] = useState<'thread' | 'telemetry'>('thread')
  const [collapsed, setCollapsed] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  useSwarmSocket(store.runId)

  const refresh = useCallback(async () => {
    try {
      const [rs, js] = await Promise.all([runsApi.list({ limit: 100 }), jobsApi.list()])
      setRuns(rs)
      setJobs(js)
    } catch { /* backend briefly unreachable — next poll wins */ }
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  // Focused run that is NOT this session's live run: load (and poll while active).
  const isLiveFocus = focusId !== null && focusId === store.runId
  useEffect(() => {
    if (!focusId || isLiveFocus) { setDetail(null); return }
    let cancelled = false
    const load = () => runsApi.get(focusId)
      .then((d) => { if (!cancelled) setDetail(d) })
      .catch(() => { /* keep last snapshot */ })
    load()
    const t = setInterval(() => {
      if (detail && !ACTIVE_STATUSES.includes(detail.status)) return
      load()
    }, 2500)
    return () => { cancelled = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId, isLiveFocus, detail?.status])

  const thread: ThreadModel | null = isLiveFocus && store.runId
    ? buildLiveThread({ ...store, runId: store.runId })
    : detail && detail.run_id === focusId
    ? buildDetailThread(detail)
    : null

  const handleRunStart = useCallback((runId: string, prompt: string) => {
    store.reset()
    store.startRun(runId, prompt)
    setFocusId(runId)
    setView('thread')
    setNotice(null)
    refresh()
  }, [store, refresh])

  const handleScheduled = useCallback((name: string, cadence: string) => {
    setNotice(`Scheduled “${name}” — ${cadence.toLowerCase()}. It's in the Agents panel.`)
    refresh()
    setDrawerOpen(true)
  }, [refresh])

  const handleApprove = useCallback((decision: 'approve' | 'reject') => {
    if (isLiveFocus) store.approveRun(decision)
    else if (focusId) approveRunApi(focusId, decision).then(refresh)
  }, [isLiveFocus, focusId, store, refresh])

  const activeCount = runs.filter((r) => ACTIVE_STATUSES.includes(r.status)).length
    + jobs.filter((j) => j.schedule_cron && j.status === 'active').length

  const title = view === 'telemetry' ? 'Telemetry'
    : thread ? thread.prompt : 'New prompt'
  const meta = view === 'telemetry' ? 'live picture'
    : thread ? `${thread.tasks.length ? `${thread.tasks.length} tasks · ` : ''}${thread.phase.replace('_', ' ')}` : ''

  return (
    <div className="flex h-screen overflow-hidden">
      <HistorySidebar
        runs={runs}
        focusId={focusId}
        collapsed={collapsed}
        onFocus={(id) => { setFocusId(id); setView('thread') }}
        onNew={() => { setFocusId(null); setView('thread'); setNotice(null) }}
        onCollapse={() => setCollapsed(true)}
      />

      <main className="flex-1 flex flex-col min-w-0 relative">
        {/* top bar */}
        <div className="flex items-center gap-3 px-5 py-2.5 border-b" style={{ borderColor: 'var(--line-soft)' }}>
          {collapsed && (
            <button onClick={() => setCollapsed(false)} title="Open sidebar"
              className="w-7 h-7 grid place-items-center rounded-[7px] text-[var(--faint)] hover:text-[var(--muted)] hover:bg-[var(--elev)]">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
            </button>
          )}
          <div className="font-display font-semibold text-[14.5px] truncate min-w-0" title={title}>{title}</div>
          <div className="font-code text-[11.5px] text-[var(--faint)] whitespace-nowrap">{meta}</div>

          <div className="ml-auto flex gap-0.5 p-[3px] rounded-[10px] border"
            style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}>
            {(['thread', 'telemetry'] as const).map((v) => (
              <button key={v} onClick={() => setView(v)}
                className={clsx('px-3 py-1.5 rounded-[7px] text-[12.5px] font-medium capitalize transition-colors',
                  view === v ? 'bg-[var(--elev)] text-[var(--text)]' : 'text-[var(--muted)] hover:text-[var(--text)]')}>
                {v === 'thread' ? 'Console' : 'Telemetry'}
              </button>
            ))}
          </div>

          <button onClick={() => setDrawerOpen(true)}
            className="flex items-center gap-2 px-3 py-[7px] rounded-[9px] border text-[13px] text-[var(--muted)] hover:text-[var(--text)] hover:!border-[#39445c] transition-colors"
            style={{ background: 'var(--elev)', borderColor: 'var(--line)' }}>
            Agents
            <span className="font-code text-[11px] font-semibold px-1.5 rounded-full"
              style={{ background: 'var(--accent)', color: '#0b0f18' }}>
              {activeCount}
            </span>
          </button>
        </div>

        {/* center */}
        <div className="flex-1 overflow-y-auto px-7 pt-6">
          {view === 'telemetry' ? (
            <TelemetryView runs={runs} jobs={jobs} />
          ) : thread ? (
            <ThreadView thread={thread} onApprove={handleApprove} />
          ) : (
            <EmptyState notice={notice} />
          )}
        </div>

        {/* composer */}
        {view === 'thread' && (
          <Composer
            onRunStart={handleRunStart}
            onScheduled={handleScheduled}
          />
        )}
      </main>

      <AgentsDrawer
        open={drawerOpen}
        runs={runs}
        jobs={jobs}
        focusId={focusId}
        onClose={() => setDrawerOpen(false)}
        onFocus={(id) => { setFocusId(id); setView('thread'); setDrawerOpen(false) }}
        onChanged={refresh}
      />
    </div>
  )
}

function EmptyState({ notice }: { notice: string | null }) {
  return (
    <div className="h-full grid place-content-center text-center gap-3 pb-16">
      {notice && (
        <div className="console-card px-4 py-2.5 text-[13px] mb-4" style={{ color: 'var(--ok)' }}>
          ✓ {notice}
        </div>
      )}
      <h2 className="font-display font-semibold text-[22px] m-0">Ready when you are</h2>
      <p className="text-[13.5px] text-[var(--muted)] max-w-[440px]">
        Send a prompt below. It becomes a plan you can approve, a team of agents
        executes it — each output verified — and the answer lands here.
        Pick an interval to make it recur.
      </p>
      <p className="font-code text-[11.5px] text-[var(--faint)]">
        ✨ opens the sample library · shield toggles plan review
      </p>
    </div>
  )
}
