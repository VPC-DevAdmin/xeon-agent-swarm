// ThreadModel — one renderable shape for a run, whether it is streaming live
// over the WebSocket (swarmStore) or loaded from the durable REST record.
// The console thread view renders this and nothing else.

import type { RunDetail } from '../api/types'
import type { AgentResult, RunMetrics, TaskStatus } from '../types/swarm'
import type { TaskMeta } from '../store/swarmStore'

export type TaskState =
  | 'queued'
  | 'running'
  | 'validating'
  | 'retrying'
  | 'done'
  | 'degraded'
  | 'failed'

export type ThreadPhase =
  | 'planning'
  | 'awaiting_approval'
  | 'executing'
  | 'synthesizing'
  | 'done'
  | 'failed'
  | 'aborted'

export interface ThreadTask {
  id: string
  name: string
  role: string
  tier: string | null
  category: string | null
  state: TaskState
  attempts: number
  hint: string | null
  tokensOut: number
}

export interface ThreadModel {
  runId: string
  prompt: string
  phase: ThreadPhase
  live: boolean // true when driven by this session's WebSocket
  plan: string[]
  tasks: ThreadTask[]
  answer: string | null
  metrics: RunMetrics | null
  error: string | null
}

// ── tier helpers ──────────────────────────────────────────────────────────────

export const TIER_ORDER = ['T1', 'T2', 'T3', 'T4', 'T5'] as const

export function tierColor(tier: string | null | undefined): string {
  switch (tier) {
    case 'T1': return 'var(--t1)'
    case 'T2': return 'var(--t2)'
    case 'T3': return 'var(--t3)'
    case 'T4': return 'var(--t4)'
    case 'T5': return 'var(--t5)'
    default: return 'var(--muted)'
  }
}

/** Split a numbered-list plan string into displayable task lines. */
export function planToTasks(plan: string | null | undefined): string[] {
  if (!plan) return []
  return plan
    .split(/\n+/)
    .map((line) => line.replace(/^\s*(?:\d+[.)]|[-*•])\s*/, '').trim())
    .filter((line) => line.length > 0)
}

// ── live thread (from swarmStore state) ───────────────────────────────────────

export interface LiveSnapshot {
  runId: string
  query: string
  isRunning: boolean
  runCompleted: boolean
  synthesizing: boolean
  awaitingApproval: boolean
  approvalPlan: string | null
  taskStatuses: Record<string, TaskStatus>
  taskMeta: Record<string, TaskMeta>
  taskResults: Record<string, AgentResult>
  workerValidating: Record<string, boolean>
  workerAttempts: Record<string, number>
  workerCorrections: Record<string, string>
  runMetrics: RunMetrics | null
  finalAnswer: string | null
}

function liveTaskState(
  status: TaskStatus,
  validating: boolean,
  attempts: number,
  result: AgentResult | undefined,
): TaskState {
  if (validating) return 'validating'
  if (status === 'running') return attempts > 1 ? 'retrying' : 'running'
  if (status === 'completed') return result?.verdict === 'degraded' ? 'degraded' : 'done'
  if (status === 'failed' || status === 'killed') return 'failed'
  return 'queued'
}

export function buildLiveThread(s: LiveSnapshot): ThreadModel {
  const ids = Object.keys(s.taskMeta)
  const tasks: ThreadTask[] = ids.map((id) => {
    const meta = s.taskMeta[id]
    const result = s.taskResults[id]
    const attempts = s.workerAttempts[id] ?? 1
    return {
      id,
      name: meta.description || id,
      role: meta.type || 'general',
      tier: result?.tier_observed ?? null,
      category: result?.category ?? null,
      state: liveTaskState(s.taskStatuses[id] ?? 'pending', s.workerValidating[id] ?? false,
        attempts, result),
      attempts,
      hint: s.workerCorrections[id] ?? null,
      tokensOut: result?.tokens_out ?? 0,
    }
  })

  let phase: ThreadPhase = 'planning'
  if (s.awaitingApproval) phase = 'awaiting_approval'
  else if (s.runCompleted) phase = 'done'
  else if (s.synthesizing) phase = 'synthesizing'
  else if (tasks.length > 0) phase = 'executing'

  return {
    runId: s.runId,
    prompt: s.query,
    phase,
    live: true,
    plan: planToTasks(s.approvalPlan),
    tasks,
    answer: s.finalAnswer,
    metrics: s.runMetrics,
    error: null,
  }
}

// ── historical thread (from the durable REST record) ─────────────────────────

function detailPhase(status: string): ThreadPhase {
  switch (status) {
    case 'completed': return 'done'
    case 'failed': return 'failed'
    case 'aborted':
    case 'killed': return 'aborted'
    case 'awaiting_approval': return 'awaiting_approval'
    case 'pending': return 'planning'
    default: return 'executing'
  }
}

export function buildDetailThread(d: RunDetail): ThreadModel {
  const steps = d.steps.filter((s) => s.step_key !== 'orchestrator')
  const tasks: ThreadTask[] = steps.map((s) => {
    const tier = s.attempts.filter((a) => a.tier_observed).slice(-1)[0]?.tier_observed ?? null
    const category = s.attempts.filter((a) => a.category).slice(-1)[0]?.category ?? null
    const lastVal = (s.validations ?? []).slice(-1)[0]
    let state: TaskState
    if (s.status === 'completed') state = lastVal?.verdict === 'degraded' ? 'degraded' : 'done'
    else if (s.status === 'failed' || s.status === 'killed') state = 'failed'
    else if (s.status === 'running' || s.status === 'validating' || s.status === 'retrying')
      state = s.status as TaskState
    else state = 'queued'
    const hint = s.attempts.filter((a) => a.correction_hint).slice(-1)[0]?.correction_hint ?? null
    const tokensOut = s.attempts.reduce((sum, a) => sum + (a.tokens_out ?? 0), 0)
    return {
      id: s.step_key,
      name: s.objective || s.step_key,
      role: s.type,
      tier,
      category,
      state,
      attempts: s.total_attempts,
      hint,
      tokensOut,
    }
  })

  const m = (d.metrics ?? null) as RunMetrics | null

  return {
    runId: d.run_id,
    prompt: d.query,
    phase: detailPhase(d.status),
    live: false,
    plan: planToTasks((d.task_graph?.plan as string) ?? null),
    tasks,
    answer: ((d.document as Record<string, unknown> | null)?.final_answer as string) || null,
    metrics: m && m.tier_calls ? m : null,
    error: d.error ?? null,
  }
}

// ── sidebar grouping ──────────────────────────────────────────────────────────

export function dayGroup(iso: string | null | undefined): string {
  if (!iso) return 'Earlier'
  const d = new Date(iso)
  const now = new Date()
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diffDays = Math.floor((startOfDay(now) - startOfDay(d)) / 86_400_000)
  if (diffDays <= 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 7) return 'This week'
  return 'Earlier'
}
