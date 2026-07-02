import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { jobsApi, startAdHocRun } from '../api/client'
import { useSwarmStore } from '../store/swarmStore'
import { SamplePromptGallery } from './SamplePromptGallery'

export interface SchedulePreset {
  id: string
  label: string
  cron: string | null // null = run once
}

export const SCHEDULE_PRESETS: SchedulePreset[] = [
  { id: 'once', label: 'Run once now', cron: null },
  { id: '15min', label: 'Every 15 minutes', cron: '*/15 * * * *' },
  { id: 'hourly', label: 'Every hour', cron: '0 * * * *' },
  { id: 'daily', label: 'Daily at 9:00', cron: '0 9 * * *' },
  { id: 'weekdays', label: 'Weekdays at 9:00', cron: '0 9 * * 1-5' },
  { id: 'weekly', label: 'Weekly (Mon 9:00)', cron: '0 9 * * 1' },
]

interface Props {
  onRunStart: (runId: string, query: string) => void
}

/**
 * PromptComposer — the single entry point for new work.
 *
 * A prompt either runs once (optionally pausing for plan review) or is saved
 * as a scheduled task on a recurring interval. Sample prompts are one click away.
 */
export function PromptComposer({ onRunStart }: Props) {
  const navigate = useNavigate()
  const [prompt, setPrompt] = useState('')
  const [schedule, setSchedule] = useState<SchedulePreset>(SCHEDULE_PRESETS[0])
  const [reviewPlan, setReviewPlan] = useState(true)
  const [galleryOpen, setGalleryOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [scheduledOk, setScheduledOk] = useState<string | null>(null)
  const isRunning = useSwarmStore((s) => s.isRunning)

  const recurring = schedule.cron !== null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const q = prompt.trim()
    if (!q || busy) return
    setBusy(true)
    setError(null)
    setScheduledOk(null)
    try {
      if (recurring) {
        // Recurring prompts run unattended — no plan-review pause.
        const job = await jobsApi.create({
          name: q.length > 60 ? `${q.slice(0, 57)}…` : q,
          query: q,
          schedule_cron: schedule.cron,
        })
        setScheduledOk(`Scheduled “${job.name}” — ${schedule.label.toLowerCase()}.`)
        setPrompt('')
        setTimeout(() => navigate('/activity?tab=scheduled'), 900)
      } else {
        const { run_id } = await startAdHocRun(q, { plan_approval: reviewPlan })
        onRunStart(run_id, q)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="w-full max-w-4xl mx-auto px-4">
      <form onSubmit={handleSubmit}>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe what you want done — it will be broken into tasks and handled by a team of agents…"
          rows={3}
          maxLength={10000}
          disabled={busy || isRunning}
          className="w-full bg-gray-900 border border-gray-700 rounded-lg px-4 py-3 text-gray-100 placeholder-gray-500 focus:outline-none focus:border-blue-500 resize-none disabled:opacity-50"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit(e)
          }}
        />

        <div className="flex flex-wrap items-center justify-between mt-3 gap-3">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setGalleryOpen(true)}
              disabled={busy || isRunning}
              className="text-xs text-blue-400 hover:text-blue-300 border border-blue-900 hover:border-blue-700 rounded px-3 py-1.5 transition-colors disabled:opacity-40"
            >
              ✨ Browse sample prompts
            </button>

            <select
              value={schedule.id}
              onChange={(e) =>
                setSchedule(SCHEDULE_PRESETS.find((p) => p.id === e.target.value) ?? SCHEDULE_PRESETS[0])
              }
              disabled={busy || isRunning}
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-blue-500 disabled:opacity-40"
            >
              {SCHEDULE_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>

            {!recurring && (
              <label className="flex items-center gap-2 cursor-pointer select-none group">
                <div className="relative">
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={reviewPlan}
                    onChange={(e) => setReviewPlan(e.target.checked)}
                    disabled={busy || isRunning}
                  />
                  <div className={`w-9 h-5 rounded-full transition-colors ${
                    reviewPlan ? 'bg-amber-600' : 'bg-gray-700'
                  } ${busy || isRunning ? 'opacity-40' : 'cursor-pointer'}`} />
                  <div className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                    reviewPlan ? 'translate-x-4' : 'translate-x-0'
                  }`} />
                </div>
                <span className="text-xs text-gray-400 group-hover:text-gray-300 transition-colors">
                  Review plan first
                </span>
              </label>
            )}
          </div>

          <button
            type="submit"
            disabled={!prompt.trim() || busy || isRunning}
            className="px-6 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded-lg font-medium transition-colors"
          >
            {busy
              ? 'Submitting…'
              : isRunning
              ? 'Running…'
              : recurring
              ? 'Schedule task 📅'
              : 'Run ⚡'}
          </button>
        </div>

        {error && <p className="mt-2 text-red-400 text-sm">{error}</p>}
        {scheduledOk && <p className="mt-2 text-green-400 text-sm">{scheduledOk}</p>}
      </form>

      <SamplePromptGallery
        open={galleryOpen}
        onClose={() => setGalleryOpen(false)}
        onSelect={(p, isRecurring) => {
          setPrompt(p)
          if (isRecurring) setSchedule(SCHEDULE_PRESETS.find((s) => s.id === 'daily') ?? SCHEDULE_PRESETS[0])
          setGalleryOpen(false)
        }}
      />
    </div>
  )
}
