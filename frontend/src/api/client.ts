import type {
  AgentDefinition,
  AgentDefinitionBody,
  CapacityEngine,
  CapacityScenario,
  CapacityStatus,
  Connector,
  ConnectorCreate,
  Job,
  JobCreate,
  RunDetail,
  RunSummary,
  ToolsResponse,
} from './types'
import { API_BASE } from '../lib/origin'



async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ── Jobs ──────────────────────────────────────────────────────────────────────
export const jobsApi = {
  list: (params?: { status?: string; search?: string }) => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.search) q.set('search', params.search)
    const qs = q.toString()
    return req<Job[]>(`/jobs${qs ? `?${qs}` : ''}`)
  },
  scheduled: () => req<Job[]>('/jobs/scheduled'),
  get: (id: string) => req<Job>(`/jobs/${id}`),
  create: (body: JobCreate) => req<Job>('/jobs', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: string, body: Partial<JobCreate> & { clear_schedule?: boolean }) =>
    req<Job>(`/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  pause: (id: string) => req<Job>(`/jobs/${id}/pause`, { method: 'POST' }),
  resume: (id: string) => req<Job>(`/jobs/${id}/resume`, { method: 'POST' }),
  archive: (id: string) => req<Job>(`/jobs/${id}/archive`, { method: 'POST' }),
  runNow: (id: string) => req<{ run_id: string; job_id: string }>(`/jobs/${id}/run-now`, { method: 'POST' }),
}

// ── Runs ──────────────────────────────────────────────────────────────────────
export const runsApi = {
  list: (params?: { job_id?: string; status?: string; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.job_id) q.set('job_id', params.job_id)
    if (params?.status) q.set('status', params.status)
    if (params?.limit) q.set('limit', String(params.limit))
    const qs = q.toString()
    return req<RunSummary[]>(`/runs${qs ? `?${qs}` : ''}`)
  },
  get: (id: string) => req<RunDetail>(`/runs/${id}`),
  kill: (id: string) => req<{ status: string; cancelled_steps: number }>(`/runs/${id}/kill`, { method: 'POST' }),
}

// ── Connectors ──────────────────────────────────────────────────────────────────
export const connectorsApi = {
  list: () => req<Connector[]>('/connectors'),
  get: (id: string) => req<Connector>(`/connectors/${id}`),
  create: (body: ConnectorCreate) =>
    req<Connector>('/connectors', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: string, body: { config?: Record<string, unknown>; status?: string }) =>
    req<Connector>(`/connectors/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  setSecret: (id: string, field: string, value: string) =>
    req<Connector>(`/connectors/${id}/secrets/${field}`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  revoke: (id: string) => req<Connector>(`/connectors/${id}`, { method: 'DELETE' }),
}

// ── Tools ─────────────────────────────────────────────────────────────────────
export const toolsApi = {
  list: () => req<ToolsResponse>('/tools'),
}

// ── Ad-hoc run ──────────────────────────────────────────────────────────────────
export function startAdHocRun(
  query: string,
  opts?: { validator_enabled?: boolean; plan_approval?: boolean; enabled_tools?: string[] },
) {
  return req<{ run_id: string }>('/run', {
    method: 'POST',
    body: JSON.stringify({
      query,
      validator_enabled: opts?.validator_enabled ?? true,
      plan_approval: opts?.plan_approval ?? null,
      enabled_tools: opts?.enabled_tools ?? [],
    }),
  })
}

// Deliver a HITL plan decision to a paused run (works from any page, not just
// the live WS listener).
export function approveRun(runId: string, decision: 'approve' | 'reject') {
  return req<{ run_id: string; decision: string; delivery: string }>(
    `/run/${runId}/approve`,
    { method: 'POST', body: JSON.stringify({ decision }) },
  )
}

// ── Capacity tester ───────────────────────────────────────────────────────────
export const capacityApi = {
  scenarios: () => req<{ scenarios: CapacityScenario[]; tile: Record<string, number>; e2e_workflows: { id: string; name: string; query: string }[]; e2e_tile: Record<string, number>; defaults: Record<string, number> }>('/capacity/scenarios'),
  engine: () => req<CapacityEngine>('/capacity/engine'),
  startEngine: () => req<{ started: boolean; reason?: string }>('/capacity/engine/start', { method: 'POST' }),
  status: () => req<CapacityStatus>('/capacity/status'),
  start: (body: {
    mode: 'local' | 'remote_mock' | 'remote_real' | 'e2e'
    mix?: 'tile' | 'custom'
    scenarios?: string[]
    agent_definitions?: string[]
    mock_ms?: number
    mock_sigma?: number
    max_users?: number
    seed?: number
    cache_mode?: 'warm' | 'cold'
    confirm_real?: boolean
  }) => req<{ started: boolean }>('/capacity/start', { method: 'POST', body: JSON.stringify(body) }),
  stop: () => req<{ stopping: boolean }>('/capacity/stop', { method: 'POST' }),
}

// ── Agent definitions ─────────────────────────────────────────────────────────
export const agentDefsApi = {
  list: () => req<AgentDefinition[]>('/agent-definitions'),
  create: (body: AgentDefinitionBody) =>
    req<AgentDefinition>('/agent-definitions', { method: 'POST', body: JSON.stringify(body) }),
  update: (id: string, body: Partial<AgentDefinitionBody>) =>
    req<AgentDefinition>(`/agent-definitions/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  clone: (id: string) => req<AgentDefinition>(`/agent-definitions/${id}/clone`, { method: 'POST' }),
  archive: (id: string) => req<AgentDefinition>(`/agent-definitions/${id}/archive`, { method: 'POST' }),
  runOnce: (id: string, input?: string) =>
    req<{ run_id: string }>(`/agent-definitions/${id}/run`, {
      method: 'POST', body: JSON.stringify({ input: input || null }),
    }),
}
