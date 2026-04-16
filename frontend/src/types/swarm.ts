// TypeScript mirrors of backend Pydantic schemas

export type TaskType =
  | 'research'
  | 'analysis'
  | 'code'
  | 'summarization'
  | 'vision'
  | 'fact_check'
  | 'writing'
  | 'general'

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

// ── Intelligence report (DocumentResult) ─────────────────────────────────────

export interface DocumentSection {
  title: string
  content: string
  sources: string[]
}

export interface CodeSnippet {
  language: string
  description: string
  code: string
}

export interface DocumentResult {
  title: string
  executive_summary: string
  sections: DocumentSection[]
  code_snippets: CodeSnippet[]
  key_findings: string[]
  sources: string[]
  diagram_mermaid: string | null
  tts_audio_url: string | null
}

// ── A/B comparison ────────────────────────────────────────────────────────────

export interface SingleModelResult {
  run_id: string
  query: string
  answer: string
  model_used: string
  hardware: string
  latency_ms: number
  status: TaskStatus
  // Context rot demo fields
  context_chunks_retrieved: number
  context_chunks_included: number
  context_chunks_cited: number
  context_token_estimate: number
  context_rot_score: number
}

export interface RunResult {
  run_id: string
  swarm: SwarmState
  single_model: SingleModelResult | null
  document: DocumentResult | null
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
