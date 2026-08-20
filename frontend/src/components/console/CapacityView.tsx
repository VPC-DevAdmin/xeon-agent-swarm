import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { capacityApi } from '../../api/client'
import type {
  CapacityEngine, CapacityResult, CapacityScenario,
  CapacitySample, CapacityStatus,
} from '../../api/types'

type Mode = 'local' | 'remote_mock' | 'remote_real'

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: 'local', label: 'Local LLM', hint: 'Qwen3 on this server — the real capacity test' },
  { id: 'remote_mock', label: 'Remote (simulated)', hint: 'bell-curve latency, zero API calls' },
  { id: 'remote_real', label: 'Remote (cloud)', hint: 'real API endpoint — spends credits' },
]

const COMPLEXITY_COLOR: Record<string, string> = {
  light: 'var(--t2)', medium: 'var(--t3)', heavy: 'var(--t5)',
}

/**
 * CapacityView — the built-in system speed test.
 *
 * Pick a target (local engine / simulated remote / real cloud), pick which of
 * the five fixed agent scenarios to mix, hit Start: virtual users are added
 * until the box shows consistent saturation, then it holds, measures a clean
 * steady state, and reports the result like a bandwidth test.
 */
export function CapacityView() {
  const [scenarios, setScenarios] = useState<CapacityScenario[]>([])
  const [enabled, setEnabled] = useState<string[]>([])
  const [mode, setMode] = useState<Mode>('remote_mock')
  const [mockMs, setMockMs] = useState(2000)
  const [mockSigma, setMockSigma] = useState(300)
  const [engine, setEngine] = useState<CapacityEngine | null>(null)
  const [status, setStatus] = useState<CapacityStatus | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    capacityApi.scenarios().then((r) => {
      setScenarios(r.scenarios)
      setEnabled(r.scenarios.map((s) => s.id))
    }).catch(() => {})
  }, [])

  const pollEngine = useCallback(() => {
    capacityApi.engine().then(setEngine).catch(() => {})
  }, [])
  useEffect(() => {
    pollEngine()
    const t = setInterval(pollEngine, 5000)
    return () => clearInterval(t)
  }, [pollEngine])

  useEffect(() => {
    const poll = () => capacityApi.status().then(setStatus).catch(() => {})
    poll()
    const t = setInterval(poll, 2000)
    return () => clearInterval(t)
  }, [])

  const active = status?.active ?? false
  const result: CapacityResult | null = status?.result ?? null

  async function start() {
    setBusy(true); setError(null)
    try {
      await capacityApi.start({
        mode,
        scenarios: enabled,
        mock_ms: mode === 'remote_mock' ? mockMs : undefined,
        mock_sigma: mode === 'remote_mock' ? mockSigma : undefined,
        confirm_real: mode === 'remote_real' ? true : undefined,
      })
      const s = await capacityApi.status(); setStatus(s)
    } catch (e) { setError(e instanceof Error ? e.message : 'start failed') }
    finally { setBusy(false) }
  }

  async function stop() {
    setBusy(true)
    try { await capacityApi.stop() } catch { /* already stopped */ }
    finally { setBusy(false) }
  }

  return (
    <div className="max-w-[820px] mx-auto pb-10">
      {/* mode + engine row */}
      <div className="flex flex-wrap items-stretch gap-3 mb-4">
        <div className="console-panel flex-1 min-w-[340px] p-3.5">
          <div className="eyebrow mb-2">Target</div>
          <div className="flex gap-1 p-[3px] rounded-[10px] border w-fit"
            style={{ background: 'var(--ink)', borderColor: 'var(--line)' }}>
            {MODES.map((m) => (
              <button key={m.id} disabled={active}
                onClick={() => setMode(m.id)}
                className={clsx('px-3 py-1.5 rounded-[7px] text-[12.5px] font-medium transition-colors disabled:opacity-60',
                  mode === m.id ? 'bg-[var(--elev)] text-[var(--text)]' : 'text-[var(--muted)] hover:text-[var(--text)]')}>
                {m.label}
              </button>
            ))}
          </div>
          <p className="text-[11.5px] text-[var(--faint)] mt-2">{MODES.find((m) => m.id === mode)?.hint}</p>

          {mode === 'remote_mock' && (
            <div className="flex gap-4 mt-2.5 text-[12px] text-[var(--muted)]">
              <label className="flex items-center gap-2">set point
                <input type="number" value={mockMs} min={100} max={60000} disabled={active}
                  onChange={(e) => setMockMs(Number(e.target.value))}
                  className="w-20 bg-[var(--ink)] border rounded px-2 py-1 font-code text-[12px]"
                  style={{ borderColor: 'var(--line)' }} /> ms
              </label>
              <label className="flex items-center gap-2">σ
                <input type="number" value={mockSigma} min={0} max={20000} disabled={active}
                  onChange={(e) => setMockSigma(Number(e.target.value))}
                  className="w-16 bg-[var(--ink)] border rounded px-2 py-1 font-code text-[12px]"
                  style={{ borderColor: 'var(--line)' }} /> ms
              </label>
            </div>
          )}

          {mode === 'local' && engine && (
            <LocalEngineChip engine={engine} onStart={() => capacityApi.startEngine().then(pollEngine)} />
          )}
          {mode === 'remote_real' && engine && (
            <p className="text-[12px] mt-2.5" style={{ color: engine.remote_real.configured ? 'var(--muted)' : 'var(--warn)' }}>
              {engine.remote_real.configured
                ? <>endpoint configured · model <b className="font-code">{engine.remote_real.model}</b> · hard budget 500 requests</>
                : 'not configured — set CAPACITY_REMOTE_BASE_URL / _MODEL / _API_KEY on the server'}
            </p>
          )}
        </div>

        {/* start / stop */}
        <div className="console-panel w-[210px] p-3.5 flex flex-col items-center justify-center gap-2">
          {!active ? (
            <button onClick={start}
              disabled={busy || enabled.length === 0 || (mode === 'local' && !engine?.serving)
                || (mode === 'remote_real' && !engine?.remote_real.configured)}
              className="w-full py-3 rounded-xl font-display font-semibold text-[15px] transition-opacity disabled:opacity-35"
              style={{ background: 'var(--accent)', color: '#0b0f18' }}>
              Start test
            </button>
          ) : (
            <button onClick={stop} disabled={busy}
              className="w-full py-3 rounded-xl font-display font-semibold text-[15px] border"
              style={{ borderColor: 'rgba(229,106,130,.5)', color: 'var(--bad)' }}>
              Stop
            </button>
          )}
          <p className="font-code text-[10.5px] text-[var(--faint)] text-center">
            {active ? `${status?.phase} · ${Math.round(status?.elapsed_s ?? 0)}s` : 'ramps users until saturation'}
          </p>
        </div>
      </div>

      {error && <p className="text-[12.5px] mb-3" style={{ color: 'var(--bad)' }}>{error}</p>}

      {/* scenario blocks */}
      <div className="console-panel p-3.5 mb-4">
        <div className="eyebrow mb-2">Agent scenarios in the mix — each virtual user runs one block on repeat</div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
          {scenarios.map((s) => {
            const on = enabled.includes(s.id)
            const open = expanded === s.id
            const live = status?.per_scenario?.[s.id]
            return (
              <div key={s.id}
                className={clsx('rounded-lg border p-2.5 transition-colors',
                  on ? 'bg-[var(--elev)]' : 'opacity-45',
                  open && 'md:col-span-2 lg:col-span-3 !opacity-100')}
                style={{ borderColor: open ? 'rgba(124,135,245,.5)' : on ? 'rgba(124,135,245,.35)' : 'var(--line)' }}>
                <div className="flex items-center gap-2">
                  {/* enable toggle */}
                  <button disabled={active} title={on ? 'Remove from the mix' : 'Add to the mix'}
                    onClick={() => setEnabled((prev) => on ? prev.filter((x) => x !== s.id) : [...prev, s.id])}
                    className="flex-none w-4 h-4 grid place-items-center rounded border disabled:cursor-default"
                    style={{ borderColor: on ? 'var(--accent)' : 'var(--line)',
                             background: on ? 'var(--accent)' : 'transparent' }}>
                    {on && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#0b0f18" strokeWidth="3.5"><path d="M20 6 9 17l-5-5" /></svg>}
                  </button>
                  <span className="w-1.5 h-1.5 rounded-full flex-none"
                    style={{ background: COMPLEXITY_COLOR[s.complexity] }} />
                  <span className="text-[13px] font-medium flex-1 truncate">{s.name}</span>
                  <span className="font-code text-[10px] text-[var(--faint)] whitespace-nowrap">
                    {s.calls_per_loop} LLM{s.tool_calls_per_loop > 0 ? ` · ${s.tool_calls_per_loop}⚒` : ''}
                    {s.session_turns > 1 ? ` · ×${s.session_turns}` : ''}
                  </span>
                  {/* loop expander */}
                  <button title={open ? 'Hide the loop' : 'See what this agent does each loop'}
                    onClick={() => setExpanded(open ? null : s.id)}
                    className="flex-none w-5 h-5 grid place-items-center rounded text-[var(--faint)] hover:text-[var(--text)] hover:bg-[var(--elev2)]">
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                      style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }}>
                      <path d="M6 9l6 6 6-6" />
                    </svg>
                  </button>
                </div>
                <p className="text-[11px] text-[var(--faint)] mt-1 line-clamp-2">{s.blurb}</p>
                {active && live && (
                  <p className="font-code text-[10.5px] mt-1.5" style={{ color: 'var(--muted)' }}>
                    {live.users} user{live.users === 1 ? '' : 's'} · {live.calls} calls
                    {live.p50_ms != null && <> · p50 {fmtMs(live.p50_ms)}</>}
                    {live.errors > 0 && <span style={{ color: 'var(--bad)' }}> · {live.errors} err</span>}
                  </p>
                )}
                {open && <ScenarioLoop s={s} />}
              </div>
            )
          })}
        </div>
      </div>

      {/* live dials + charts */}
      {(active || (status?.timeline?.length ?? 0) > 0) && status && (
        <LivePanel status={status} />
      )}

      {/* final result */}
      {!active && result && <ResultCard result={result} />}
    </div>
  )
}

/* ── scenario loop detail ────────────────────────────────────────────────────── */

function ScenarioLoop({ s }: { s: CapacityScenario }) {
  return (
    <div className="mt-2.5 pt-2.5 border-t anim-task-in" style={{ borderColor: 'var(--line-soft)' }}>
      <div className="eyebrow mb-1.5">
        The loop — one virtual user repeats this until the test ends
      </div>
      <div className="flex flex-col gap-1">
        {s.steps.map((st, i) => (
          <div key={i} className="flex items-center gap-2.5 text-[12px] flex-wrap">
            <span className="flex-none min-w-[18px] h-[18px] rounded-full grid place-items-center font-code text-[9.5px]"
              style={{ background: 'rgba(124,135,245,.15)', color: 'var(--accent)' }}>
              {i + 1}
            </span>
            <span className="font-code text-[11.5px] text-[var(--text)] w-32 truncate">{st.label}</span>
            <span className="flex items-center gap-1 flex-1 min-w-[100px]">
              <span className="h-[5px] rounded-sm" title={`~${st.prompt_tokens} base tokens in (prefill)`}
                style={{ width: `${Math.max(3, Math.min(50, st.prompt_tokens / 55))}%`, background: 'var(--t1)' }} />
              <span className="h-[5px] rounded-sm" title={`up to ${st.max_tokens} tokens out (decode)`}
                style={{ width: `${Math.max(3, Math.min(50, st.max_tokens / 55))}%`, background: 'var(--t4)' }} />
            </span>
            <span className="font-code text-[10.5px] text-[var(--muted)] whitespace-nowrap">
              ~{st.prompt_tokens} in → ≤{st.max_tokens} out
            </span>
            {st.carry_context && (
              <span className="font-code text-[9.5px] px-1.5 py-0.5 rounded-full whitespace-nowrap"
                title="This step's prompt includes everything produced so far — compounding context"
                style={{ background: 'rgba(94,200,229,.12)', color: 'var(--t1)' }}>
                ⮡ carries context
              </span>
            )}
            {st.tool_calls > 0 && (
              <span className="font-code text-[9.5px] px-1.5 py-0.5 rounded-full whitespace-nowrap"
                title={`${st.tool_calls} tool round-trip(s): agent waits on the tool, ~${st.tool_result_tokens} result tokens injected into context, model called again`}
                style={{ background: 'rgba(232,155,92,.12)', color: 'var(--t4)' }}>
                ⚒ {st.tool_calls} tool call{st.tool_calls === 1 ? '' : 's'} · +{st.tool_result_tokens} tok each
              </span>
            )}
          </div>
        ))}
        <div className="flex items-center gap-2.5 text-[11px] text-[var(--faint)] mt-0.5">
          <span className="flex-none w-[18px] text-center">↻</span>
          <span>
            think {(s.think_ms / 1000).toFixed(1)}s, then repeat
            {s.session_turns > 1
              ? <> — <b style={{ color: 'var(--muted)' }}>session compounds over {s.session_turns} turns</b> (context carries into the next loop, capped at {s.context_cap.toLocaleString()} tok) before resetting</>
              : ' — stateless: every loop starts fresh'}
            {' '}· {s.calls_per_loop} LLM calls{s.tool_calls_per_loop > 0 ? ` + ${s.tool_calls_per_loop} tool waits` : ''} per loop
          </span>
        </div>
        <div className="flex items-center gap-3 mt-1 font-code text-[10px] text-[var(--faint)]">
          <span className="flex items-center gap-1"><i className="w-2 h-[5px] rounded-sm inline-block" style={{ background: 'var(--t1)' }} /> prompt (prefill)</span>
          <span className="flex items-center gap-1"><i className="w-2 h-[5px] rounded-sm inline-block" style={{ background: 'var(--t4)' }} /> output (decode)</span>
          <span>· carried context and injected tool results grow the prompt beyond the base each turn</span>
        </div>
      </div>
    </div>
  )
}

/* ── local engine chip ───────────────────────────────────────────────────────── */

function LocalEngineChip({ engine, onStart }: { engine: CapacityEngine; onStart: () => void }) {
  const [showLog, setShowLog] = useState(false)
  const starting = engine.setup_state === 'starting'
  return (
    <div className="mt-2.5">
      <div className="flex items-center gap-2 text-[12px]">
        <span className={clsx('w-2 h-2 rounded-full', starting && 'anim-dot-pulse')}
          style={{ background: engine.serving ? 'var(--ok)' : starting ? 'var(--warn)' : 'var(--bad)' }} />
        <span className="text-[var(--muted)]">
          {engine.serving
            ? <>engine serving <b className="font-code">{engine.models[0] ?? engine.model}</b></>
            : starting ? 'engine starting — installing / loading model (can take minutes)'
            : engine.setup_state === 'failed' ? 'engine setup failed'
            : 'engine not running'}
        </span>
        {!engine.serving && !starting && (
          <button onClick={onStart}
            className="ml-1 px-2.5 py-1 rounded-md text-[11.5px] font-medium"
            style={{ background: 'var(--accent)', color: '#0b0f18' }}>
            {engine.setup_state === 'failed' ? 'Retry setup' : 'Start engine'}
          </button>
        )}
        {engine.setup_log.length > 0 && (
          <button onClick={() => setShowLog((v) => !v)}
            className="text-[11px] text-[var(--faint)] hover:text-[var(--muted)]">
            {showLog ? 'hide log' : 'show log'}
          </button>
        )}
      </div>
      {showLog && (
        <pre className="mt-2 p-2 rounded-md text-[10.5px] font-code max-h-40 overflow-y-auto whitespace-pre-wrap"
          style={{ background: 'var(--ink)', color: 'var(--muted)', border: '1px solid var(--line)' }}>
          {engine.setup_log.join('\n')}
        </pre>
      )}
    </div>
  )
}

/* ── live panel ──────────────────────────────────────────────────────────────── */

function LivePanel({ status }: { status: CapacityStatus }) {
  const latest = status.latest ?? {}
  const timeline = status.timeline ?? []
  return (
    <div className="console-panel p-4 mb-4">
      <div className="flex items-center gap-2 mb-3">
        <span className={clsx('w-2 h-2 rounded-full', status.active && 'anim-dot-pulse')}
          style={{ background: status.active ? 'var(--accent)' : 'var(--faint)' }} />
        <span className="eyebrow">
          {status.active ? `${status.phase} — ${status.users} virtual agents` : 'last run timeline'}
        </span>
        <span className="ml-auto font-code text-[11px] text-[var(--faint)]">
          {status.total_requests ?? 0} requests
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Dial label="agents" value={String(status.users ?? 0)} accent />
        <Dial label="tokens/s" value={fmtNum(latest.tps)} />
        <Dial label="req/min" value={fmtNum(latest.rpm)} />
        <Dial label="p95" value={fmtMs(latest.p95_ms)} />
        <Dial label="CPU" value={latest.cpu_pct != null ? `${latest.cpu_pct}%` : '—'} />
        <Dial label="memory" value={latest.mem_pct != null ? `${latest.mem_pct}%` : '—'} />
        <Dial label="mem b/w" value={latest.bw_gbs != null ? `${latest.bw_gbs} GB/s` : '—'} />
        <Dial label={latest.kv_pct != null ? 'KV cache' : 'power'}
          value={latest.kv_pct != null ? `${latest.kv_pct}%`
            : latest.power_w != null ? `${latest.power_w} W` : '—'} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Spark title="agents · CPU % · memory %" series={[
          { color: 'var(--accent)', pts: timeline.map((s) => s.users) },
          { color: 'var(--t5)', pts: timeline.map((s) => s.cpu_pct ?? null) },
          { color: 'var(--t1)', pts: timeline.map((s) => s.mem_pct ?? null) },
        ]} maxHint={100} />
        <Spark title="throughput tok/s" series={[
          { color: 'var(--t2)', pts: timeline.map((s) => s.tps) },
        ]} />
        <Spark title="p95 latency ms" series={[
          { color: 'var(--t3)', pts: timeline.map((s) => s.p95_ms ?? null) },
        ]} />
        <Spark title="memory bandwidth GB/s · KV cache %" series={[
          { color: 'var(--t4)', pts: timeline.map((s) => s.bw_gbs ?? null) },
          { color: 'var(--t1)', pts: timeline.map((s) => s.kv_pct ?? null) },
        ]} />
      </div>
    </div>
  )
}

function Dial({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-xl border px-3 py-2.5 text-center"
      style={{ background: 'var(--elev)', borderColor: accent ? 'rgba(124,135,245,.4)' : 'var(--line)' }}>
      <div className="font-display font-semibold text-[20px] tracking-[-0.02em]"
        style={{ color: accent ? 'var(--accent)' : 'var(--text)' }}>{value}</div>
      <div className="eyebrow mt-0.5">{label}</div>
    </div>
  )
}

function Spark({ title, series, maxHint }: {
  title: string
  series: { color: string; pts: (number | null)[] }[]
  maxHint?: number
}) {
  const W = 260, H = 72, P = 4
  const all = series.flatMap((s) => s.pts).filter((v): v is number => v != null)
  const max = Math.max(maxHint ?? 0, ...all, 1)
  const line = (pts: (number | null)[]) => {
    const n = pts.length
    if (n < 2) return ''
    return pts.map((v, i) => v == null ? null
      : `${(P + (i / (n - 1)) * (W - 2 * P)).toFixed(1)},${(H - P - (v / max) * (H - 2 * P)).toFixed(1)}`)
      .filter(Boolean).join(' ')
  }
  return (
    <div className="rounded-xl border p-2.5" style={{ background: 'var(--ink)', borderColor: 'var(--line)' }}>
      <div className="eyebrow mb-1">{title}</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[72px]">
        {series.map((s, i) => (
          <polyline key={i} points={line(s.pts)} fill="none" stroke={s.color}
            strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" opacity={0.9} />
        ))}
      </svg>
    </div>
  )
}

/* ── result card ─────────────────────────────────────────────────────────────── */

const VERDICT_TEXT: Record<string, string> = {
  cpu: 'CPU saturated — this is the box’s ceiling',
  memory: 'system memory saturated — RAM gated before the cores did',
  kv: 'engine KV cache saturated — model memory gated before CPU',
  plateau: 'throughput plateaued — no headroom past this point',
  errors: 'error rate exceeded the limit',
  capped: 'reached the configured user cap without saturating',
  timeout: 'time limit reached',
  stopped: 'stopped manually',
}

function ResultCard({ result }: { result: CapacityResult }) {
  const s = result.steady
  return (
    <div className="console-panel p-5" style={{ borderColor: 'rgba(124,135,245,.4)' }}>
      <div className="eyebrow mb-1">Capacity result — {result.mode.replace('_', ' ')}</div>
      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="font-display font-bold text-[40px] tracking-[-0.03em]" style={{ color: 'var(--accent)' }}>
          {result.max_users}
        </span>
        <span className="text-[15px] text-[var(--text)]">concurrent agents sustained</span>
        <span className="text-[12.5px] text-[var(--muted)]">
          {VERDICT_TEXT[result.verdict ?? ''] ?? result.verdict}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-2 mt-4 text-[12.5px]">
        <Kv k="throughput" v={`${fmtNum(s.tps)} tok/s`} />
        <Kv k="requests" v={`${fmtNum(s.rpm)}/min`} />
        <Kv k="latency p50 / p95" v={`${fmtMs(s.p50_ms)} / ${fmtMs(s.p95_ms)}`} />
        <Kv k="error rate" v={`${((s.err_rate ?? 0) * 100).toFixed(1)}%`} />
        <Kv k="CPU at steady state" v={s.cpu_pct != null ? `${s.cpu_pct}%` : '—'} />
        <Kv k="memory" v={s.mem_pct != null ? `${s.mem_pct}%` : '—'} />
        <Kv k="memory bandwidth" v={s.bw_gbs != null ? `${s.bw_gbs} GB/s` : '—'} />
        <Kv k="KV cache" v={s.kv_pct != null ? `${s.kv_pct}%` : '—'} />
        <Kv k="memory / added agent" v={result.mem_mb_per_user != null ? `${fmtNum(result.mem_mb_per_user)} MB` : '—'} />
        <Kv k="power" v={s.power_w != null ? `${s.power_w} W` : '—'} />
        <Kv k="energy used" v={result.energy_wh != null ? `${result.energy_wh} Wh` : '—'} />
        <Kv k="duration" v={`${Math.round(result.duration_s)}s`} />
        <Kv k="total requests" v={String(result.total_requests)} />
        <Kv k="total tokens out" v={result.total_tokens_out.toLocaleString()} />
      </div>

      <div className="mt-4 pt-3 border-t" style={{ borderColor: 'var(--line-soft)' }}>
        <div className="eyebrow mb-2">Per agent type</div>
        <div className="flex flex-col gap-1">
          {Object.entries(result.per_scenario).map(([sid, sc]) => (
            <div key={sid} className="flex items-center gap-3 text-[12px]">
              <span className="w-[38%] truncate text-[var(--text)]">{sc.name}</span>
              <span className="font-code text-[11px] text-[var(--muted)] w-14">{sc.users} usr</span>
              <span className="font-code text-[11px] text-[var(--muted)] w-20">{sc.calls} calls</span>
              <span className="font-code text-[11px] text-[var(--muted)] w-24">p50 {fmtMs(sc.p50_ms)}</span>
              <span className="font-code text-[11px] text-[var(--muted)] w-28"
                title="average KV-cache tokens this agent type keeps resident">
                {sc.avg_kv_tokens != null ? `${sc.avg_kv_tokens.toLocaleString()} kv tok` : ''}
              </span>
              <span className="font-code text-[11px] w-16"
                style={{ color: sc.errors ? 'var(--bad)' : 'var(--faint)' }}>
                {sc.errors ? `${sc.errors} err` : 'clean'}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-2 min-w-0">
      <span className="text-[var(--faint)] truncate">{k}</span>
      <span className="font-code text-[12px] text-[var(--text)] whitespace-nowrap">{v}</span>
    </div>
  )
}

function fmtNum(v?: number | null): string {
  if (v == null) return '—'
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v * 10) / 10)
}

function fmtMs(v?: number | null): string {
  if (v == null) return '—'
  return v >= 10_000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`
}
