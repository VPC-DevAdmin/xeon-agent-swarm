import { create } from 'zustand'
import type {
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
}

interface SwarmStore {
  // Run
  runId: string | null
  query: string
  isRunning: boolean

  // Swarm pipeline
  taskGraph: TaskGraph | null
  taskStatuses: Record<string, TaskStatus>
  taskMeta: Record<string, TaskMeta>
  taskResults: Record<string, AgentResult>
  synthesizing: boolean
  finalAnswer: string | null
  swarmLatencyMs: number | null
  swarmTaskCount: number | null

  // Intelligence report document
  document: DocumentResult | null

  // Single-model A/B panel
  singleTokens: string
  singleModel: string
  singleHardware: string
  singleCompleted: boolean
  singleLatencyMs: number | null

  // Context rot metrics
  singleChunksRetrieved: number
  singleChunksIncluded: number
  singleChunksCited: number
  singleTokenEstimate: number
  singleRotScore: number | null

  // Actions
  startRun: (runId: string, query: string) => void
  dispatch: (event: SwarmEvent) => void
  reset: () => void
}

const initialState = {
  runId: null,
  query: '',
  isRunning: false,
  taskGraph: null,
  taskStatuses: {},
  taskMeta: {},
  taskResults: {},
  synthesizing: false,
  finalAnswer: null,
  swarmLatencyMs: null,
  swarmTaskCount: null,
  document: null,
  singleTokens: '',
  singleModel: '',
  singleHardware: '',
  singleCompleted: false,
  singleLatencyMs: null,
  singleChunksRetrieved: 0,
  singleChunksIncluded: 0,
  singleChunksCited: 0,
  singleTokenEstimate: 0,
  singleRotScore: null,
}

export const useSwarmStore = create<SwarmStore>((set, get) => ({
  ...initialState,

  startRun: (runId, query) => set({ ...initialState, runId, query, isRunning: true }),

  reset: () => set(initialState),

  dispatch: (event: SwarmEvent) => {
    const { payload } = event

    switch (event.event) {
      case 'graph_ready':
        set({ taskGraph: payload as unknown as TaskGraph })
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
            },
          },
        }))
        break

      case 'task_completed':
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
              confidence: payload.confidence as number,
              model_used: payload.model_used as string,
              hardware: payload.hardware as string,
              latency_ms: payload.latency_ms as number,
              tool_calls: (payload.tool_calls as string[]) || [],
            },
          },
        }))
        break

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

      case 'synthesis_started':
        set({ synthesizing: true })
        break

      case 'run_completed': {
        set({
          finalAnswer: payload.final_answer as string,
          swarmLatencyMs: payload.latency_ms as number,
          swarmTaskCount: payload.task_count as number,
          synthesizing: false,
          isRunning: false,
        })
        // Fetch full RunResult from REST API to get structured document
        const runId = get().runId
        if (runId) {
          fetch(`${API_BASE}/run/${runId}`)
            .then((r) => r.json())
            .then((data) => {
              if (data?.document) {
                set({ document: data.document as DocumentResult })
              }
            })
            .catch((err) => console.error('[swarm] fetch document failed:', err))
        }
        break
      }

      case 'single_started':
        set({
          singleModel: payload.model as string,
          singleHardware: payload.hardware as string,
          // Context rot: chunks available before LLM call
          singleChunksRetrieved: (payload.context_chunks_retrieved as number) || 0,
          singleChunksIncluded: (payload.context_chunks_included as number) || 0,
          singleTokenEstimate: (payload.context_token_estimate as number) || 0,
        })
        break

      case 'single_token':
        set((s) => ({ singleTokens: s.singleTokens + (payload.token as string) }))
        break

      case 'single_completed':
        set({
          singleCompleted: true,
          singleLatencyMs: payload.latency_ms as number,
          singleTokens: payload.answer as string,
          // Context rot metrics
          singleChunksRetrieved: (payload.context_chunks_retrieved as number) || 0,
          singleChunksIncluded: (payload.context_chunks_included as number) || 0,
          singleChunksCited: (payload.context_chunks_cited as number) || 0,
          singleTokenEstimate: (payload.context_token_estimate as number) || 0,
          singleRotScore: payload.context_rot_score != null
            ? (payload.context_rot_score as number)
            : null,
        })
        break

      case 'error':
        console.error('[swarm error]', payload.error)
        set({ isRunning: false, synthesizing: false })
        break
    }
  },
}))
