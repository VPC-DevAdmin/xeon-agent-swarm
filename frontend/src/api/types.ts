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

// ── Capacity tester ───────────────────────────────────────────────────────────

export interface CapacityScenarioStep {
  label: string
  prompt_tokens: number
  max_tokens: number
  carry_context: boolean
  tool_calls: number
  tool_result_tokens: number
}

export interface CapacityScenario {
  id: string
  name: string
  blurb: string
  complexity: 'light' | 'medium' | 'heavy'
  calls_per_loop: number
  tool_calls_per_loop: number
  tokens_out_per_loop: number
  tokens_in_per_loop: number
  think_ms: number
  session_turns: number
  context_cap: number
  steps: CapacityScenarioStep[]
}

export interface CapacitySample {
  ts: number
  cpu_pct?: number | null
  mem_gb?: number | null
  mem_pct?: number | null
  load1?: number | null
  power_w?: number | null
  bw_gbs?: number | null
  kv_pct?: number | null
  users: number
  tps: number
  rpm: number
  p50_ms?: number | null
  p95_ms?: number | null
  err_rate: number
}

export interface CapacityScenarioStat {
  name: string
  users: number
  calls: number
  errors: number
  p50_ms?: number | null
  p95_ms?: number | null
  tokens_out?: number
  avg_tokens_in_flight?: number
  trace?: { llm_calls: number; steps: number; validations: number; task_count: number }
}

export interface CapacityBreach {
  profile: string
  metric: string
  value: number
  limit: number
  baseline_ms?: number | null
}

export interface CapacityResult {
  mode: string
  verdict: string | null
  capacity_users: number
  capacity_tiles?: number | null
  mix?: string
  comparable?: boolean
  tile?: Record<string, number> | null
  tile_size?: number | null
  breach?: CapacityBreach | null
  baselines?: Record<string, number>
  baseline_p95_ms?: number | null
  slo?: { p95_x: number; p95_ms?: number | null; err: number }
  max_users: number
  duration_s: number
  total_requests: number
  total_tokens_out: number
  steady: {
    tps: number; rpm: number; p50_ms?: number | null; p95_ms?: number | null
    err_rate: number; cpu_pct?: number | null; mem_pct?: number | null
    power_w?: number | null; load1?: number | null
    bw_gbs?: number | null; kv_pct?: number | null
  }
  energy_wh?: number | null
  workflows_per_hour?: number | null
  mem_mb_per_user?: number | null
  per_scenario: Record<string, CapacityScenarioStat>
  timeline: CapacitySample[]
  error?: string | null
  repro?: {
    seed: number
    cache_mode: string
    warmup_s?: number | null
    benchmark_version: number
    scenario_fingerprint?: string | null
    git_commit?: string | null
    model?: string | null
    engine?: Record<string, unknown> | null
    host?: { platform?: string; cpu_count?: number; mem_total_gb?: number | null; numa_nodes?: number | null }
    mix?: string
    tile?: Record<string, number> | null
  } | null
}

export interface CapacityStatus {
  active: boolean
  phase: string
  verdict?: string | null
  mode?: string
  users?: number
  capacity_users?: number | null
  capacity_tiles?: number | null
  mix?: string
  tile_size?: number | null
  breach?: CapacityBreach | null
  baseline_p95_ms?: number | null
  elapsed_s?: number
  total_requests?: number
  latest?: Partial<CapacitySample>
  per_scenario?: Record<string, CapacityScenarioStat>
  timeline?: CapacitySample[]
  error?: string | null
  result?: CapacityResult | null
}

export interface CapacityEngine {
  base_url: string
  model: string
  setup_state: 'idle' | 'starting' | 'ready' | 'failed'
  setup_log: string[]
  serving: boolean
  models: string[]
  remote_real: { configured: boolean; model: string | null }
}
