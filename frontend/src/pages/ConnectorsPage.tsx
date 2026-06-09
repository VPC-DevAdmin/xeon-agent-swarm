import { useEffect, useState } from 'react'
import { connectorsApi } from '../api/client'
import { CONNECTOR_KINDS, type Connector, type ConnectorCreate } from '../api/types'
import { Button, Card, Empty, Field, StatusBadge, inputClass, timeAgo } from '../components/ui'

export function ConnectorsPage() {
  const [connectors, setConnectors] = useState<Connector[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    try {
      setConnectors(await connectorsApi.list())
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  async function act(fn: () => Promise<unknown>) {
    try { await fn(); await refresh() } catch (e) { setError(String(e)) }
  }

  return (
    <div className="max-w-4xl mx-auto px-6 py-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-lg font-semibold text-white">Connectors</h1>
        <Button variant="primary" onClick={() => setShowCreate(true)}>+ New Connector</Button>
      </div>

      <p className="text-xs text-gray-500 mb-4">
        Tool & credential integrations. Secrets are encrypted at rest and never
        returned by the API — only the field names are shown.
      </p>

      {error && (
        <div className="mb-3 text-xs text-red-400 bg-red-950/40 border border-red-900 rounded px-3 py-2">
          {error}
        </div>
      )}

      {loading ? (
        <Empty message="Loading…" />
      ) : connectors.length === 0 ? (
        <Empty message="No connectors yet." />
      ) : (
        <div className="space-y-2">
          {connectors.map((c) => (
            <Card key={c.id} className="p-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-gray-100">{c.name}</span>
                    <span className="text-[11px] text-blue-400 font-mono">{c.kind}</span>
                    <StatusBadge status={c.status} />
                  </div>
                  <div className="text-[11px] text-gray-600">
                    secrets: {c.secret_fields.length
                      ? c.secret_fields.map((f) => <span key={f} className="font-mono mr-2">🔒 {f}</span>)
                      : <span className="text-gray-700">none</span>}
                  </div>
                  <div className="text-[11px] text-gray-700 mt-1">created {timeAgo(c.created_at)}</div>
                </div>
                {c.status === 'active' && (
                  <Button variant="ghost" onClick={() => act(() => connectorsApi.revoke(c.id))}>
                    Revoke
                  </Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      {showCreate && (
        <CreateConnectorModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); refresh() }}
        />
      )}
    </div>
  )
}

function CreateConnectorModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState<ConnectorCreate>({ name: '', kind: 'router', config: {}, secrets: {} })
  const [secretRows, setSecretRows] = useState<{ field: string; value: string }[]>([])
  const [configText, setConfigText] = useState('{}')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit() {
    setSaving(true)
    setErr(null)
    let config: Record<string, unknown> = {}
    try {
      config = JSON.parse(configText || '{}')
    } catch {
      setErr('Config must be valid JSON')
      setSaving(false)
      return
    }
    const secrets: Record<string, string> = {}
    for (const r of secretRows) if (r.field.trim()) secrets[r.field.trim()] = r.value
    try {
      await connectorsApi.create({ ...form, config, secrets })
      onCreated()
    } catch (e) {
      setErr(String(e))
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <Card className="w-full max-w-lg p-5">
        <div onClick={(e) => e.stopPropagation()}>
          <h2 className="text-sm font-semibold text-white mb-4">New Connector</h2>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Name">
                <input className={inputClass} value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="slack-primary" />
              </Field>
              <Field label="Kind">
                <select className={inputClass} value={form.kind}
                  onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                  {CONNECTOR_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </Field>
            </div>
            <Field label="Config (JSON — non-secret settings like base_url, scopes)">
              <textarea className={`${inputClass} font-mono`} rows={3} value={configText}
                onChange={(e) => setConfigText(e.target.value)}
                placeholder='{"base_url": "https://..."}' />
            </Field>
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-gray-400">Secrets (encrypted at rest)</span>
                <button className="text-[11px] text-blue-400 hover:underline"
                  onClick={() => setSecretRows([...secretRows, { field: '', value: '' }])}>
                  + add field
                </button>
              </div>
              <div className="space-y-1.5">
                {secretRows.map((r, i) => (
                  <div key={i} className="flex gap-2">
                    <input className={`${inputClass} flex-1`} placeholder="api_key" value={r.field}
                      onChange={(e) => {
                        const next = [...secretRows]; next[i].field = e.target.value; setSecretRows(next)
                      }} />
                    <input className={`${inputClass} flex-1`} type="password" placeholder="value" value={r.value}
                      onChange={(e) => {
                        const next = [...secretRows]; next[i].value = e.target.value; setSecretRows(next)
                      }} />
                  </div>
                ))}
              </div>
            </div>
          </div>
          {err && <div className="mt-3 text-xs text-red-400">{err}</div>}
          <div className="flex justify-end gap-2 mt-5">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button variant="primary" onClick={submit} disabled={saving || !form.name}>
              {saving ? 'Creating…' : 'Create Connector'}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  )
}
