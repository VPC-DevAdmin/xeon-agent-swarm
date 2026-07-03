// DTOs mirroring backend/schemas/api.py

export interface Job {
  id: string
  name: string
  description?: string | null
  query: string
  config: Record<string, unknown>
  schedule_cron?: string | null
  schedule_tz: string
  overlap_policy: 'skip' | 'queue' | 'parallel'
  status: 'active' | 'paused' | 'archived'
  next_fire_at?: string | null
  last_run_id?: string | null
  owner?: string | null
  created_at: string
  updated_at: string
  connector_ids: string[]
}

export interface JobCreate {
  name: string
  query: string
  description?: string
  config?: Record<string, unknown>
  schedule_cron?: string | null
  schedule_tz?: string
  overlap_policy?: 'skip' | 'queue' | 'parallel'
  owner?: string
  connector_ids?: string[]
}

export interface RunSummary {
  id: string
  job_id?: string | null
  trigger: string
  status: string
  query: string
  started_at: string
  completed_at?: string | null
}

export interface StepAttempt {
  attempt_no: number
  status: string
  model_id?: string | null
  correction_hint?: string | null
  latency_ms?: number | null
  // Routing telemetry: the semantic router's decision for this call
  tier_requested?: string | null
  tier_observed?: string | null
  category?: string | null
  cache_hit?: boolean | null
  tokens_in?: number | null
  tokens_out?: number | null
}

export interface StepValidation {
  level: 'mechanical' | 'judge' | 'frontier'
  verdict: 'pass' | 'degraded' | 'fail'
  score?: number | null
  validator_tier?: string | null
  rubric_id?: string | null
  retries_used: number
  escalated: boolean
  detail?: Record<string, unknown> | null
}

export interface RunStep {
  step_key: string
  type: string
  objective?: string | null
  deliverable_format?: string | null
  dependencies: string[]
  status: string
  result?: Record<string, unknown> | null
  confidence?: number | null
  total_attempts: number
  latency_ms?: number | null
  attempts: StepAttempt[]
  validations: StepValidation[]
}

export interface RunDetail {
  run_id: string
  job_id?: string | null
  trigger: string
  query: string
  config: Record<string, unknown>
  status: string
  task_graph?: Record<string, unknown> | null
  document?: Record<string, unknown> | null
  metrics?: Record<string, unknown> | null
  langfuse_trace_id?: string | null
  error?: string | null
  started_at?: string | null
  completed_at?: string | null
  steps: RunStep[]
}

export interface Connector {
  id: string
  name: string
  kind: string
  config: Record<string, unknown>
  status: 'active' | 'revoked' | 'expired'
  last_health_at?: string | null
  last_health_ok?: boolean | null
  created_at: string
  updated_at: string
  secret_fields: string[]
}

export interface ConnectorCreate {
  name: string
  kind: string
  config?: Record<string, unknown>
  secrets?: Record<string, string>
}

export const CONNECTOR_KINDS = [
  'slack', 'github', 'gmail', 'mcp_server',
  'http_webhook', 'router', 'search_endpoint',
] as const

// ── Tools ──────────────────────────────────────────────────────────────────────
export interface ToolSetupField {
  field: string
  label: string
  secret: boolean
}

export interface Tool {
  id: string
  name: string
  category: string
  description: string
  capabilities: string[]
  backing: 'builtin' | 'api' | 'stub'
  write_risk: boolean
  setup: ToolSetupField[]
  configured: boolean
}

export interface ToolsResponse {
  categories: string[]
  tools: Tool[]
}
