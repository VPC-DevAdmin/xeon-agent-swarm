/**
 * WorkerGrid — animated 2-column grid of worker cards.
 * Workers spawn with staggered timing to communicate parallelism visually.
 * Completing workers show a mini artifact preview inside their card.
 */
import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { useSwarmStore, PACING } from '../store/swarmStore'
import type { TaskSpec } from '../types/swarm'

// ── Role colours ──────────────────────────────────────────────────────────────

const ROLE_STYLE: Record<string, { bg: string; border: string; badge: string; icon: string }> = {
  research:     { bg: 'bg-blue-950/40',   border: 'border-blue-800',   badge: 'bg-blue-900 text-blue-200',   icon: '🔬' },
  analysis:     { bg: 'bg-purple-950/40', border: 'border-purple-800', badge: 'bg-purple-900 text-purple-200', icon: '📊' },
  code:         { bg: 'bg-cyan-950/40',   border: 'border-cyan-800',   badge: 'bg-cyan-900 text-cyan-200',   icon: '💻' },
  vision:       { bg: 'bg-pink-950/40',   border: 'border-pink-800',   badge: 'bg-pink-900 text-pink-200',   icon: '👁️' },
  fact_check:   { bg: 'bg-amber-950/40',  border: 'border-amber-800',  badge: 'bg-amber-900 text-amber-200', icon: '🔍' },
  writing:      { bg: 'bg-green-950/40',  border: 'border-green-800',  badge: 'bg-green-900 text-green-200', icon: '✍️' },
  summarization:{ bg: 'bg-teal-950/40',   border: 'border-teal-800',   badge: 'bg-teal-900 text-teal-200',   icon: '📝' },
  general:      { bg: 'bg-gray-900/50',   border: 'border-gray-700',   badge: 'bg-gray-800 text-gray-300',   icon: '⚙️' },
}

const DEFAULT_STYLE = ROLE_STYLE.general

// ── Artifact mini-preview ─────────────────────────────────────────────────────

function ArtifactPreview({ taskId }: { taskId: string }) {
  const result = useSwarmStore((s) => s.taskResults[taskId])
  if (!result?.artifacts?.length) return null

  const art = result.artifacts[0]
  if (!art) return null

  const previewMap: Record<string, () => React.ReactNode> = {
    table: () => {
      const c = art.content as { headers?: string[]; rows?: string[][] }
      const headers = c.headers ?? []
      return (
        <div className="text-[9px] text-gray-400 mt-1 overflow-hidden">
          <div className="flex gap-2 font-semibold text-gray-300 border-b border-gray-700 pb-0.5 mb-0.5">
            {headers.slice(0, 3).map((h) => <span key={h} className="truncate flex-1">{h}</span>)}
          </div>
          {(c.rows ?? []).slice(0, 2).map((row, i) => (
            <div key={i} className="flex gap-2">
              {row.slice(0, 3).map((cell, j) => <span key={j} className="truncate flex-1">{cell}</span>)}
            </div>
          ))}
        </div>
      )
    },
    diagram: () => (
      <div className="text-[9px] text-cyan-400 mt-1 font-mono truncate">
        {String((art.content as { mermaid?: string }).mermaid ?? '').split('\n')[0]}…
      </div>
    ),
    code: () => (
      <div className="text-[9px] text-gray-400 mt-1 font-mono truncate">
        <span className="text-cyan-500">{(art.content as { language?: string }).language}</span>{' '}
        {(art.content as { description?: string }).description}
      </div>
    ),
    citation_set: () => {
      const citations = ((art.content as { citations?: Array<{ title: string }> }).citations ?? [])
      return (
        <div className="text-[9px] text-gray-400 mt-1 space-y-0.5">
          {citations.slice(0, 2).map((c, i) => (
            <div key={i} className="truncate">📎 {c.title}</div>
          ))}
        </div>
      )
    },
    chart: () => {
      const series = ((art.content as { series?: Array<{ name: string; data: Array<{ x: unknown; y: number }> }> }).series ?? [])
      const caption = (art.content as { caption?: string }).caption
      const pts = series[0]?.data ?? []
      return (
        <div className="text-[9px] text-gray-400 mt-1">
          {caption && <div className="text-indigo-400 truncate mb-0.5">📈 {caption}</div>}
          <div className="flex items-end gap-0.5 h-5">
            {pts.slice(0, 8).map((pt, i) => {
              const max = Math.max(...pts.map((p) => p.y), 1)
              const pct = Math.round((pt.y / max) * 100)
              return (
                <div
                  key={i}
                  className="bg-indigo-500/60 rounded-sm flex-1"
                  style={{ height: `${Math.max(pct, 8)}%` }}
                  title={`${pt.x}: ${pt.y}`}
                />
              )
            })}
          </div>
        </div>
      )
    },
    claim_verdict: () => {
      const v = (art.content as { verdict?: string; claim?: string }).verdict ?? 'uncertain'
      const colors = { supported: 'text-green-400', unsupported: 'text-red-400', uncertain: 'text-amber-400' }
      return (
        <div className={`text-[9px] mt-1 ${colors[v as keyof typeof colors] ?? 'text-gray-400'}`}>
          {v === 'supported' ? '✓' : v === 'unsupported' ? '✗' : '?'} {(art.content as { claim?: string }).claim?.slice(0, 60)}…
        </div>
      )
    },
    extracted_data: () => {
      const pts = ((art.content as { data_points?: Array<{ label: string; value: string; unit?: string }> }).data_points ?? [])
      return (
        <div className="text-[9px] text-gray-400 mt-1 space-y-0.5">
          {pts.slice(0, 2).map((p, i) => (
            <div key={i} className="truncate">{p.label}: <span className="text-white">{p.value}</span>{p.unit ? ` ${p.unit}` : ''}</div>
          ))}
        </div>
      )
    },
  }

  const render = previewMap[art.type]
  return render ? <>{render()}</> : null
}

// ── Worker card ───────────────────────────────────────────────────────────────

function WorkerCard({ task, index }: { task: TaskSpec; index: number }) {
  const meta       = useSwarmStore((s) => s.taskMeta[task.id])
  const status     = useSwarmStore((s) => s.taskStatuses[task.id] ?? 'pending')
  const killTask   = useSwarmStore((s) => s.killTask)
  const retryTask  = useSwarmStore((s) => s.retryTask)

  const style = ROLE_STYLE[task.type] ?? DEFAULT_STYLE

  const isRunning   = status === 'running'
  const isCompleted = status === 'completed'
  const isFailed    = status === 'failed'
  const isKilled    = status === 'killed'

  // Live elapsed timer — ticks every 100ms while the task is running
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!isRunning) return
    const id = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(id)
  }, [isRunning])

  const elapsed = meta?.startedAt
    ? meta.completedAt
      ? ((meta.completedAt - meta.startedAt) / 1000).toFixed(1)
      : ((now - meta.startedAt) / 1000).toFixed(1)
    : null

  const borderClass = isCompleted
    ? 'border-green-700'
    : isFailed
    ? 'border-red-800'
    : isKilled
    ? 'border-red-900'
    : isRunning
    ? style.border
    : 'border-gray-800'

  return (
    <motion.div
      initial={{ opacity: 0, y: 12, scale: 0.92 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.35, delay: index * (PACING.WORKER_SPAWN_STAGGER_MS / 1000), ease: [0.22, 1, 0.36, 1] }}
      className={`
        relative rounded-lg border p-3 text-xs
        ${style.bg} ${borderClass}
        ${isRunning ? 'shadow-lg shadow-blue-950' : ''}
        transition-colors duration-500
      `}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${style.badge}`}>
          {style.icon} {task.type.replace('_', ' ')}
        </span>
        <span className="ml-auto text-gray-600 text-[9px]">{meta?.model?.split('/').pop() ?? ''}</span>
      </div>

      {/* Description */}
      <div className="text-gray-300 leading-snug mb-1.5 line-clamp-2">{task.description}</div>

      {/* Status row */}
      <div className="flex items-center gap-2">
        {isRunning && (
          <motion.div
            animate={{ opacity: [1, 0.3, 1] }}
            transition={{ duration: 1.2, repeat: Infinity }}
            className="w-1.5 h-1.5 rounded-full bg-blue-400"
          />
        )}
        {isCompleted && <div className="w-1.5 h-1.5 rounded-full bg-green-400" />}
        {isFailed    && <div className="w-1.5 h-1.5 rounded-full bg-red-500" />}
        {isKilled    && <div className="w-1.5 h-1.5 rounded-full bg-red-900" />}

        <span className={`text-[10px] ${
          isCompleted ? 'text-green-400' :
          isFailed    ? 'text-red-400' :
          isKilled    ? 'text-red-600' :
          isRunning   ? 'text-blue-400' : 'text-gray-600'
        }`}>
          {isKilled ? 'killed' : status}
        </span>

        {elapsed && <span className="ml-auto text-gray-600 text-[10px]">{elapsed}s</span>}

        {/* Kill button — only shown while running */}
        {isRunning && (
          <button
            onClick={() => killTask(task.id)}
            className="ml-1 text-[9px] px-1.5 py-0.5 rounded border border-red-900 text-red-600 hover:bg-red-950 transition-colors"
          >
            Kill
          </button>
        )}
        {/* Retry button — shown on killed or failed workers */}
        {(isKilled || isFailed) && (
          <button
            onClick={() => retryTask(task.id)}
            className="ml-1 text-[9px] px-1.5 py-0.5 rounded border border-blue-900 text-blue-500 hover:bg-blue-950 transition-colors"
          >
            ↺ Retry
          </button>
        )}
      </div>

      {/* Artifact mini-preview */}
      {isCompleted && <ArtifactPreview taskId={task.id} />}

      {/* Killed overlay */}
      {isKilled && (
        <div className="absolute inset-0 rounded-lg bg-red-950/30 flex items-center justify-center">
          <span className="text-red-400 text-xs font-semibold">worker killed</span>
        </div>
      )}
    </motion.div>
  )
}

// ── Grid ──────────────────────────────────────────────────────────────────────

export function WorkerGrid() {
  const taskGraph = useSwarmStore((s) => s.taskGraph)
  if (!taskGraph) return null

  // Show all tasks except the writing task (it's the synthesizer stage)
  const workerTasks = taskGraph.tasks.filter((t) => t.type !== 'writing')
  const writingTask = taskGraph.tasks.find((t) => t.type === 'writing')

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        {workerTasks.map((task, i) => (
          <WorkerCard key={task.id} task={task} index={i} />
        ))}
      </div>

      {writingTask && (
        <WritingTaskRow task={writingTask} index={workerTasks.length} />
      )}
    </div>
  )
}

function WritingTaskRow({ task, index }: { task: TaskSpec; index: number }) {
  const status    = useSwarmStore((s) => s.taskStatuses[task.id] ?? 'pending')
  const stream    = useSwarmStore((s) => s.taskStreams[task.id] ?? '')
  const retryTask = useSwarmStore((s) => s.retryTask)

  const isRunning   = status === 'running'
  const isCompleted = status === 'completed'
  const isFailed    = status === 'failed'
  const isKilled    = status === 'killed'
  const isError     = isFailed || isKilled

  // Show the last ~280 chars of the stream so the user can see what's being written
  const streamTail = stream.length > 280 ? '…' + stream.slice(-280) : stream
  const charCount  = stream.length

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: index * (PACING.WORKER_SPAWN_STAGGER_MS / 1000) }}
      className={`rounded-lg border px-3 py-2 text-xs
        ${isCompleted ? 'border-green-700 bg-green-950/20'
          : isRunning  ? 'border-green-800 bg-green-950/10'
          : isError    ? 'border-red-800 bg-red-950/20'
          : 'border-gray-800 bg-gray-900/30'}
      `}
    >
      {/* Header row */}
      <div className="flex items-center gap-3">
        <span className="text-base">✍️</span>
        <span className={`flex-1 ${isError ? 'text-red-400' : 'text-gray-400'}`}>
          Synthesizer — {task.description}
        </span>

        {charCount > 0 && isRunning && (
          <span className="text-[9px] text-gray-600 tabular-nums">{charCount.toLocaleString()} chars</span>
        )}

        <span className={`text-[10px] font-semibold ${
          isCompleted ? 'text-green-400'
          : isRunning  ? 'text-green-600'
          : isError    ? 'text-red-400'
          : 'text-gray-600'
        }`}>
          {isCompleted ? 'report ready ↓'
            : isRunning ? 'drafting…'
            : isFailed  ? 'timed out'
            : isKilled  ? 'killed'
            : 'waiting'}
        </span>

        {isRunning && (
          <motion.div
            animate={{ opacity: [1, 0.2, 1] }}
            transition={{ duration: 1.2, repeat: Infinity }}
            className="w-1.5 h-1.5 rounded-full bg-green-400"
          />
        )}

        {isError && (
          <button
            onClick={() => retryTask(task.id)}
            className="ml-1 text-[9px] px-1.5 py-0.5 rounded border border-blue-900 text-blue-500 hover:bg-blue-950 transition-colors"
          >
            ↺ Retry
          </button>
        )}
      </div>

      {/* Live typewriter preview — shown while the writing worker is streaming */}
      {isRunning && streamTail && (
        <div className="mt-2 rounded bg-gray-950 border border-gray-800 p-2 font-mono text-[9px] leading-relaxed text-gray-500 overflow-hidden" style={{ maxHeight: 72 }}>
          <span>{streamTail}</span>
          <motion.span
            animate={{ opacity: [1, 0, 1] }}
            transition={{ duration: 0.8, repeat: Infinity }}
            className="text-green-500"
          >▌</motion.span>
        </div>
      )}
    </motion.div>
  )
}
