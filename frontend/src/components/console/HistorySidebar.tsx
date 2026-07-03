import clsx from 'clsx'
import type { RunSummary } from '../../api/types'
import { dayGroup } from '../../lib/thread'

interface Props {
  runs: RunSummary[]
  focusId: string | null
  collapsed: boolean
  onFocus: (runId: string) => void
  onNew: () => void
  onCollapse: () => void
}

function statusDot(status: string): { color: string; pulse: boolean } {
  switch (status) {
    case 'running':
    case 'pending': return { color: 'var(--accent)', pulse: true }
    case 'awaiting_approval': return { color: 'var(--warn)', pulse: true }
    case 'completed': return { color: 'var(--ok)', pulse: false }
    case 'failed': return { color: 'var(--bad)', pulse: false }
    default: return { color: 'var(--faint)', pulse: false }
  }
}

const GROUP_ORDER = ['Today', 'Yesterday', 'This week', 'Earlier']

/** HistorySidebar — every prompt ever run, grouped by day, newest first. */
export function HistorySidebar({ runs, focusId, collapsed, onFocus, onNew, onCollapse }: Props) {
  const grouped: Record<string, RunSummary[]> = {}
  for (const r of runs) (grouped[dayGroup(r.started_at)] ??= []).push(r)

  return (
    <aside
      className={clsx('flex flex-col min-w-0 overflow-hidden border-r transition-[width] duration-200',
        collapsed ? 'w-0' : 'w-[240px]')}
      style={{ background: 'var(--panel)', borderColor: 'var(--line)' }}
    >
      <div className="flex items-center gap-2.5 px-4 pt-4 pb-3">
        <span className="w-[7px] h-[7px] rounded-full flex-none"
          style={{ background: 'var(--accent)', boxShadow: '0 0 0 4px rgba(124,135,245,.16)' }} />
        <h1 className="font-display font-bold text-[16px] tracking-[-0.02em] whitespace-nowrap">
          Agent Orchestrator
        </h1>
        <button onClick={onCollapse} title="Collapse sidebar"
          className="ml-auto w-[26px] h-[26px] grid place-items-center rounded-[7px] text-[var(--faint)] hover:text-[var(--muted)] hover:bg-[var(--elev)]">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M15 18l-6-6 6-6" /></svg>
        </button>
      </div>

      <button onClick={onNew}
        className="mx-3 mb-3 px-3 py-2 rounded-[10px] border text-[13px] font-medium flex items-center gap-2 hover:bg-[var(--elev)] hover:!border-[#39445c] transition-colors"
        style={{ borderColor: 'var(--line)' }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14" /></svg>
        New prompt
      </button>

      <div className="flex-1 overflow-y-auto px-2 pb-4">
        {GROUP_ORDER.filter((g) => grouped[g]?.length).map((g) => (
          <div key={g}>
            <div className="eyebrow px-2.5 pt-2.5 pb-1">{g}</div>
            {grouped[g].map((r) => {
              const dot = statusDot(r.status)
              return (
                <button key={r.id}
                  onClick={() => onFocus(r.id)}
                  title={r.query}
                  className={clsx('w-full text-left px-2.5 py-2 rounded-lg text-[13px] flex items-center gap-2 truncate transition-colors',
                    r.id === focusId
                      ? 'bg-[var(--elev2)] text-[var(--text)]'
                      : 'text-[var(--muted)] hover:bg-[var(--elev)] hover:text-[var(--text)]')}
                >
                  <span className={clsx('w-1.5 h-1.5 rounded-full flex-none', dot.pulse && 'anim-dot-pulse')}
                    style={{ background: dot.color }} />
                  <span className="truncate">{r.query}</span>
                </button>
              )
            })}
          </div>
        ))}
        {runs.length === 0 && (
          <p className="px-3 py-2 text-[12.5px] text-[var(--faint)]">No runs yet.</p>
        )}
      </div>

      <div className="px-4 py-3 border-t flex gap-4 text-[11.5px]" style={{ borderColor: 'var(--line-soft)' }}>
        <a href="/connectors" className="text-[var(--faint)] hover:text-[var(--muted)]">Connectors</a>
        <a href="/activity" className="text-[var(--faint)] hover:text-[var(--muted)]">Classic view</a>
      </div>
    </aside>
  )
}
