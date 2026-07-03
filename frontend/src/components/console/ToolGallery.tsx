import { useEffect, useMemo, useState } from 'react'
import { connectorsApi, toolsApi } from '../../api/client'
import type { Tool } from '../../api/types'

interface Props {
  open: boolean
  onClose: () => void
  onConfigured?: () => void
}

// Capability → token color. Anything unrecognized falls back to muted.
const CAP_COLOR: Record<string, string> = {
  read: 'var(--t1)',
  write: 'var(--t4)',
  notify: 'var(--t3)',
  compute: 'var(--accent)',
}

function capColor(cap: string): string {
  return CAP_COLOR[cap.toLowerCase()] ?? 'var(--muted)'
}

/**
 * Configure a tool by creating (or reusing) a connector named exactly the tool
 * id, kind 'tool'. Secret fields go into `secrets`, everything else into config.
 */
async function configureTool(tool: Tool, values: Record<string, string>) {
  const config: Record<string, unknown> = {}
  const secrets: Record<string, string> = {}
  for (const f of tool.setup) {
    const v = values[f.field] ?? ''
    if (f.secret) secrets[f.field] = v
    else config[f.field] = v
  }
  try {
    await connectorsApi.create({ name: tool.id, kind: 'tool', config, secrets })
  } catch (e) {
    // Already exists (409) → patch config + push each secret.
    const msg = String(e)
    if (!msg.includes('409')) throw e
    const existing = (await connectorsApi.list()).find((c) => c.name === tool.id)
    if (!existing) throw e
    await connectorsApi.update(existing.id, { config })
    for (const [field, value] of Object.entries(secrets)) {
      await connectorsApi.setSecret(existing.id, field, value)
    }
  }
}

/**
 * ToolGallery — a browsable catalog of tools the swarm can use, grouped by
 * category. Each tool can be set up inline (its connector credentials) and
 * shows whether it's already configured. Mirrors SamplePromptGallery's modal.
 */
export function ToolGallery({ open, onClose, onConfigured }: Props) {
  const [tools, setTools] = useState<Tool[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [category, setCategory] = useState('All')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [setupId, setSetupId] = useState<string | null>(null)

  const refresh = async () => {
    try {
      const res = await toolsApi.list()
      setTools(res.tools)
      setCategories(res.categories)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) refresh()
  }, [open])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return tools.filter((t) => {
      if (category !== 'All' && t.category !== category) return false
      if (q && !`${t.name} ${t.description} ${t.capabilities.join(' ')}`.toLowerCase().includes(q)) return false
      return true
    })
  }, [tools, category, search])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm anim-pop"
      onClick={onClose}
    >
      <div
        className="console-panel w-full max-w-4xl mx-4 max-h-[82vh] flex flex-col"
        style={{ borderRadius: 14 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* header */}
        <div className="flex items-center gap-3 px-5 py-3 border-b" style={{ borderColor: 'var(--line)' }}>
          <span className="font-display font-semibold text-[14px]" style={{ color: 'var(--text)' }}>Tools</span>
          <span className="font-code text-[11px]" style={{ color: 'var(--faint)' }}>
            {filtered.length} of {tools.length}
          </span>
          <input
            autoFocus
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            className="ml-auto w-56 rounded-lg px-2.5 py-1 text-[12px] outline-none focus:!border-[#38425a] transition-colors"
            style={{ background: 'var(--ink)', border: '1px solid var(--line)', color: 'var(--text)' }}
          />
          <button
            onClick={onClose}
            className="text-[18px] leading-none w-7 h-7 grid place-items-center rounded-lg hover:bg-[var(--elev)] transition-colors"
            style={{ color: 'var(--muted)' }}
          >
            ×
          </button>
        </div>

        {/* category filter */}
        <div className="flex gap-1.5 px-5 py-2.5 border-b overflow-x-auto" style={{ borderColor: 'var(--line)' }}>
          {['All', ...categories].map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className="flex-shrink-0 font-code text-[11px] px-2.5 py-1 rounded-full border transition-colors"
              style={
                category === c
                  ? { background: 'rgba(124,135,245,.18)', color: 'var(--accent)', borderColor: 'rgba(124,135,245,.5)' }
                  : { color: 'var(--muted)', borderColor: 'var(--line)' }
              }
            >
              {c}
            </button>
          ))}
        </div>

        {/* body */}
        <div className="flex-1 overflow-y-auto p-4">
          {error && (
            <div
              className="mb-3 text-[12.5px] rounded-lg px-3 py-2"
              style={{ color: 'var(--bad)', background: 'rgba(229,106,130,.1)', border: '1px solid rgba(229,106,130,.35)' }}
            >
              {error}
            </div>
          )}
          {loading ? (
            <p className="text-center py-12 text-[13px]" style={{ color: 'var(--faint)' }}>Loading…</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
              {filtered.map((t) => (
                <ToolCard
                  key={t.id}
                  tool={t}
                  editing={setupId === t.id}
                  onToggleSetup={() => setSetupId((v) => (v === t.id ? null : t.id))}
                  onSaved={async () => {
                    setSetupId(null)
                    await refresh()
                    onConfigured?.()
                  }}
                />
              ))}
              {filtered.length === 0 && (
                <p className="col-span-2 text-center py-10 text-[13px]" style={{ color: 'var(--faint)' }}>
                  No tools match.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ToolCard({
  tool,
  editing,
  onToggleSetup,
  onSaved,
}: {
  tool: Tool
  editing: boolean
  onToggleSetup: () => void
  onSaved: () => void
}) {
  return (
    <div className="console-card p-3 flex flex-col gap-2" style={{ background: 'var(--ink)' }}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[13.5px] font-medium" style={{ color: 'var(--text)' }}>{tool.name}</span>
            {tool.backing === 'stub' && (
              <span
                className="font-code text-[9.5px] px-1.5 py-0.5 rounded-full"
                style={{ color: 'var(--faint)', border: '1px solid var(--line)' }}
              >
                catalog only
              </span>
            )}
          </div>
          <div className="flex gap-1 flex-wrap mt-1">
            {tool.capabilities.map((cap) => (
              <span
                key={cap}
                className="font-code text-[9.5px] px-1.5 py-0.5 rounded-full"
                style={{ color: capColor(cap), border: `1px solid ${capColor(cap)}`, opacity: 0.9 }}
              >
                {cap}
              </span>
            ))}
          </div>
        </div>
        {tool.configured ? (
          <span
            className="flex-none font-code text-[10.5px] px-2 py-1 rounded-md whitespace-nowrap"
            style={{ color: 'var(--ok)', background: 'rgba(87,201,138,.12)', border: '1px solid rgba(87,201,138,.4)' }}
          >
            Configured ✓
          </span>
        ) : tool.setup.length > 0 ? (
          <button
            onClick={onToggleSetup}
            className="flex-none font-code text-[10.5px] px-2.5 py-1 rounded-md whitespace-nowrap transition-colors"
            style={{ color: 'var(--accent)', background: 'var(--elev)', border: '1px solid rgba(124,135,245,.4)' }}
          >
            {editing ? 'Cancel' : 'Set up'}
          </button>
        ) : (
          <span className="flex-none font-code text-[10.5px]" style={{ color: 'var(--faint)' }}>no setup</span>
        )}
      </div>

      <p className="text-[12px] leading-snug" style={{ color: 'var(--muted)' }}>{tool.description}</p>

      {editing && <SetupForm tool={tool} onSaved={onSaved} />}
    </div>
  )
}

function SetupForm({ tool, onSaved }: { tool: Tool; onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    setSaving(true)
    setErr(null)
    try {
      await configureTool(tool, values)
      onSaved()
    } catch (e) {
      setErr(String(e))
      setSaving(false)
    }
  }

  return (
    <div className="mt-1 pt-2.5 border-t flex flex-col gap-2" style={{ borderColor: 'var(--line)' }}>
      {tool.setup.map((f) => (
        <label key={f.field} className="block">
          <span className="eyebrow block mb-1">{f.label}{f.secret ? ' 🔒' : ''}</span>
          <input
            type={f.secret ? 'password' : 'text'}
            value={values[f.field] ?? ''}
            onChange={(e) => setValues((v) => ({ ...v, [f.field]: e.target.value }))}
            className="w-full rounded-lg px-2.5 py-1.5 text-[12.5px] outline-none focus:!border-[#38425a] transition-colors"
            style={{ background: 'var(--panel)', border: '1px solid var(--line)', color: 'var(--text)' }}
          />
        </label>
      ))}
      {err && <p className="text-[11.5px]" style={{ color: 'var(--bad)' }}>{err}</p>}
      <button
        onClick={submit}
        disabled={saving}
        className="self-end font-semibold text-[12px] px-3 py-1.5 rounded-lg transition-opacity disabled:opacity-40"
        style={{ background: 'var(--accent)', color: '#0b0f18' }}
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  )
}
