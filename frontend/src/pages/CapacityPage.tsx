import { CapacityView } from '../components/console/CapacityView'

/**
 * CapacityPage — the Capacity Planner as its own demo, split from the agent
 * console. Served standalone at /planner (and as the root experience on the
 * capacity.* hostname); shares the backend with the console — the e2e mode
 * drives the same orchestrator in-process.
 */
export function CapacityPage() {
  const host = window.location.hostname
  const onCapacityHost = host.startsWith('capacity.')
  // Cross-link to the console demo: sibling hostname in production, root in dev.
  const consoleHref = onCapacityHost
    ? `${window.location.protocol}//${host.replace(/^capacity\./, 'agents.')}`
    : '/'

  return (
    <div className="min-h-screen flex flex-col">
      <div className="sticky top-0 z-20 flex items-center gap-2.5 px-6 py-3 border-b backdrop-blur"
        style={{ borderColor: 'var(--line-soft)', background: 'color-mix(in srgb, var(--ink) 92%, transparent)' }}>
        <span className="w-[7px] h-[7px] rounded-full flex-none"
          style={{ background: 'var(--t2)', boxShadow: '0 0 0 4px rgba(84,210,166,.16)' }} />
        <h1 className="font-display font-bold text-[16px] tracking-[-0.02em] m-0">
          Capacity Planner
        </h1>
        <span className="font-code text-[11px] text-[var(--faint)]">
          agent workload benchmark · tiles · SLOs · history
        </span>
        <a href={consoleHref}
          className="ml-auto text-[12px] px-3 py-1.5 rounded-[9px] border no-underline text-[var(--muted)] hover:text-[var(--text)] transition-colors"
          style={{ background: 'var(--elev)', borderColor: 'var(--line)' }}>
          Agent console ↗
        </a>
      </div>

      <div className="flex-1 overflow-y-auto px-7 pt-6">
        <CapacityView />
      </div>
    </div>
  )
}
