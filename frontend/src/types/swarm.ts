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

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'killed'

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

// ── Typed artifact system ─────────────────────────────────────────────────────

export type ArtifactType =
  | 'prose'
  | 'table'
  | 'diagram'
  | 'chart'
  | 'code'
  | 'claim_verdict'
  | 'citation_set'
  | 'extracted_data'

export interface Artifact {
  type: ArtifactType
  content: Record<string, unknown>
  worker_id: string
  confidence: number
  source_chunks: string[]
}

export interface TableContent { headers: string[]; rows: string[][]; caption?: string }
export interface DiagramContent { mermaid: string; caption?: string }
export interface ChartContent {
  series: Array<{ name: string; data: Array<{ x: string | number; y: number }> }>
  x_label?: string; y_label?: string; chart_type?: 'bar' | 'line'; caption?: string
}
export interface CodeContent {
  language: string; code: string; description?: string; syntax_valid?: boolean
}
export interface ClaimVerdictContent {
  claim: string; verdict: 'supported' | 'unsupported' | 'uncertain'
  evidence?: string; source_url?: string
}
export interface CitationSetContent {
  citations: Array<{ title: string; url: string; snippet?: string }>
}
export interface ExtractedDataContent {
  description: string
  data_points: Array<{ label: string; value: string; unit?: string }>
  source_image?: string
}

// ── Agent result ──────────────────────────────────────────────────────────────

export interface AgentResult {
  task_id: string
  status: TaskStatus
  result: string
  artifacts: Artifact[]
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

// ── Intelligence report ───────────────────────────────────────────────────────

export interface DocumentSection { title: string; content: string; sources: string[] }
export interface CodeSnippet {
  language: string; description: string; code: string; syntax_valid: boolean
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
  artifacts: Artifact[]
}

export interface RunResult {
  run_id: string
  swarm: SwarmState
  document: DocumentResult | null
}

// ── WebSocket events ──────────────────────────────────────────────────────────

export type EventType =
  | 'run_started'
  | 'graph_ready'
  | 'task_started'
  | 'task_completed'
  | 'task_failed'
  | 'task_killed'
  | 'synthesis_started'
  | 'run_completed'
  | 'error'

export interface SwarmEvent {
  event: EventType
  run_id: string
  payload: Record<string, unknown>
  timestamp: string
}
