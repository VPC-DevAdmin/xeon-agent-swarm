import { useState } from 'react'
import clsx from 'clsx'
import type { TaskSpec } from '../types/swarm'
import type { TaskMeta } from '../store/swarmStore'
import { useSwarmStore } from '../store/swarmStore'

const TYPE_COLORS: Record<string, string> = {
  research:      'bg-purple-900 text-purple-300 border-purple-700',
  analysis:      'bg-amber-900 text-amber-300 border-amber-700',
  code:          'bg-teal-900 text-teal-300 border-teal-700',
  summarization: 'bg-blue-900 text-blue-300 border-blue-700',
  vision:        'bg-pink-900 text-pink-300 border-pink-700',
  fact_check:    'bg-cyan-900 text-cyan-300 border-cyan-700',
  writing:       'bg-green-900 text-green-300 border-green-700',
  general:       'bg-gray-800 text-gray-300 border-gray-600',
}

const STATUS_COLORS: Record<string, string> = {
  pending:   'border-gray-700 bg-gray-900',
  running:   'border-blue-500 bg-gray-900 animate-pulse-border',
  completed: 'border-green-600 bg-gray-900',
  failed:    'border-red-600 bg-gray-900',
  killed:    'border-gray-600 bg-gray-900 opacity-60',
}

const STATUS_DOT: Record<string, string> = {
  pending:   'bg-gray-500',
  running:   'bg-blue-400 animate-pulse',
  completed: 'bg-green-400',
  failed:    'bg-red-400',
  killed:    'bg-gray-500',
}

function elapsed(meta: TaskMeta): string {
  if (!meta.startedAt) return ''
  const end = meta.completedAt ?? Date.now()
  const ms = end - meta.startedAt
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

interface Props {
  task: TaskSpec
}

export function WorkerCard({ task }: Props) {
  const [expanded, setExpanded] = useState(false)
  const status = useSwarmStore((s) => s.taskStatuses[task.id] ?? 'pending')
  const meta = useSwarmStore((s) => s.taskMeta[task.id])
  const result = useSwarmStore((s) => s.taskResults[task.id])
  const isValidating = useSwarmStore((s) => s.workerValidating[task.id] ?? false)
  const attemptNum = useSwarmStore((s) => s.workerAttempts[task.id] ?? 1)
  const correctionHint = useSwarmStore((s) => s.workerCorrections[task.id])
  const rejectedFinal = useSwarmStore((s) => s.workerRejectedFinal[task.id])

  const typeColor = TYPE_COLORS[task.type] ?? TYPE_COLORS.general

  // Determine effective border color (validator states override status colors)
  const borderClass = isValidating
    ? 'border-yellow-500 bg-gray-900'
    : rejectedFinal
    ? 'border-red-500 bg-gray-900'
    : attemptNum > 1 && status === 'running'
    ? 'border-orange-500 bg-gray-900 animate-pulse-border'
    : STATUS_COLORS[status]

  const taskDesc = task.objective || task.description

  return (
    <div
      className={clsx(
        'rounded-lg border p-3 transition-all duration-300',
        borderClass,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={clsx(
            'w-2 h-2 rounded-full flex-shrink-0',
            isValidating ? 'bg-yellow-400 animate-pulse' : STATUS_DOT[status],
          )} />
          <p className="text-sm text-gray-200 truncate">{taskDesc}</p>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <span className={clsx('text-xs px-1.5 py-0.5 rounded border font-mono', typeColor)}>
            {task.type}
          </span>
          {meta && (
            <span className={clsx(
              'text-xs px-1.5 py-0.5 rounded border',
              meta.hardware === 'gpu'
                ? 'bg-green-900 text-green-300 border-green-700'
                : 'bg-gray-800 text-gray-400 border-gray-600',
            )}>
              {meta.hardware?.toUpperCase()}
            </span>
          )}
        </div>
      </div>

      {meta && (
        <div className="mt-1.5 flex items-center gap-3 text-xs text-gray-500">
          <span className="truncate">{meta.model}</span>
          <span className="flex-shrink-0">{elapsed(meta)}</span>
          {result && (
            <span className="flex-shrink-0 text-gray-400">
              conf {(result.confidence * 100).toFixed(0)}%
            </span>
          )}
          {result?.tool_calls?.length > 0 && (
            <span className="flex-shrink-0 text-purple-400">
              tools: {result.tool_calls.join(', ')}
            </span>
          )}
        </div>
      )}

      {/* Validator status line */}
      {isValidating && (
        <div className="mt-1.5 flex items-center gap-1.5 text-xs text-yellow-400">
          <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />
          Validator checking contract…
        </div>
      )}
      {attemptNum > 1 && !isValidating && (
        <div className="mt-1.5 text-xs text-orange-400">
          Attempt {attemptNum}
          {correctionHint && (
            <span className="text-orange-500 ml-1">
              · Hint: {correctionHint.slice(0, 60)}{correctionHint.length > 60 ? '…' : ''}
            </span>
          )}
        </div>
      )}
      {rejectedFinal && (
        <div className="mt-1.5 text-xs text-red-400">
          ⚠ Committed with warnings ({rejectedFinal})
        </div>
      )}

      {result && (
        <>
          <button
            onClick={() => setExpanded(!expanded)}
            className="mt-2 text-xs text-blue-400 hover:text-blue-300 transition-colors"
          >
            {expanded ? '▲ hide result' : '▼ show result'}
          </button>
          {expanded && (
            <div className="mt-2 text-xs text-gray-300 bg-gray-950 rounded p-2 max-h-40 overflow-y-auto whitespace-pre-wrap">
              {result.result}
            </div>
          )}
        </>
      )}
    </div>
  )
}
