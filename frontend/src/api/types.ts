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

export type CapacityBenchmarkTarget = 'agent_host' | 'integrated_node' | 'inference_engine'
export type CapacityInferenceBackend = 'local' | 'remote_mock' | 'remote_real'

export interface CapacityCloudModel {
  id: string
  provider: 'openai' | 'anthropic' | 'google' | 'custom'
  name: string
  model: string
  base_url: string
  input_per_mtok: number
  output_per_mtok: number
  pricing_as_of: string
  pricing_url?: string | null
  pricing_note?: string | null
  api_key_configured?: boolean
}

export interface CapacityLevel {
  phase: 'ramp' | 'steady'
  users: number
  tiles?: number | null
  slo_state: 'good' | 'bad' | 'inconclusive'
  p95_ms?: number | null
  err_rate: number
  tps: number
  rpm: number
  tokens_in: number
  tokens_out: number
  incremental_cost_usd: number
  cumulative_cost_usd: number
  projected_cost_per_hour: number
}

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
  in_flight?: number
  tps: number
  rpm: number
  p50_ms?: number | null
  p95_ms?: number | null
  err_rate: number
  cost_usd?: number
  cost_per_hour?: number
  cpu_by?: Record<string, number>   // per-component CPU%, same basis as cpu_pct
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
  last_error?: string | null
}

export interface CapacityBreach {
  profile: string
  metric: string
  value: number
  limit: number
  baseline_ms?: number | null
}

export interface CapabilityBlock {
  users: number | null
  tiles?: number | null
  // 'measured' | 'lower bound' | 'not met at any tested level' |
  // 'not configured' | 'stopped before certification'
  status: string
  reason?: string
  service_class?: string
  confidence?: number
  target?: number
  last_tested_users?: number
  per_type?: Record<string, {
    deadline_s: number; decided: number; successes: number; pending: number
    observed: number | null; lower_bound_95: number | null
  }>
}

export interface CapacityRateLevel {
  offered_rate: number
  // The rate the schedule actually fired at, counted per level. A gap below
  // offered_rate is the generator saturating, and it censors the run.
  achieved_rate?: number
  clean_rate: number
  backlog_slope_lb?: number | null
  outstanding?: number
  oldest_inflight_s?: number | null
  errors?: number
  err_rate?: number
  rejected?: number
  // What the in-process generator (the control plane) cost during this level.
  control_cpu_pct?: number | null
}

export interface SustainableCapacityBlock {
  // 'measured' | 'lower bound' | 'no distinct capacity knee detected' |
  // 'too few offered rates to fit a breakpoint'
  status: string
  reason?: string
  at_least_workflows_per_s?: number | null
  clean_workflows_per_s?: number
  breakpoint_estimate?: number
  ci95?: [number, number]
  confirmed_divergence_rate?: number | null
  levels?: CapacityRateLevel[]
}

export interface CapacityResult {
  capability?: CapabilityBlock | null
  sustainable_capacity?: SustainableCapacityBlock | null
  capacity_workflows_per_s?: number | null
  stability_ceiling_users?: number | null
  failure_onset?: { offered_rate?: number; reason?: string } | null
  load_model?: 'closed' | 'open'
  service_class?: string
  mode: string
  benchmark_target?: CapacityBenchmarkTarget
  inference_backend?: CapacityInferenceBackend
  verdict: string | null
  // What the run's numbers MEAN, decided by how it ended. 'boundary' = the
  // system showed its edge. 'lower_bound' = the run stopped for its own
  // reasons first, so every figure is a floor. 'invalid' = the run did not
  // measure what it claims to.
  result_kind?: 'boundary' | 'lower_bound' | 'invalid' | 'inconclusive'
  censored?: boolean
  censor_reason?: string | null
  capacity_users: number | null   // null = no rung certified — capacity unknown
  capacity_certified?: boolean
  capacity_tiles?: number | null
  mix?: string
  comparable?: boolean
  tile?: Record<string, number> | null
  tile_size?: number | null
  breach?: CapacityBreach | null
  knee_users?: number | null   // efficiency knee (diagnostic; never a stop)
  slo_capacity_users?: number | null  // overlay: last level within the default latency budget
  slo_capacity_tiles?: number | null
  baselines?: Record<string, number>
  baseline_p95_ms?: number | null
  slo?: { p95_x: number; p95_ms?: number | null; err: number }
  peak_users?: number
  max_users: number // legacy alias for old exports
  duration_s: number
  total_requests: number
  completed_requests?: number
  unfinished_requests?: number
  max_in_flight?: number
  total_tokens_out: number
  total_tokens_in?: number
  steady: {
    tps: number; rpm: number; p50_ms?: number | null; p95_ms?: number | null
    err_rate: number; cpu_pct?: number | null; mem_pct?: number | null
    power_w?: number | null; load1?: number | null
    bw_gbs?: number | null; kv_pct?: number | null
  }
  energy_wh?: number | null
  cpu_breakdown?: Record<string, number> | null   // steady-state CPU% by component
  background_cpu_pct?: number | null   // whole-run median CPU from processes outside the benchmark
  workflows_per_hour?: number | null
  mem_mb_per_user?: number | null
  per_scenario: Record<string, CapacityScenarioStat>
  timeline: CapacitySample[]
  cloud_model?: CapacityCloudModel | null
  pricing?: {
    currency: 'USD'; input_per_mtok: number; output_per_mtok: number
    pricing_as_of?: string | null; pricing_url?: string | null; note?: string | null
  } | null
  cost?: {
    run_total_usd: number; circuit_breaker_usd: number
    in_flight_reserved_usd?: number; committed_estimate_usd?: number
    remaining_usd: number; steady_cost_per_hour: number
    steady_cost_per_workflow?: number | null; steady_cost_per_1k_requests?: number | null
  } | null
  capacity_levels?: CapacityLevel[]
  error?: string | null
  repro?: {
    seed: number
    cache_mode: string | null
    warmup_s?: number | null
    eval_window_s?: number | null
    hold_window_s?: number | null
    e2e_timeout_s?: number | null
    background_cpu_pct?: number | null
    benchmark_version: number
    scenario_fingerprint?: string | null
    git_commit?: string | null
    model?: string | null
    engine?: Record<string, unknown> | null
    host?: { platform?: string; cpu_count?: number; mem_total_gb?: number | null; numa_nodes?: number | null }
    mix?: string
    tile?: Record<string, number> | null
    benchmark_target?: CapacityBenchmarkTarget
    inference_backend?: CapacityInferenceBackend
  } | null
}

export interface RepeatMetric {
  unit: string
  n: number
  median: number
  min: number
  max: number
  values: number[]
  // How far apart the runs landed as a share of the median: the number that
  // says whether the set agrees with itself.
  spread_pct?: number | null
}

export interface RepeatExclusion {
  seed: number
  verdict?: string | null
  reason: string
  history_id?: string | null
}

export interface RepeatSetResult {
  kind: 'repeat_set'
  status: 'complete' | 'incomplete'
  runs_requested: number
  runs_accepted: number
  runs_excluded: number
  censored: boolean
  censor_reasons?: string[]
  metrics: Record<string, RepeatMetric>
  excluded: RepeatExclusion[]
  child_run_ids: string[]
  seeds: (number | null)[]
  comparability?: Record<string, string | number | null> | null
  base_seed: number
  duration_s: number
  incomplete_reason?: string
}

export interface RepeatSetStatus {
  active: boolean
  phase: string
  runs_requested: number
  runs_accepted: number
  runs_excluded: number
  excluded: RepeatExclusion[]
  base_seed: number
  elapsed_s: number
  result?: RepeatSetResult | null
}

export interface CapacityStatus {
  active: boolean
  phase: string
  repeat?: RepeatSetStatus | null
  verdict?: string | null
  mode?: string
  benchmark_target?: CapacityBenchmarkTarget
  inference_backend?: CapacityInferenceBackend
  users?: number
  capacity_users?: number | null
  capacity_certified?: boolean | null
  capacity_tiles?: number | null
  mix?: string
  tile_size?: number | null
  breach?: CapacityBreach | null
  baseline_p95_ms?: number | null
  elapsed_s?: number
  total_requests?: number
  cost_usd?: number | null
  committed_cost_usd?: number | null
  max_cost_usd?: number | null
  cloud_model?: CapacityCloudModel | null
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
}

// ── Agent definitions (persistent configured agents) ─────────────────────────

export interface AgentDefinition {
  id: string
  name: string
  icon: string
  purpose?: string | null
  instructions: string
  enabled_tools: string[]
  plan_approval: boolean
  validator_enabled: boolean
  budgets?: Record<string, number> | null
  session_policy?: Record<string, number> | null
  slo?: { p95_ms?: number } | null
  schedule_cron?: string | null
  schedule_tz: string
  job_id?: string | null
  version: number
  status: 'active' | 'archived'
  history: { version: number; ts: string }[]
  created_at?: string | null
  updated_at?: string | null
}

export interface AgentDefinitionBody {
  name: string
  icon?: string
  purpose?: string
  instructions: string
  enabled_tools?: string[]
  plan_approval?: boolean
  validator_enabled?: boolean
  budgets?: Record<string, number> | null
  schedule_cron?: string | null
  clear_schedule?: boolean
}

export interface CapacityHistoryRow {
  id: string
  mode: string
  benchmark_target?: CapacityBenchmarkTarget | null
  inference_backend?: CapacityInferenceBackend | null
  mix: string
  comparable?: boolean | null
  verdict?: string | null
  result_kind?: 'boundary' | 'lower_bound' | 'invalid' | 'inconclusive' | null
  censored?: boolean | null
  capacity_users?: number | null
  capacity_certified?: boolean | null
  capacity_tiles?: number | null
  workflows_per_hour?: number | null
  cloud_model_name?: string | null
  run_cost_usd?: number | null
  steady_cost_per_hour?: number | null
  circuit_breaker_usd?: number | null
  steady_tps?: number | null
  p95_ms?: number | null
  duration_s?: number | null
  seed?: number | null
  label?: string | null
  scenario_fingerprint?: string | null
  git_commit?: string | null
  cache_mode?: string | null
  started_at?: string | null
}
