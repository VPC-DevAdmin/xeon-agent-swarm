import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { agentDefsApi, toolsApi } from '../../api/client'
import type { AgentDefinition, Tool } from '../../api/types'
import { SCHEDULE_PRESETS } from './Composer'

/**
 * AgentDefinitions — the persistent-agent product surface.
 *
 * A DEFINITION is a configured agent (instructions, tools, gates, budgets,
 * schedule) that outlives any single run. From here: create, inspect, edit
 * (version-bumped), clone, archive, and run once as a test.
 */
export function AgentDefinitions({ onRunStarted }: { onRunStarted: (runId: string) => void }) {
  const [defs, setDefs] = useState<AgentDefinition[]>([])
  const [editing, setEditing] = useState<AgentDefinition | 'new' | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = () => agentDefsApi.list().then(setDefs).catch(() => {})
  useEffect(() => { refresh() }, [])

  async function runOnce(d: AgentDefinition) {
    setBusy(d.id)
    try {
      const { run_id } = await agentDefsApi.runOnce(d.id)
      onRunStarted(run_id)
    } catch { /* surfaced by refresh state */ }
    finally { setBusy(null) }
  }

  return (
    <>
      <div className="flex items-center gap-2 px-1.5 pt-1">
        <span className="eyebrow">Agent definitions</span>
        <button onClick={() => setEditing('new')}
          className="ml-auto text-[11px] px-2 py-0.5 rounded-md font-medium"
          style={{ background: 'var(--accent)', color: '#0b0f18' }}>
          + New agent
        </button>
      </div>
      {defs.length === 0 && (
        <p className="px-1.5 text-[12px] text-[var(--faint)]">
          No persistent agents yet — define one and it can run once, on a
          schedule, or inside the capacity benchmark.
        </p>
      )}
      {defs.map((d) => (
        <div key={d.id} className="console-card p-3">
          <div className="flex items-center gap-2">
            <span className="text-[15px] flex-none">{d.icon}</span>
            <span className="text-[13px] font-medium truncate flex-1" title={d.purpose ?? ''}>
              {d.name}
            </span>
            <span className="font-code text-[9.5px] px-1.5 py-0.5 rounded-full flex-none"
              title={`version ${d.version} · ${d.history.length} prior version${d.history.length === 1 ? '' : 's'} kept`}
              style={{ background: 'var(--elev2)', color: 'var(--muted)' }}>
              v{d.version}
            </span>
          </div>
          <p className="text-[11px] text-[var(--faint)] mt-1 line-clamp-2">{d.instructions}</p>
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap font-code text-[9.5px]" style={{ color: 'var(--muted)' }}>
            {d.enabled_tools.length > 0 && <span className="px-1.5 py-0.5 rounded-full" style={{ background: 'var(--elev2)' }}>🧰 {d.enabled_tools.length} tools</span>}
            {d.plan_approval && <span className="px-1.5 py-0.5 rounded-full" style={{ background: 'var(--elev2)', color: 'var(--warn)' }}>plan review</span>}
            {d.schedule_cron && <span className="px-1.5 py-0.5 rounded-full" style={{ background: 'var(--elev2)' }}>🕘 scheduled</span>}
            {d.budgets && <span className="px-1.5 py-0.5 rounded-full" style={{ background: 'var(--elev2)' }}>budgeted</span>}
          </div>
          <div className="flex gap-1.5 mt-2">
            <button onClick={() => runOnce(d)} disabled={busy === d.id}
              className="flex-1 py-1.5 rounded-lg text-[11.5px] font-code font-medium disabled:opacity-50"
              style={{ background: 'var(--accent)', color: '#0b0f18' }}>
              {busy === d.id ? 'starting…' : '▶ Run once'}
            </button>
            <button title="Edit (bumps version)" onClick={() => setEditing(d)}
              className="w-8 h-8 grid place-items-center rounded-lg border text-[var(--faint)] hover:text-[var(--text)]"
              style={{ borderColor: 'var(--line)' }}>✎</button>
            <button title="Clone" onClick={() => agentDefsApi.clone(d.id).then(refresh)}
              className="w-8 h-8 grid place-items-center rounded-lg border text-[var(--faint)] hover:text-[var(--text)]"
              style={{ borderColor: 'var(--line)' }}>⧉</button>
            <button title="Archive" onClick={() => agentDefsApi.archive(d.id).then(refresh)}
              className="w-8 h-8 grid place-items-center rounded-lg border text-[var(--faint)] hover:!text-[var(--bad)]"
              style={{ borderColor: 'var(--line)' }}>🗑</button>
          </div>
        </div>
      ))}
      {editing && (
        <DefinitionBuilder
          existing={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); refresh() }}
        />
      )}
    </>
  )
}

/* ── builder modal ───────────────────────────────────────────────────────────── */

function DefinitionBuilder({ existing, onClose, onSaved }:
  { existing: AgentDefinition | null; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(existing?.name ?? '')
  const [icon, setIcon] = useState(existing?.icon ?? '🤖')
  const [purpose, setPurpose] = useState(existing?.purpose ?? '')
  const [instructions, setInstructions] = useState(existing?.instructions ?? '')
  const [tools, setTools] = useState<Tool[]>([])
  const [enabledTools, setEnabledTools] = useState<string[]>(existing?.enabled_tools ?? [])
  const [planApproval, setPlanApproval] = useState(existing?.plan_approval ?? false)
  const [schedule, setSchedule] = useState<string | null>(existing?.schedule_cron ?? null)
  const [maxSub, setMaxSub] = useState<string>(String(existing?.budgets?.max_subagents ?? ''))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { toolsApi.list().then((r) => setTools(r.tools)).catch(() => {}) }, [])

  async function save() {
    if (!name.trim() || !instructions.trim()) { setError('Name and instructions are required.'); return }
    setBusy(true); setError(null)
    const body = {
      name: name.trim(), icon: icon.trim() || '🤖',
      purpose: purpose.trim() || undefined,
      instructions: instructions.trim(),
      enabled_tools: enabledTools,
      plan_approval: planApproval,
      budgets: maxSub ? { max_subagents: Number(maxSub) } : null,
      ...(schedule ? { schedule_cron: schedule } : existing?.schedule_cron ? { clear_schedule: true } : {}),
    }
    try {
      if (existing) await agentDefsApi.update(existing.id, body)
      else await agentDefsApi.create(body)
      onSaved()
    } catch (e) { setError(e instanceof Error ? e.message : 'save failed') }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-xl mx-4 max-h-[85vh] overflow-y-auto rounded-lg border p-5"
        style={{ background: 'var(--panel)', borderColor: 'var(--line)', boxShadow: 'var(--shadow)' }}
        onClick={(e) => e.stopPropagation()}>
        <h3 className="font-display font-semibold text-[16px] m-0 mb-3">
          {existing ? `Edit agent — v${existing.version} → v${existing.version + 1}` : 'New agent definition'}
        </h3>

        <div className="flex gap-2 mb-2.5">
          <input value={icon} onChange={(e) => setIcon(e.target.value)} maxLength={4}
            className="w-14 text-center bg-[var(--ink)] border rounded-lg px-2 py-2 text-[16px]"
            style={{ borderColor: 'var(--line)' }} title="Icon (emoji)" />
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Agent name"
            className="flex-1 bg-[var(--ink)] border rounded-lg px-3 py-2 text-[14px]"
            style={{ borderColor: 'var(--line)' }} />
        </div>
        <input value={purpose} onChange={(e) => setPurpose(e.target.value)}
          placeholder="Purpose (one line, shown on the card)"
          className="w-full bg-[var(--ink)] border rounded-lg px-3 py-2 text-[12.5px] mb-2.5"
          style={{ borderColor: 'var(--line)' }} />
        <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)}
          placeholder="Standing instructions — what this agent does every time it runs…"
          rows={4}
          className="w-full bg-[var(--ink)] border rounded-lg px-3 py-2 text-[13px] resize-none mb-2.5"
          style={{ borderColor: 'var(--line)' }} />

        <div className="eyebrow mb-1.5">Tools this agent may use</div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-1 mb-3 max-h-40 overflow-y-auto">
          {tools.map((t) => {
            const on = enabledTools.includes(t.id)
            return (
              <button key={t.id}
                onClick={() => setEnabledTools((p) => on ? p.filter((x) => x !== t.id) : [...p, t.id])}
                className={clsx('text-left px-2 py-1.5 rounded-md border text-[11.5px] truncate transition-colors',
                  on ? 'text-[var(--text)]' : 'text-[var(--faint)]')}
                style={{ borderColor: on ? 'rgba(124,135,245,.45)' : 'var(--line)',
                         background: on ? 'var(--elev)' : 'transparent' }}
                title={t.description}>
                {on ? '✓ ' : ''}{t.name}
              </button>
            )
          })}
        </div>

        <div className="flex items-center gap-4 mb-3 flex-wrap text-[12px]">
          <label className="flex items-center gap-2 cursor-pointer" style={{ color: 'var(--muted)' }}>
            <input type="checkbox" checked={planApproval} onChange={(e) => setPlanApproval(e.target.checked)} />
            Review plan before each run
          </label>
          <label className="flex items-center gap-2" style={{ color: 'var(--muted)' }}>
            Schedule
            <select value={schedule ?? ''} onChange={(e) => setSchedule(e.target.value || null)}
              className="bg-[var(--ink)] border rounded px-2 py-1 text-[12px]"
              style={{ borderColor: 'var(--line)' }}>
              <option value="">none (on demand)</option>
              {SCHEDULE_PRESETS.filter((p) => p.cron).map((p) => (
                <option key={p.id} value={p.cron!}>{p.label}</option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2" style={{ color: 'var(--muted)' }}>
            Max workers
            <input type="number" min={1} max={16} value={maxSub}
              onChange={(e) => setMaxSub(e.target.value)} placeholder="default"
              className="w-20 bg-[var(--ink)] border rounded px-2 py-1 font-code text-[12px]"
              style={{ borderColor: 'var(--line)' }} />
          </label>
        </div>

        {error && <p className="text-[12px] mb-2" style={{ color: 'var(--bad)' }}>{error}</p>}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-1.5 rounded-lg border text-[13px] text-[var(--muted)]"
            style={{ borderColor: 'var(--line)' }}>Cancel</button>
          <button onClick={save} disabled={busy}
            className="px-4 py-1.5 rounded-lg text-[13px] font-medium disabled:opacity-50"
            style={{ background: 'var(--accent)', color: '#0b0f18' }}>
            {busy ? 'Saving…' : existing ? 'Save new version' : 'Create agent'}
          </button>
        </div>
      </div>
    </div>
  )
}
