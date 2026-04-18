import { create } from 'zustand'
import type {
  Artifact,
  TaskGraph,
  TaskStatus,
  AgentResult,
  SwarmEvent,
  DocumentResult,
} from '../types/swarm'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export interface TaskMeta {
  description: string
  type: string
  model: string
  hardware: string
  startedAt: number | null
  completedAt: number | null
  killed: boolean
}

// Demo pacing: minimum time each stage stays visible before auto-advancing.
// The flow canvas reads these to gate transitions even when compute finishes fast.
export const PACING = {
  ORCHESTRATOR_DWELL_MS: 8_000,   // hold orchestrator card visible for ≥ 8s
  WORKER_SPAWN_STAGGER_MS: 120,   // 120ms between each worker card appearing
  WORKER_MIN_DWELL_MS: 3_000,     // don't show a worker as done in <3s
  FACT_CHECK_DWELL_MS: 4_000,
  SYNTHESIS_DWELL_MS: 3_000,
}

interface SwarmStore {
  // Run lifecycle
  runId: string | null
  query: string
  isRunning: boolean
  synthesizing: boolean
  runCompleted: boolean
  swarmLatencyMs: number | null

  // Graph + tasks
  taskGraph: TaskGraph | null
  taskStatuses: Record<string, TaskStatus>
  taskMeta: Record<string, TaskMeta>
  taskResults: Record<string, AgentResult>

  // Typed artifacts collected from all workers (live, as tasks complete)
  artifacts: Artifact[]

  // Final structured document (fetched after run_completed)
  document: DocumentResult | null

  // Active demo stage for narrative cards and flow canvas
  // orchestrating → working → fact_checking → synthesizing → done
  demoStage: 'idle' | 'orchestrating' | 'working' | 'fact_checking' | 'synthesizing' | 'done'

  // Actions
  startRun: (runId: string, query: string) => void
  killTask: (taskId: string) => void
  dispatch: (event: SwarmEvent) => void
  reset: () => void
}

const initialState = {
  runId: null,
  query: '',
  isRunning: false,
  synthesizing: false,
  runCompleted: false,
  swarmLatencyMs: null,
  taskGraph: null,
  taskStatuses: {},
  taskMeta: {},
  taskResults: {},
  artifacts: [],
  document: null,
  demoStage: 'idle' as const,
}

export const useSwarmStore = create<SwarmStore>((set, get) => ({
  ...initialState,

  startRun: (runId, query) =>
    set({ ...initialState, runId, query, isRunning: true, demoStage: 'orchestrating' }),

  reset: () => set(initialState),

  killTask: (taskId: string) => {
    const { runId } = get()
    if (!runId) return
    // Optimistic UI update
    set((s) => ({
      taskStatuses: { ...s.taskStatuses, [taskId]: 'killed' },
      taskMeta: {
        ...s.taskMeta,
        [taskId]: { ...s.taskMeta[taskId], killed: true, completedAt: Date.now() },
      },
    }))
    // Inform the backend (best-effort; backend may not support it yet)
    fetch(`${API_BASE}/run/${runId}/kill`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    }).catch(() => {/* ignore — kill is UI-side for now */})
  },

  dispatch: (event: SwarmEvent) => {
    const { payload } = event

    switch (event.event) {
      case 'graph_ready':
        set({ taskGraph: payload as unknown as TaskGraph, demoStage: 'working' })
        break

      case 'task_started':
        set((s) => ({
          taskStatuses: { ...s.taskStatuses, [payload.task_id as string]: 'running' },
          taskMeta: {
            ...s.taskMeta,
            [payload.task_id as string]: {
              description: payload.description as string,
              type: payload.type as string,
              model: payload.model as string,
              hardware: payload.hardware as string,
              startedAt: Date.now(),
              completedAt: null,
              killed: false,
            },
          },
        }))
        break

      case 'task_completed': {
        const taskArtifacts = (payload.artifacts as Artifact[] | undefined) ?? []
        set((s) => ({
          taskStatuses: { ...s.taskStatuses, [payload.task_id as string]: 'completed' },
          taskMeta: {
            ...s.taskMeta,
            [payload.task_id as string]: {
              ...s.taskMeta[payload.task_id as string],
              completedAt: Date.now(),
            },
          },
          taskResults: {
            ...s.taskResults,
            [payload.task_id as string]: {
              task_id: payload.task_id as string,
              status: 'completed',
              result: payload.result as string,
              artifacts: taskArtifacts,
              confidence: payload.confidence as number,
              model_used: payload.model_used as string,
              hardware: payload.hardware as string,
              latency_ms: payload.latency_ms as number,
              tool_calls: (payload.tool_calls as string[]) || [],
            },
          },
          // Collect artifacts from all non-writing workers into the global list
          artifacts: [
            ...s.artifacts,
            ...taskArtifacts.filter((a) => a.type !== 'prose'),
          ],
        }))
        break
      }

      case 'task_failed':
        set((s) => ({
          taskStatuses: { ...s.taskStatuses, [payload.task_id as string]: 'failed' },
          taskMeta: {
            ...s.taskMeta,
            [payload.task_id as string]: {
              ...s.taskMeta[payload.task_id as string],
              completedAt: Date.now(),
            },
          },
        }))
        break

      case 'task_killed':
        set((s) => ({
          taskStatuses: { ...s.taskStatuses, [payload.task_id as string]: 'killed' },
          taskMeta: {
            ...s.taskMeta,
            [payload.task_id as string]: {
              ...s.taskMeta[payload.task_id as string],
              killed: true,
              completedAt: Date.now(),
            },
          },
        }))
        break

      case 'synthesis_started':
        set({ synthesizing: true, demoStage: 'synthesizing' })
        break

      case 'run_completed': {
        set({
          swarmLatencyMs: payload.latency_ms as number,
          synthesizing: false,
          isRunning: false,
          runCompleted: true,
          demoStage: 'done',
        })
        const runId = get().runId
        if (runId) {
          fetch(`${API_BASE}/run/${runId}`)
            .then((r) => r.json())
            .then((data) => {
              if (data?.document) set({ document: data.document as DocumentResult })
            })
            .catch((err) => console.error('[swarm] fetch document failed:', err))
        }
        break
      }

      case 'error': {
        const msg = payload.error as string
        console.error('[swarm error]', msg)
        set({ isRunning: false, synthesizing: false })
        break
      }
    }
  },
}))
