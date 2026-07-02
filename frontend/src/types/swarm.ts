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
  // Contract fields (new — populated by structured orchestrator)
  objective?: string
  scope?: string[]
  deliverable_format?: string
  success_criteria?: string[]
  type: TaskType
  dependencies: string[]
  priority: number
  expected_image_types?: string[]
  fallback_behavior?: 'skip' | 'retrieval_only' | 'describe'
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
  render_targets: string[]
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
  claim: string; verdict: 'supported' | 'unsupported' | 'uncertain' | 'partially_supported' | 'contradicted'
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
  total_tokens?: number
  // Routing telemetry (from the semantic tier router, per task_completed)
  tier_observed?: string | null   // tier the router actually served (T1..T5)
  category?: string | null        // router's difficulty classification
  cache_hits?: number
  tokens_out?: number
  tool_hops?: number
  verdict?: string                // pass | degraded (validation terminal state)
  attempts?: number
  retries_used?: number
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
  validator_enabled: boolean
}

// ── Validation models ─────────────────────────────────────────────────────────

export interface ValidationVerdict {
  compliant: boolean
  failed_criteria: string[]
  correction_hint: string
  severity: 'minor' | 'major' | 'unfixable'
}

// ── Run metrics (the run_metrics WS packet: routing rollup + token totals) ────

export interface RunMetrics {
  call_count: number                        // model calls across the whole run
  cached_calls: number                      // served from the router cache
  tokens_out: number
  tier_calls: Record<string, number>        // tier -> calls (the routing distribution)
  tier_tokens_out: Record<string, number>
  task_count: number                        // worker delegations
  total_tokens: number                      // generation in+out
  validation_tokens: number                 // validator spend, kept separate
}

// ── Intelligence report ───────────────────────────────────────────────────────

export interface DocumentSection {
  title: string
  content: string
  sources: string[]
  render_targets?: string[]
  audio_url?: string | null
}

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
  executive_summary_audio_url?: string | null
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
  | 'task_token'              // streaming token from writing worker
  | 'task_completed'
  | 'task_failed'
  | 'task_killed'
  | 'validator_started'       // validator checking output
  | 'validator_approved'      // passed validation
  | 'validator_rejected'      // failed validation
  | 'worker_retrying'         // retrying with correction hint
  | 'worker_rejected_final'   // exceeded retry budget
  | 'synthesis_started'
  | 'awaiting_approval'       // HITL: run paused for plan approval
  | 'run_resumed'             // HITL: resumed after a decision
  | 'tts_started'
  | 'tts_completed'
  | 'run_completed'
  | 'run_metrics'             // final metrics packet
  | 'error'

export interface SwarmEvent {
  event: EventType
  run_id: string
  payload: Record<string, unknown>
  timestamp: string
}
