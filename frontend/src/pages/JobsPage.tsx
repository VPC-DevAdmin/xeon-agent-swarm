import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { jobsApi } from '../api/client'
import type { Job, JobCreate } from '../api/types'
import { Button, Card, Empty, Field, StatusBadge, inputClass, timeAgo } from '../components/ui'

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  async function refresh() {
    try {
      setJobs(await jobsApi.list())
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [])

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn()
      await refresh()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-lg font-semibold text-white">Jobs</h1>
        <Button variant="primary" onClick={() => setShowCreate(true)}>+ New Job</Button>
      </div>

      {error && (
        <div className="mb-3 text-xs text-red-400 bg-red-950/40 border border-red-900 rounded px-3 py-2">
          {error}
        </div>
      )}

      {loading ? (
        <Empty message="Loading…" />
      ) : jobs.length === 0 ? (
        <Empty message="No jobs yet. Create one to schedule or run on demand." />
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => (
            <Card key={job.id} className="p-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-gray-100 truncate">{job.name}</span>
                    <StatusBadge status={job.status} />
                    {job.schedule_cron && (
                      <span className="text-[11px] text-gray-500 font-mono">
                        ⏱ {job.schedule_cron} ({job.schedule_tz})
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 truncate">{job.query}</p>
                  <div className="flex items-center gap-4 mt-2 text-[11px] text-gray-600">
                    {job.schedule_cron && <span>next: {timeAgo(job.next_fire_at)}</span>}
                    {job.last_run_id && (
                      <button
                        className="text-blue-400 hover:underline"
                        onClick={() => navigate(`/runs/${job.last_run_id}`)}
                      >
                        last run →
                      </button>
                    )}
                    <span>overlap: {job.overlap_policy}</span>
                  </div>
                </div>
                <div className="flex flex-col gap-1.5 shrink-0">
                  <Button variant="primary" onClick={() => act(async () => {
                    const r = await jobsApi.runNow(job.id)
                    navigate(`/runs/${r.run_id}`)
                  })}>Run now</Button>
                  {job.status === 'active' && (
                    <Button onClick={() => act(() => jobsApi.pause(job.id))}>Pause</Button>
                  )}
                  {job.status === 'paused' && (
                    <Button onClick={() => act(() => jobsApi.resume(job.id))}>Resume</Button>
                  )}
                  <Button variant="ghost" onClick={() => act(() => jobsApi.archive(job.id))}>Archive</Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {showCreate && (
        <CreateJobModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); refresh() }}
        />
      )}
    </div>
  )
}

function CreateJobModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState<JobCreate>({
    name: '', query: '', schedule_cron: '', schedule_tz: 'UTC', overlap_policy: 'skip',
  })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    setSaving(true)
    setErr(null)
    try {
      await jobsApi.create({
        ...form,
        schedule_cron: form.schedule_cron?.trim() || null,
      })
      onCreated()
    } catch (e) {
      setErr(String(e))
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <Card className="w-full max-w-lg p-5" >
        <div onClick={(e) => e.stopPropagation()}>
          <h2 className="text-sm font-semibold text-white mb-4">New Job</h2>
          <div className="space-y-3">
            <Field label="Name">
              <input className={inputClass} value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Daily AI hardware briefing" />
            </Field>
            <Field label="Query (the prompt the orchestrator decomposes)">
              <textarea className={inputClass} rows={3} value={form.query}
                onChange={(e) => setForm({ ...form, query: e.target.value })}
                placeholder="Summarize overnight AI-hardware news from the last 24h…" />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Schedule (cron, optional)">
                <input className={inputClass} value={form.schedule_cron ?? ''}
                  onChange={(e) => setForm({ ...form, schedule_cron: e.target.value })}
                  placeholder="0 8 * * MON-FRI" />
              </Field>
              <Field label="Timezone">
                <input className={inputClass} value={form.schedule_tz}
                  onChange={(e) => setForm({ ...form, schedule_tz: e.target.value })} />
              </Field>
            </div>
            <Field label="Overlap policy">
              <select className={inputClass} value={form.overlap_policy}
                onChange={(e) => setForm({ ...form, overlap_policy: e.target.value as JobCreate['overlap_policy'] })}>
                <option value="skip">skip — don't start if a run is active</option>
                <option value="parallel">parallel — always start</option>
                <option value="queue">queue — (treated as skip for now)</option>
              </select>
            </Field>
            <label className="flex items-center gap-2 text-xs text-gray-400">
              <input type="checkbox"
                checked={form.config?.validator_enabled !== false}
                onChange={(e) => setForm({ ...form, config: { ...form.config, validator_enabled: e.target.checked } })} />
              Contract enforcement (validator)
            </label>
          </div>
          {err && <div className="mt-3 text-xs text-red-400">{err}</div>}
          <div className="flex justify-end gap-2 mt-5">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button variant="primary" onClick={submit} disabled={saving || !form.name || !form.query}>
              {saving ? 'Creating…' : 'Create Job'}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  )
}
