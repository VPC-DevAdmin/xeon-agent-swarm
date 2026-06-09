import { NavLink } from 'react-router-dom'

const TABS = [
  { to: '/', label: 'Live Run', end: true },
  { to: '/jobs', label: 'Jobs' },
  { to: '/runs', label: 'Runs' },
  { to: '/connectors', label: 'Connectors' },
]

export function NavBar() {
  return (
    <div className="sticky top-0 z-30 h-12 bg-gray-950/95 backdrop-blur border-b border-gray-800 px-6 flex items-center gap-6">
      <span className="text-xs font-semibold text-blue-400 uppercase tracking-widest shrink-0">
        Agent Orchestrator
      </span>
      <nav className="flex items-center gap-1">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            className={({ isActive }) =>
              `px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                isActive
                  ? 'bg-gray-800 text-white'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'
              }`
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
