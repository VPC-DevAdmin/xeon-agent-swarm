import type { ReactNode } from 'react'

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    active: 'bg-green-950 text-green-300 border-green-800',
    paused: 'bg-amber-950 text-amber-300 border-amber-800',
    archived: 'bg-gray-800 text-gray-400 border-gray-700',
    completed: 'bg-green-950 text-green-300 border-green-800',
    running: 'bg-blue-950 text-blue-300 border-blue-800',
    orchestrating: 'bg-blue-950 text-blue-300 border-blue-800',
    reducing: 'bg-blue-950 text-blue-300 border-blue-800',
    pending: 'bg-gray-800 text-gray-400 border-gray-700',
    failed: 'bg-red-950 text-red-300 border-red-800',
    killed: 'bg-red-950 text-red-300 border-red-800',
    revoked: 'bg-red-950 text-red-300 border-red-800',
    expired: 'bg-amber-950 text-amber-300 border-amber-800',
  }
  const cls = map[status] ?? 'bg-gray-800 text-gray-400 border-gray-700'
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-[11px] font-medium border ${cls}`}>
      {status}
    </span>
  )
}

export function Button({
  children, onClick, variant = 'default', type = 'button', disabled,
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'primary' | 'danger' | 'ghost'
  type?: 'button' | 'submit'
  disabled?: boolean
}) {
  const variants = {
    default: 'bg-gray-800 hover:bg-gray-700 text-gray-200 border-gray-700',
    primary: 'bg-blue-700 hover:bg-blue-600 text-white border-blue-600',
    danger: 'bg-red-900 hover:bg-red-800 text-red-100 border-red-800',
    ghost: 'bg-transparent hover:bg-gray-800 text-gray-400 border-transparent',
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`px-3 py-1.5 rounded text-xs font-medium border transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${variants[variant]}`}
    >
      {children}
    </button>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`bg-gray-900 border border-gray-800 rounded-lg ${className}`}>
      {children}
    </div>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs text-gray-400 mb-1">{label}</span>
      {children}
    </label>
  )
}

export const inputClass =
  'w-full bg-gray-950 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 ' +
  'focus:outline-none focus:border-blue-600 placeholder-gray-600'

export function timeAgo(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso).getTime()
  const diff = Date.now() - d
  const s = Math.floor(diff / 1000)
  if (s < 0) return formatFuture(-s)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function formatFuture(s: number): string {
  if (s < 60) return `in ${s}s`
  if (s < 3600) return `in ${Math.floor(s / 60)}m`
  if (s < 86400) return `in ${Math.floor(s / 3600)}h`
  return `in ${Math.floor(s / 86400)}d`
}

export function Empty({ message }: { message: string }) {
  return (
    <div className="text-center py-16 text-gray-600 text-sm">{message}</div>
  )
}
