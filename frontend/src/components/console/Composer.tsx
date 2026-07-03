import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { jobsApi, startAdHocRun, toolsApi } from '../../api/client'
import type { Tool } from '../../api/types'
import { SamplePromptGallery } from '../SamplePromptGallery'
import { ToolGallery } from './ToolGallery'

export interface SchedulePreset {
  id: string
  label: string
  cron: string | null // null = run once
}

export const SCHEDULE_PRESETS: SchedulePreset[] = [
  { id: 'once', label: 'Run once', cron: null },
  { id: '15min', label: 'Every 15 min', cron: '*/15 * * * *' },
  { id: 'hourly', label: 'Every hour', cron: '0 * * * *' },
  { id: 'daily', label: 'Daily 9:00', cron: '0 9 * * *' },
  { id: 'weekdays', label: 'Weekdays 9:00', cron: '0 9 * * 1-5' },
  { id: 'weekly', label: 'Weekly Mon 9:00', cron: '0 9 * * 1' },
]

interface Props {
  disabled?: boolean
  onRunStart: (runId: string, prompt: string) => void
  onScheduled: (jobName: string, cadence: string) => void
}

/**
 * Composer — the console's single point of input. A prompt either runs now
 * (optionally pausing for plan review) or is scheduled on a recurring interval.
 */
export function Composer({ disabled, onRunStart, onScheduled }: Props) {
  const [prompt, setPrompt] = useState('')
  const [schedule, setSchedule] = useState<SchedulePreset>(SCHEDULE_PRESETS[0])
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const [review, setReview] = useState(true)
  const [galleryOpen, setGalleryOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Tool picker: which tools are enabled for the next run.
  const [tools, setTools] = useState<Tool[]>([])
  const [enabledTools, setEnabledTools] = useState<string[]>([])
  const [toolsOpen, setToolsOpen] = useState(false)
  const [toolGalleryOpen, setToolGalleryOpen] = useState(false)

  const loadTools = () => {
    toolsApi.list().then((res) => setTools(res.tools)).catch(() => { /* offline — retry on next open */ })
  }
  useEffect(() => { loadTools() }, [])

  const recurring = schedule.cron !== null

  async function submit() {
    const q = prompt.trim()
    if (!q || busy || disabled) return
    setBusy(true)
    setError(null)
    try {
      if (recurring) {
        const job = await jobsApi.create({
          name: q.length > 60 ? `${q.slice(0, 57)}…` : q,
          query: q,
          schedule_cron: schedule.cron,
          config: enabledTools.length ? { enabled_tools: enabledTools } : undefined,
        })
        setPrompt('')
        setSchedule(SCHEDULE_PRESETS[0])
        onScheduled(job.name, schedule.label)
      } else {
        const { run_id } = await startAdHocRun(q, { plan_approval: review, enabled_tools: enabledTools })
        setPrompt('')
        onRunStart(run_id, q)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  return (
    <div className="w-full max-w-[760px] mx-auto px-6 pb-5 pt-2">
      {error && <p className="text-[12.5px] mb-1.5 px-1" style={{ color: 'var(--bad)' }}>{error}</p>}
      <div
        className="console-panel flex items-end gap-2 p-2.5 pl-4 focus-within:!border-[#38425a] transition-colors"
        style={{ borderRadius: 14 }}
      >
        <textarea
          ref={inputRef}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
          }}
          rows={Math.min(4, Math.max(1, prompt.split('\n').length))}
          maxLength={10000}
          placeholder="Describe what you want done…"
          disabled={busy || disabled}
          className="flex-1 bg-transparent outline-none resize-none text-[15px] py-1.5 placeholder:text-[var(--faint)] disabled:opacity-50"
        />

        {/* sample gallery */}
        <button
          title="Browse sample prompts"
          onClick={() => setGalleryOpen(true)}
          className="flex-none w-9 h-9 grid place-items-center rounded-lg text-[15px] hover:bg-[var(--elev)] transition-colors"
          style={{ color: 'var(--muted)' }}
        >
          ✨
        </button>

        {/* tool picker */}
        <div className="relative flex-none">
          <button
            title="Enable tools for this run"
            onClick={() => { setToolsOpen((v) => !v); if (!tools.length) loadTools() }}
            className="relative w-9 h-9 grid place-items-center rounded-lg text-[15px] hover:bg-[var(--elev)] transition-colors"
            style={{ color: enabledTools.length ? 'var(--accent)' : 'var(--muted)' }}
          >
            🧰
            {enabledTools.length > 0 && (
              <span
                className="absolute -top-0.5 -right-0.5 min-w-[15px] h-[15px] px-1 grid place-items-center rounded-full font-code text-[9px] font-semibold"
                style={{ background: 'var(--accent)', color: '#0b0f18' }}
              >
                {enabledTools.length}
              </span>
            )}
          </button>
          {toolsOpen && (
            <ToolPicker
              tools={tools}
              enabled={enabledTools}
              onToggle={(id) =>
                setEnabledTools((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
              }
              onClose={() => setToolsOpen(false)}
              onManage={() => { setToolsOpen(false); setToolGalleryOpen(true) }}
            />
          )}
        </div>

        {/* schedule select */}
        <div className="relative flex-none">
          <button
            onClick={() => setScheduleOpen((v) => !v)}
            className={clsx('flex items-center gap-1.5 h-9 px-3 rounded-lg border font-code text-[12px] transition-colors',
              recurring ? 'text-[var(--text)]' : 'text-[var(--muted)] hover:text-[var(--text)]')}
            style={{ borderColor: recurring ? 'rgba(124,135,245,.45)' : 'var(--line)', background: 'var(--elev)' }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M23 4v6h-6M1 20v-6h6" />
              <path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15" />
            </svg>
            {schedule.label}
          </button>
          {scheduleOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setScheduleOpen(false)} />
              <div className="absolute right-0 bottom-[calc(100%+6px)] z-20 w-44 p-1.5 rounded-[10px] anim-pop"
                style={{ background: 'var(--elev2)', border: '1px solid var(--line)', boxShadow: 'var(--shadow)' }}>
                {SCHEDULE_PRESETS.map((p) => (
                  <button key={p.id}
                    onClick={() => { setSchedule(p); setScheduleOpen(false) }}
                    className="flex justify-between items-center w-full px-2.5 py-2 rounded-md text-[12.5px] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--panel)]">
                    {p.label}
                    <span style={{ color: 'var(--accent)', opacity: schedule.id === p.id ? 1 : 0 }}>✓</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        {/* review-plan toggle (one-off runs only) */}
        {!recurring && (
          <button
            title={review ? 'Plan review on — you approve before agents run' : 'Plan review off — runs immediately'}
            onClick={() => setReview((v) => !v)}
            className="flex-none flex items-center gap-1.5 h-9 px-3 rounded-lg border font-code text-[12px] transition-colors"
            style={{
              borderColor: review ? 'rgba(228,197,106,.4)' : 'var(--line)',
              color: review ? 'var(--warn)' : 'var(--faint)',
              background: 'var(--elev)',
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
            review
          </button>
        )}

        <button
          onClick={submit}
          disabled={!prompt.trim() || busy || disabled}
          className="flex-none h-9 px-4 rounded-[9px] font-semibold text-[13.5px] transition-opacity disabled:opacity-35"
          style={{ background: 'var(--accent)', color: '#0b0f18' }}
        >
          {busy ? '…' : recurring ? 'Schedule' : 'Send'}
        </button>
      </div>

      <SamplePromptGallery
        open={galleryOpen}
        onClose={() => setGalleryOpen(false)}
        onSelect={(p, isRecurring) => {
          setPrompt(p)
          if (isRecurring) setSchedule(SCHEDULE_PRESETS.find((s) => s.id === 'daily') ?? SCHEDULE_PRESETS[0])
          setGalleryOpen(false)
          inputRef.current?.focus()
        }}
      />

      <ToolGallery
        open={toolGalleryOpen}
        onClose={() => setToolGalleryOpen(false)}
        onConfigured={loadTools}
      />
    </div>
  )
}

/**
 * ToolPicker — a compact popover of enable-checkboxes for the next run.
 * Configured tools sort first; unconfigured ones stay selectable with a hint.
 */
function ToolPicker({
  tools,
  enabled,
  onToggle,
  onClose,
  onManage,
}: {
  tools: Tool[]
  enabled: string[]
  onToggle: (id: string) => void
  onClose: () => void
  onManage: () => void
}) {
  const sorted = [...tools].sort((a, b) => {
    if (a.configured !== b.configured) return a.configured ? -1 : 1
    return a.name.localeCompare(b.name)
  })

  return (
    <>
      <div className="fixed inset-0 z-10" onClick={onClose} />
      <div
        className="absolute right-0 bottom-[calc(100%+6px)] z-20 w-72 max-h-[340px] flex flex-col rounded-[10px] anim-pop"
        style={{ background: 'var(--elev2)', border: '1px solid var(--line)', boxShadow: 'var(--shadow)' }}
      >
        <div className="flex items-center justify-between px-3 py-2 border-b" style={{ borderColor: 'var(--line)' }}>
          <span className="eyebrow">Tools for this run</span>
          {enabled.length > 0 && (
            <span className="font-code text-[10.5px]" style={{ color: 'var(--accent)' }}>{enabled.length} on</span>
          )}
        </div>
        <div className="flex-1 overflow-y-auto p-1.5">
          {sorted.length === 0 ? (
            <p className="text-center py-4 text-[12px]" style={{ color: 'var(--faint)' }}>No tools available.</p>
          ) : (
            sorted.map((t) => {
              const on = enabled.includes(t.id)
              return (
                <button
                  key={t.id}
                  onClick={() => onToggle(t.id)}
                  className="flex items-center gap-2 w-full px-2 py-1.5 rounded-md hover:bg-[var(--panel)] transition-colors text-left"
                >
                  <span
                    className="flex-none w-4 h-4 grid place-items-center rounded-[5px] border text-[10px] font-bold"
                    style={{
                      borderColor: on ? 'var(--accent)' : 'var(--line)',
                      background: on ? 'var(--accent)' : 'transparent',
                      color: '#0b0f18',
                    }}
                  >
                    {on ? '✓' : ''}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[12.5px] truncate" style={{ color: 'var(--text)' }}>{t.name}</span>
                    {!t.configured && (
                      <span className="block font-code text-[9.5px]" style={{ color: 'var(--faint)' }}>not set up</span>
                    )}
                  </span>
                </button>
              )
            })
          )}
        </div>
        <button
          onClick={onManage}
          className="px-3 py-2 border-t text-[11.5px] text-left hover:bg-[var(--panel)] transition-colors"
          style={{ borderColor: 'var(--line)', color: 'var(--accent)' }}
        >
          Manage tools →
        </button>
      </div>
    </>
  )
}
