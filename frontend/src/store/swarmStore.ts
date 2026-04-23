import { create } from 'zustand'
import type {
  Artifact,
  TaskGraph,
  TaskStatus,
  AgentResult,
  SwarmEvent,
  DocumentResult,
  RunMetrics,
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

  // Live streaming text per task (populated by task_token events from the writing worker)
  taskStreams: Record<string, string>

  // Final structured document (fetched after run_completed)
  document: DocumentResult | null

  // Active demo stage for narrative cards and flow canvas
  // orchestrating → working → fact_checking → synthesizing → done
  demoStage: 'idle' | 'orchestrating' | 'working' | 'fact_checking' | 'synthesizing' | 'done'

  // ── Validator feature ──────────────────────────────────────────────────────
  validatorEnabled: boolean
  // Per-task validator state
  workerAttempts: Record<string, number>          // task_id → current attempt number
  workerCorrections: Record<string, string>       // task_id → latest correction hint
  workerValidating: Record<string, boolean>       // task_id → validator is checking
  workerRejectedFinal: Record<string, string>     // task_id → rejection reason
  // Run-level metrics (received at end of run)
  runMetrics: RunMetrics | null

  // ── Legacy A/B single-model state (used when ENABLE_AB_COMPARISON=1) ───────
  // Kept so ABPanel / ContextRotPanel / TimingBar continue to compile.
  finalAnswer: string | null
  singleTokens: string
  singleModel: string
  singleHardware: string
  singleCompleted: boolean
  singleLatencyMs: number | null
  singleError: string | null
  singleRetrying: boolean
  singleRetryInfo: {
    requestedTokens: number
    limitTokens: number
    originalChunks: number
    retryTopK: number
  } | null
  singleChunksRetrieved: number
  singleChunksIncluded: number
  singleChunksCited: number
  singleTokenEstimate: number
  singleRotScore: number | null

  // Actions
  startRun: (runId: string, query: string) => void
  setValidatorEnabled: (enabled: boolean) => void
  killTask: (taskId: string) => void
  retryTask: (taskId: string) => void
  retryRun: () => Promise<void>
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
  taskStreams: {},
  document: null,
  demoStage: 'idle' as const,
  // Validator
  validatorEnabled: true,
  workerAttempts: {},
  workerCorrections: {},
  workerValidating: {},
  workerRejectedFinal: {},
  runMetrics: null,
  // Legacy A/B fields (zeroed out; populated only when ENABLE_AB_COMPARISON=1)
  finalAnswer: null,
  singleTokens: '',
  singleModel: '',
  singleHardware: '',
  singleCompleted: false,
  singleLatencyMs: null,
  singleError: null,
  singleRetrying: false,
  singleRetryInfo: null,
  singleChunksRetrieved: 0,
  singleChunksIncluded: 0,
  singleChunksCited: 0,
  singleTokenEstimate: 0,
  singleRotScore: null,
}

export const useSwarmStore = create<SwarmStore>((set, get) => ({
  ...initialState,

  startRun: (runId, query) =>
    set((s) => ({
      ...initialState,
      // Preserve validator toggle across runs
      validatorEnabled: s.validatorEnabled,
      runId,
      query,
      isRunning: true,
      demoStage: 'orchestrating' as const,
    })),

  setValidatorEnabled: (enabled) => set({ validatorEnabled: enabled }),

  reset: () => set(initialState),

  killTask: (taskId: string) => {
    const { runId } = get()
    if (!runId) return
    // Optimistic UI update — the backend confirms via task_killed WS event
    set((s) => ({
      taskStatuses: { ...s.taskStatuses, [taskId]: 'killed' },
      taskMeta: {
        ...s.taskMeta,
        [taskId]: { ...s.taskMeta[taskId], killed: true, completedAt: Date.now() },
      },
    }))
    fetch(`${API_BASE}/run/${runId}/kill`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    }).catch(() => {/* network failure — optimistic state stands */})
  },

  retryTask: (taskId: string) => {
    const { runId } = get()
    if (!runId) return
    // Optimistic: reset the task to running so the card shows activity immediately
    set((s) => ({
      taskStatuses: { ...s.taskStatuses, [taskId]: 'running' },
      taskMeta: {
        ...s.taskMeta,
        [taskId]: {
          ...s.taskMeta[taskId],
          killed: false,
          startedAt: Date.now(),
          completedAt: null,
        },
      },
      // Remove stale result so the mini-preview clears
      taskResults: Object.fromEntries(
        Object.entries(s.taskResults).filter(([k]) => k !== taskId)
      ),
    }))
    fetch(`${API_BASE}/run/${runId}/retry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    }).catch(() => {/* fall through — WS events will update state if it worked */})
  },

  retryRun: async () => {
    const { query, reset, startRun } = get()
    if (!query.trim()) return
    reset()
    try {
      const resp = await fetch(`${API_BASE}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim() }),
      })
      if (!resp.ok) return
      const data = await resp.json()
      startRun(data.run_id as string, query)
    } catch {/* ignore */}
  },

  dispatch: (event: SwarmEvent) => {
    const { payload } = event

    switch (event.event) {
      case 'graph_ready':
        set({ taskGraph: payload as unknown as TaskGraph, demoStage: 'working' })
        break

      case 'task_started': {
        const isFactCheck = (payload.type as string) === 'fact_check'
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
          // Advance to fact_checking stage when the first fact_check task starts
          demoStage: isFactCheck && s.demoStage === 'working' ? 'fact_checking' : s.demoStage,
        }))
        break
      }

      case 'task_token': {
        // Append streaming token to taskStreams for the writing worker's live preview
        const { task_id: streamTaskId, token } = payload as { task_id: string; token: string }
        set((s) => ({
          taskStreams: {
            ...s.taskStreams,
            [streamTaskId]: (s.taskStreams[streamTaskId] ?? '') + token,
          },
        }))
        break
      }

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

      // ── Validator events ─────────────────────────────────────────────────────

      case 'validator_started': {
        const vsTaskId = payload.task_id as string
        set((s) => ({
          workerValidating: { ...s.workerValidating, [vsTaskId]: true },
        }))
        break
      }

      case 'validator_approved': {
        const vaTaskId = payload.task_id as string
        set((s) => ({
          workerValidating: { ...s.workerValidating, [vaTaskId]: false },
        }))
        break
      }

      case 'validator_rejected': {
        const vrTaskId = payload.task_id as string
        const hint = payload.correction_hint as string
        set((s) => ({
          workerValidating: { ...s.workerValidating, [vrTaskId]: false },
          workerCorrections: { ...s.workerCorrections, [vrTaskId]: hint },
        }))
        break
      }

      case 'worker_retrying': {
        const wrTaskId = payload.task_id as string
        const nextAttempt = payload.next_attempt as number
        const retryHint = payload.correction_hint as string
        set((s) => ({
          taskStatuses: { ...s.taskStatuses, [wrTaskId]: 'running' },
          workerAttempts: { ...s.workerAttempts, [wrTaskId]: nextAttempt },
          workerCorrections: { ...s.workerCorrections, [wrTaskId]: retryHint },
        }))
        break
      }

      case 'worker_rejected_final': {
        const wfTaskId = payload.task_id as string
        const reason = payload.reason as string
        set((s) => ({
          workerRejectedFinal: { ...s.workerRejectedFinal, [wfTaskId]: reason },
          workerValidating: { ...s.workerValidating, [wfTaskId]: false },
        }))
        break
      }

      case 'run_metrics': {
        set({ runMetrics: payload as unknown as RunMetrics })
        break
      }

      case 'tts_started':
      case 'tts_completed':
        // No state change needed — handled by document fetch on run_completed
        break

      case 'synthesis_started':
        set({ synthesizing: true, demoStage: 'synthesizing' })
        break

      case 'run_completed': {
        const fa = payload.final_answer as string | undefined
        set({
          swarmLatencyMs: payload.latency_ms as number,
          synthesizing: false,
          isRunning: false,
          runCompleted: true,
          demoStage: 'done',
          finalAnswer: fa ?? null,
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

      // ── Legacy A/B single-model events (ENABLE_AB_COMPARISON=1) ─────────────

      case 'single_started':
        set({
          singleTokens: '',
          singleModel: payload.model as string,
          singleHardware: payload.hardware as string,
          singleCompleted: false,
          singleLatencyMs: null,
          singleError: null,
          singleRetrying: false,
          singleRetryInfo: null,
        })
        break

      case 'single_token':
        set((s) => ({ singleTokens: s.singleTokens + (payload.token as string) }))
        break

      case 'single_retrying':
        set({
          singleRetrying: true,
          singleTokens: '',
          singleRetryInfo: {
            requestedTokens: payload.requested_tokens as number,
            limitTokens: payload.limit_tokens as number,
            originalChunks: payload.original_chunks as number,
            retryTopK: payload.retry_top_k as number,
          },
        })
        break

      case 'single_completed':
        set({
          singleCompleted: true,
          singleLatencyMs: payload.latency_ms as number,
          singleRetrying: false,
          singleChunksRetrieved: (payload.chunks_retrieved as number) ?? 0,
          singleChunksIncluded: (payload.chunks_included as number) ?? 0,
          singleChunksCited: (payload.chunks_cited as number) ?? 0,
          singleTokenEstimate: (payload.token_estimate as number) ?? 0,
          singleRotScore: (payload.rot_score as number) ?? null,
        })
        break

      case 'error': {
        const msg = payload.error as string
        console.error('[swarm error]', msg)
        set({ isRunning: false, synthesizing: false })
        break
      }
    }
  },
}))
