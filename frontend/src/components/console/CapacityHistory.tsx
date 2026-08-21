import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { capacityApi } from '../../api/client'
import type { CapacityHistoryRow, CapacityResult } from '../../api/types'
import { parseServerDate } from '../../lib/thread'

function dimensions(r: CapacityHistoryRow) {
  return {
    target: r.benchmark_target ?? (r.mode === 'e2e' ? 'agent_host' : 'inference_engine'),
    backend: r.inference_backend ?? (r.mode === 'e2e' ? 'remote_mock' : r.mode),
  }
}

function dimensionLabel(r: CapacityHistoryRow) {
  const { target, backend } = dimensions(r)
  const t = target === 'agent_host' ? 'agent host' : target === 'integrated_node' ? 'integrated' : 'inference'
  const b = backend === 'remote_mock' ? 'mock' : backend === 'remote_real' ? 'cloud' : 'local'
  return `${t} · ${b}`
}

/**
 * CapacityHistory — DB-persisted benchmark history: view any past result,
 * compare two runs side by side (with a non-comparability warning when the
 * workload/mode differ), export the full JSON, label, delete.
 */
export function CapacityHistory({ activePhase, onView }:
  { activePhase: string; onView: (r: CapacityResult) => void }) {
  const [rows, setRows] = useState<CapacityHistoryRow[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [compare, setCompare] = useState<(CapacityHistoryRow & { result: CapacityResult })[] | null>(null)

  const refresh = () => capacityApi.history().then(setRows).catch(() => {})
  useEffect(() => { refresh() }, [activePhase])   // refetch when a test finishes

  useEffect(() => {
    if (selected.length !== 2) { setCompare(null); return }
    Promise.all(selected.map((id) => capacityApi.historyGet(id)))
      .then(setCompare).catch(() => setCompare(null))
  }, [selected])

  if (rows.length === 0) return null

  return (
    <div className="console-panel p-4 mt-4">
      <div className="flex items-center gap-2 mb-2.5">
        <span className="eyebrow">Benchmark history</span>
        <span className="font-code text-[10.5px] text-[var(--faint)]">
          select two to compare · stored in the database
        </span>
      </div>
      <div className="flex flex-col gap-0.5">
        {rows.map((r) => {
          const sel = selected.includes(r.id)
          return (
            <div key={r.id}
              className={clsx('flex items-center gap-2.5 px-2 py-1.5 rounded-md text-[12px] transition-colors',
                sel ? 'bg-[var(--elev)]' : 'hover:bg-[var(--elev)]')}>
              <button title="Select for compare"
                onClick={() => setSelected((p) => sel ? p.filter((x) => x !== r.id)
                  : p.length >= 2 ? [p[1], r.id] : [...p, r.id])}
                className="flex-none w-3.5 h-3.5 grid place-items-center rounded border"
                style={{ borderColor: sel ? 'var(--accent)' : 'var(--line)',
                         background: sel ? 'var(--accent)' : 'transparent' }}>
                {sel && <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="#0b0f18" strokeWidth="3.5"><path d="M20 6 9 17l-5-5" /></svg>}
              </button>
              <span className="font-code text-[10.5px] text-[var(--faint)] w-24 flex-none">
                {parseServerDate(r.started_at)?.toLocaleDateString([], { month: 'short', day: 'numeric' })}{' '}
                {parseServerDate(r.started_at)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
              <span className="font-code text-[10px] px-1.5 py-0.5 rounded-full flex-none"
                style={{ background: 'var(--elev2)', color: 'var(--muted)' }}>
                {dimensionLabel(r)} · {r.mix}
              </span>
              {r.comparable === false && (
                <span className="font-code text-[9px] flex-none" style={{ color: 'var(--warn)' }}>non-comp</span>
              )}
              <span className="flex-1 truncate text-[var(--text)]">
                {(r.capacity_certified ?? true) && ['capped', 'timeout', 'budget', 'spend_guard'].includes(r.verdict ?? '') ? '≥' : ''}
                {r.mix === 'tile' && r.capacity_tiles != null
                  ? `${r.capacity_tiles} tiles (${r.capacity_users})`
                  : `${r.capacity_users ?? '—'} sessions`}
                <span className="text-[var(--faint)]"> · {r.verdict}</span>
                {r.label && <span style={{ color: 'var(--t3)' }}> · “{r.label}”</span>}
              </span>
              <span className="font-code text-[10.5px] text-[var(--faint)] flex-none">
                {r.workflows_per_hour != null ? `${r.workflows_per_hour}/h`
                  : r.steady_tps != null ? `${r.steady_tps} tok/s` : ''}
                {r.steady_cost_per_hour != null ? ` · $${r.steady_cost_per_hour.toFixed(2)}/h` : ''}
              </span>
              <button onClick={() => capacityApi.historyGet(r.id).then((f) => onView(f.result))}
                className="flex-none text-[10.5px] px-2 py-0.5 rounded border text-[var(--muted)] hover:text-[var(--text)]"
                style={{ borderColor: 'var(--line)' }}>view</button>
              <a href={capacityApi.exportUrl(r.id)} download
                className="flex-none text-[10.5px] px-2 py-0.5 rounded border text-[var(--muted)] hover:text-[var(--text)] no-underline"
                style={{ borderColor: 'var(--line)' }}>export</a>
              <button title="Label"
                onClick={() => { const l = prompt('Label this run', r.label ?? ''); if (l !== null) capacityApi.historyLabel(r.id, l || null).then(refresh) }}
                className="flex-none text-[10.5px] w-6 h-6 rounded border text-[var(--faint)] hover:text-[var(--text)]"
                style={{ borderColor: 'var(--line)' }}>✎</button>
              <button title="Delete"
                onClick={() => capacityApi.historyDelete(r.id).then(refresh)}
                className="flex-none text-[10.5px] w-6 h-6 rounded border text-[var(--faint)] hover:!text-[var(--bad)]"
                style={{ borderColor: 'var(--line)' }}>×</button>
            </div>
          )
        })}
      </div>
      {compare && <CompareCard a={compare[0]} b={compare[1]} />}
    </div>
  )
}

function CompareCard({ a, b }: {
  a: CapacityHistoryRow & { result: CapacityResult }
  b: CapacityHistoryRow & { result: CapacityResult }
}) {
  const sameWorkload = a.scenario_fingerprint === b.scenario_fingerprint
    && dimensions(a).target === dimensions(b).target
    && dimensions(a).backend === dimensions(b).backend
    && a.mix === b.mix && a.cache_mode === b.cache_mode
    && a.result.cloud_model?.id === b.result.cloud_model?.id
  const rows: [string, (r: CapacityResult) => number | null | undefined, string][] = [
    ['capacity (sessions)', (r) => r.capacity_users, ''],
    ['capacity (tiles)', (r) => r.capacity_tiles, ''],
    ['throughput', (r) => r.steady?.tps, ' tok/s'],
    ['workflows/hour', (r) => r.workflows_per_hour, ''],
    ['cloud cost/hour', (r) => r.cost?.steady_cost_per_hour, ' USD'],
    ['run cloud cost', (r) => r.cost?.run_total_usd, ' USD'],
    ['p50', (r) => r.steady?.p50_ms, ' ms'],
    ['p95', (r) => r.steady?.p95_ms, ' ms'],
    ['CPU', (r) => r.steady?.cpu_pct, ' %'],
    ['memory', (r) => r.steady?.mem_pct, ' %'],
    ['power', (r) => r.steady?.power_w, ' W'],
    ['duration', (r) => r.duration_s, ' s'],
  ]
  return (
    <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--line-soft)' }}>
      <div className="eyebrow mb-1.5">Compare</div>
      {!sameWorkload && (
        <p className="text-[11.5px] mb-2" style={{ color: 'var(--warn)' }}>
          ⚠ different {dimensions(a).target !== dimensions(b).target ? 'benchmark target'
            : dimensions(a).backend !== dimensions(b).backend ? 'inference backend'
            : a.result.cloud_model?.id !== b.result.cloud_model?.id ? 'cloud model'
            : a.mix !== b.mix ? 'mix'
            : a.cache_mode !== b.cache_mode ? 'cache mode' : 'scenario version'} —
          these runs are not directly comparable
        </p>
      )}
      <div className="grid gap-y-1 text-[12px]" style={{ gridTemplateColumns: '1fr auto auto auto' }}>
        <span />
        <span className="font-code text-[10.5px] text-[var(--muted)] px-3">A · seed {a.seed}</span>
        <span className="font-code text-[10.5px] text-[var(--muted)] px-3">B · seed {b.seed}</span>
        <span className="font-code text-[10.5px] text-[var(--faint)] px-2">Δ</span>
        {rows.map(([label, get, unit]) => {
          const va = get(a.result); const vb = get(b.result)
          if (va == null && vb == null) return null
          const delta = va != null && vb != null && va !== 0
            ? ((vb - va) / Math.abs(va)) * 100 : null
          return [
            <span key={label + 'l'} className="text-[var(--faint)]">{label}</span>,
            <span key={label + 'a'} className="font-code px-3 text-[var(--text)]">{va != null ? `${va}${unit}` : '—'}</span>,
            <span key={label + 'b'} className="font-code px-3 text-[var(--text)]">{vb != null ? `${vb}${unit}` : '—'}</span>,
            <span key={label + 'd'} className="font-code px-2"
              style={{ color: delta == null ? 'var(--faint)' : delta >= 0 ? 'var(--ok)' : 'var(--bad)' }}>
              {delta != null ? `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}%` : ''}
            </span>,
          ]
        })}
      </div>
    </div>
  )
}
