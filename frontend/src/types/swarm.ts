// TypeScript mirrors of backend Pydantic schemas

export type TaskType = 'research' | 'analysis' | 'code' | 'summarization' | 'general'
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface TaskSpec {
  id: string
  description: string
  type: TaskType
  dependencies: string[]
  priority: number
}

export interface TaskGraph {
  query: string
  tasks: TaskSpec[]
  reasoning: string
}

export interface AgentResult {
  task_id: string
  status: TaskStatus
  result: string
  confidence: number
  model_used: string
  hardware: string
  latency_ms: number
  tool_calls: string[]
}

export interface SwarmState {
  run_id: string
  query: string
  task_graph: TaskGraph | null
  results: Record<string, AgentResult>
  final_answer: string | null
  status: TaskStatus
  started_at: string
  completed_at: string | null
}

export interface SingleModelResult {
  run_id: string
  query: string
  answer: string
  model_used: string
  hardware: string
  latency_ms: number
  status: TaskStatus
}

export interface RunResult {
  run_id: string
  swarm: SwarmState
  single_model: SingleModelResult | null
}

// ── WebSocket events ──────────────────────────────────────────────────────────

export type EventType =
  | 'run_started'
  | 'graph_ready'
  | 'task_started'
  | 'task_completed'
  | 'task_failed'
  | 'synthesis_started'
  | 'run_completed'
  | 'single_started'
  | 'single_token'
  | 'single_completed'
  | 'error'

export interface SwarmEvent {
  event: EventType
  run_id: string
  payload: Record<string, unknown>
  timestamp: string
}
