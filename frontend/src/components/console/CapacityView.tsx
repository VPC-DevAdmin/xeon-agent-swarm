import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { agentDefsApi, capacityApi } from '../../api/client'
import { CapacityHistory } from './CapacityHistory'
import type {
  AgentDefinition,
  CapacityBenchmarkTarget, CapacityInferenceBackend,
  CapacityCloudModel, CapacityEngine, CapacityResult, CapacityScenario,
  CapacitySample, CapacityStatus,
} from '../../api/types'

const TARGETS: { id: CapacityBenchmarkTarget; label: string; hint: string }[] = [
  { id: 'agent_host', label: 'Agent host capacity', hint: 'Real agent workflows load this orchestration server; inference remains an external dependency.' },
  { id: 'integrated_node', label: 'Integrated agent node', hint: 'Real agent workflows and local inference share this system, so the result measures the combined node.' },
  { id: 'inference_engine', label: 'Inference diagnostic', hint: 'Synthetic agent-shaped requests isolate model-serving capacity; no real agents or orchestration are exercised.' },
]

const BACKENDS: { id: CapacityInferenceBackend; label: string; hint: string }[] = [
  { id: 'remote_mock', label: 'Remote mock', hint: 'external mock router; zero cloud calls' },
  { id: 'remote_real', label: 'Remote cloud', hint: 'live API/router; spends credits' },
  { id: 'local', label: 'Local inference', hint: 'the on-box SGLang engine' },
]

const COMPLEXITY_COLOR: Record<string, string> = {
  light: 'var(--t2)', medium: 'var(--t3)', heavy: 'var(--t5)',
}

/**
 * CapacityView separates the system boundary under test from the inference
 * backend. Runtime targets execute complete agent workflows; the inference
 * diagnostic sends synthetic agent-shaped traces directly to a model endpoint.
 */
export function CapacityView() {
  const [scenarios, setScenarios] = useState<CapacityScenario[]>([])
  const [enabled, setEnabled] = useState<string[]>([])
  const [tile, setTile] = useState<Record<string, number>>({})
  const [e2eWorkflows, setE2eWorkflows] = useState<{ id: string; name: string; query: string }[]>([])
  const [e2eTile, setE2eTile] = useState<Record<string, number>>({})
  const [defs, setDefs] = useState<AgentDefinition[]>([])
  const [defsInMix, setDefsInMix] = useState<string[]>([])
  const [viewing, setViewing] = useState<CapacityResult | null>(null)
  const [mix, setMix] = useState<'tile' | 'custom'>('tile')
  const [target, setTarget] = useState<CapacityBenchmarkTarget>('agent_host')
  const [backend, setBackend] = useState<CapacityInferenceBackend>('remote_mock')
  const [mockMs, setMockMs] = useState(2000)
  const [mockSigma, setMockSigma] = useState(300)
  const [engine, setEngine] = useState<CapacityEngine | null>(null)
  const [status, setStatus] = useState<CapacityStatus | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [armReal, setArmReal] = useState(false)   // cloud mode needs an explicit second click
  const [cacheMode, setCacheMode] = useState<'warm' | 'cold'>('warm')
  const [cloudModels, setCloudModels] = useState<CapacityCloudModel[]>([])
  const [cloudModelId, setCloudModelId] = useState('openai:gpt-5.4-mini')
  const [cloudApiKey, setCloudApiKey] = useState('')
  const [maxCostUsd, setMaxCostUsd] = useState(25)
  const [customBaseUrl, setCustomBaseUrl] = useState('')
  const [customModel, setCustomModel] = useState('')
  const [customInputCost, setCustomInputCost] = useState(0)
  const [customOutputCost, setCustomOutputCost] = useState(0)

  useEffect(() => {
    capacityApi.scenarios().then((r) => {
      setScenarios(r.scenarios)
      setEnabled(r.scenarios.map((s) => s.id))
      setTile(r.tile ?? {})
      setE2eWorkflows(r.e2e_workflows ?? [])
      setE2eTile(r.e2e_tile ?? {})
    }).catch(() => {})
    agentDefsApi.list().then(setDefs).catch(() => {})
    capacityApi.models().then((r) => setCloudModels(r.models)).catch(() => {})
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
  const runtimeTarget = target !== 'inference_engine'
  const availableBackends: CapacityInferenceBackend[] = target === 'agent_host'
    ? ['remote_mock', 'remote_real']
    : target === 'integrated_node' ? ['local'] : ['local', 'remote_mock', 'remote_real']
  const selectedCloudModel = cloudModels.find((m) => m.id === cloudModelId)
  const customCloud = cloudModelId === 'custom'
  const cloudReady = customCloud
    ? Boolean(customBaseUrl.trim() && customModel.trim() && maxCostUsd > 0)
    : Boolean(selectedCloudModel && (cloudApiKey.trim() || selectedCloudModel.api_key_configured) && maxCostUsd > 0)

  function chooseTarget(next: CapacityBenchmarkTarget) {
    setTarget(next)
    if (next === 'integrated_node') setBackend('local')
    else if (next === 'agent_host' && backend === 'local') setBackend('remote_mock')
    setArmReal(false)
  }

  async function start() {
    // Cloud mode spends real credits: require a deliberate second click that
    // shows the model and the hard request budget before anything is sent.
    if (backend === 'remote_real' && !armReal) { setArmReal(true); return }
    setArmReal(false)
    setBusy(true); setError(null)
    try {
      await capacityApi.start({
        benchmark_target: target,
        inference_backend: backend,
        mix,
        scenarios: mix === 'custom' ? enabled : undefined,
        agent_definitions: runtimeTarget && mix === 'custom' ? defsInMix : undefined,
        mock_ms: target === 'inference_engine' && backend === 'remote_mock' ? mockMs : undefined,
        mock_sigma: target === 'inference_engine' && backend === 'remote_mock' ? mockSigma : undefined,
        cache_mode: cacheMode,
        confirm_real: backend === 'remote_real' ? true : undefined,
        cloud_model: backend === 'remote_real' ? cloudModelId : undefined,
        cloud_api_key: backend === 'remote_real' && cloudApiKey ? cloudApiKey : undefined,
        custom_base_url: backend === 'remote_real' && customCloud ? customBaseUrl : undefined,
        custom_model: backend === 'remote_real' && customCloud ? customModel : undefined,
        input_cost_per_mtok: backend === 'remote_real' && customCloud ? customInputCost : undefined,
        output_cost_per_mtok: backend === 'remote_real' && customCloud ? customOutputCost : undefined,
        max_cost_usd: backend === 'remote_real' ? maxCostUsd : undefined,
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
      {/* benchmark boundary + inference backend */}
      <div className="flex flex-wrap items-stretch gap-3 mb-4">
        <div className="console-panel flex-1 min-w-[340px] p-3.5">
          <div className="eyebrow mb-2">What are we measuring?</div>
          <div className="flex flex-wrap gap-1 p-[3px] rounded-[10px] border w-fit"
            style={{ background: 'var(--ink)', borderColor: 'var(--line)' }}>
            {TARGETS.map((t) => (
              <button key={t.id} disabled={active}
                onClick={() => chooseTarget(t.id)}
                className={clsx('px-3 py-1.5 rounded-[7px] text-[12.5px] font-medium transition-colors disabled:opacity-60',
                  target === t.id ? 'bg-[var(--elev)] text-[var(--text)]' : 'text-[var(--muted)] hover:text-[var(--text)]')}>
                {t.label}
              </button>
            ))}
          </div>
          <p className="text-[11.5px] text-[var(--faint)] mt-2">{TARGETS.find((t) => t.id === target)?.hint}</p>

          <div className="eyebrow mt-3 mb-1.5">Inference backend</div>
          <div className="flex flex-wrap gap-1.5">
            {BACKENDS.filter((b) => availableBackends.includes(b.id)).map((b) => (
              <button key={b.id} disabled={active}
                onClick={() => { setBackend(b.id); setArmReal(false) }} title={b.hint}
                className={clsx('px-2.5 py-1 rounded-full border text-[11.5px] transition-colors',
                  backend === b.id ? 'text-[var(--text)]' : 'text-[var(--faint)]')}
                style={{ borderColor: backend === b.id ? 'rgba(124,135,245,.5)' : 'var(--line)',
                         background: backend === b.id ? 'var(--elev)' : 'transparent' }}>
                {b.label}
              </button>
            ))}
          </div>

          {target === 'inference_engine' && backend !== 'remote_mock' && (
            <div className="flex items-center gap-1.5 mt-2">
              <span className="font-code text-[10px] text-[var(--faint)] uppercase">cache</span>
              {(['warm', 'cold'] as const).map((cm) => (
                <button key={cm} disabled={active} onClick={() => setCacheMode(cm)}
                  title={cm === 'warm'
                    ? 'Warm: the short shared system preamble may prefix-cache (the realistic production case); prompt bodies are always unique'
                    : 'Cold: every call is salted so NOTHING prefix-caches — the worst-case prefill number'}
                  className={clsx('px-2 py-0.5 rounded-full border font-code text-[10.5px] transition-colors',
                    cacheMode === cm ? 'text-[var(--text)]' : 'text-[var(--faint)]')}
                  style={{ borderColor: cacheMode === cm ? 'rgba(124,135,245,.45)' : 'var(--line)',
                           background: cacheMode === cm ? 'var(--elev)' : 'transparent' }}>
                  {cm}
                </button>
              ))}
            </div>
          )}

          {target === 'inference_engine' && backend === 'remote_mock' && (
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

          {backend === 'local' && engine && (
            <LocalEngineChip engine={engine} onStart={() => capacityApi.startEngine().then(pollEngine)} />
          )}
          {backend === 'remote_real' && (
            <CloudModelSetup models={cloudModels} modelId={cloudModelId}
              onModelId={(id) => { setCloudModelId(id); setArmReal(false) }}
              apiKey={cloudApiKey} onApiKey={setCloudApiKey}
              maxCost={maxCostUsd} onMaxCost={setMaxCostUsd}
              customBaseUrl={customBaseUrl} onCustomBaseUrl={setCustomBaseUrl}
              customModel={customModel} onCustomModel={setCustomModel}
              customInputCost={customInputCost} onCustomInputCost={setCustomInputCost}
              customOutputCost={customOutputCost} onCustomOutputCost={setCustomOutputCost}
              disabled={active} />
          )}
        </div>

        {/* start / stop */}
        <div className="console-panel w-[210px] p-3.5 flex flex-col items-center justify-center gap-2">
          {!active ? (
            <button onClick={start}
              disabled={busy || (!runtimeTarget && enabled.length === 0) || (backend === 'local' && !engine?.serving)
                || (backend === 'remote_real' && !cloudReady)}
              className="w-full py-3 rounded-xl font-display font-semibold text-[15px] transition-opacity disabled:opacity-35"
              style={{ background: armReal ? 'var(--bad)' : 'var(--accent)', color: armReal ? '#fff' : '#0b0f18' }}>
              {armReal ? 'Confirm cloud spend' : 'Start test'}
            </button>
          ) : (
            <button onClick={stop} disabled={busy}
              className="w-full py-3 rounded-xl font-display font-semibold text-[15px] border"
              style={{ borderColor: 'rgba(229,106,130,.5)', color: 'var(--bad)' }}>
              Stop
            </button>
          )}
          <p className="font-code text-[10.5px] text-center"
            style={{ color: armReal ? 'var(--bad)' : 'var(--faint)' }}>
            {active ? `${status?.phase} · ${Math.round(status?.elapsed_s ?? 0)}s`
              : armReal ? `confirm ${selectedCloudModel?.name ?? (customModel || 'custom model')} · circuit breaker $${maxCostUsd.toFixed(2)}`
              : 'failure-driven ramp · no session or duration cap · cloud spend is dollar-guarded'}
          </p>
        </div>
      </div>

      {error && <p className="text-[12.5px] mb-3" style={{ color: 'var(--bad)' }}>{error}</p>}

      {/* scenario blocks */}
      <div className="console-panel p-3.5 mb-4">
        <div className="flex items-center gap-3 mb-2 flex-wrap">
          <span className="eyebrow">Workload mix — each virtual session repeats one {runtimeTarget ? 'real agent workflow' : 'synthetic agent trace'}</span>
          <div className="ml-auto flex gap-0.5 p-[2px] rounded-lg border"
            style={{ background: 'var(--ink)', borderColor: 'var(--line)' }}>
            <button disabled={active} onClick={() => setMix('tile')}
              title="One Agent Capacity Unit (fixed weighted bundle) per rung — the same mix at every load level, so rungs and systems are comparable"
              className={clsx('px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors',
                mix === 'tile' ? 'bg-[var(--elev)] text-[var(--text)]' : 'text-[var(--muted)]')}>
              Reference tile · comparable
            </button>
            <button disabled={active} onClick={() => setMix('custom')}
              title="Pick your own profile mix for customer-specific planning — results are NOT comparable across runs with different mixes"
              className={clsx('px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors',
                mix === 'custom' ? 'bg-[var(--elev)] text-[var(--text)]' : 'text-[var(--muted)]')}>
              Custom · non-comparable
            </button>
          </div>
        </div>
        {mix === 'tile' && (
          <p className="font-code text-[10.5px] mb-2" style={{ color: 'var(--faint)' }}>
            1 tile (ACU) = {runtimeTarget
              ? Object.entries(e2eTile).map(([sid, n]) => `${n}× ${e2eWorkflows.find((w) => w.id === sid)?.name ?? sid}`).join(' + ')
              : Object.entries(tile).map(([sid, n]) => `${n}× ${scenarios.find((s) => s.id === sid)?.name ?? sid}`).join(' + ')} — ramps add whole tiles
          </p>
        )}
        {runtimeTarget && mix === 'custom' && defs.length > 0 && (
          <div className="mb-2">
            <div className="eyebrow mb-1.5">Your agent definitions — assign to this planning mix</div>
            <div className="flex gap-1.5 flex-wrap">
              {defs.map((d) => {
                const on = defsInMix.includes(d.id)
                return (
                  <button key={d.id} disabled={active}
                    onClick={() => setDefsInMix((p) => on ? p.filter((x) => x !== d.id) : [...p, d.id])}
                    className={clsx('px-2.5 py-1 rounded-full border text-[11.5px] transition-colors',
                      on ? 'text-[var(--text)]' : 'text-[var(--faint)]')}
                    style={{ borderColor: on ? 'rgba(124,135,245,.5)' : 'var(--line)',
                             background: on ? 'var(--elev)' : 'transparent' }}>
                    {d.icon} {d.name} v{d.version}{on ? ' ✓' : ''}
                  </button>
                )
              })}
            </div>
          </div>
        )}
        {runtimeTarget && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            {e2eWorkflows.map((w) => {
              const live = status?.per_scenario?.[w.id]
              return (
                <div key={w.id} className="rounded-lg border p-2.5 bg-[var(--elev)]"
                  style={{ borderColor: 'rgba(124,135,245,.35)' }}>
                  <div className="flex items-center gap-2">
                    <span className="flex-none font-code text-[10px] px-1.5 py-0.5 rounded-full"
                      style={{ background: 'rgba(124,135,245,.15)', color: 'var(--accent)' }}>
                      ×{e2eTile[w.id] ?? 1}
                    </span>
                    <span className="text-[13px] font-medium flex-1 truncate">{w.name}</span>
                    <span className="font-code text-[10px] text-[var(--faint)]">real run</span>
                  </div>
                  <p className="text-[11px] text-[var(--faint)] mt-1 line-clamp-2">{w.query}</p>
                  {live && (
                    <p className="font-code text-[10.5px] mt-1.5" style={{ color: 'var(--muted)' }}>
                      {live.calls} workflow{live.calls === 1 ? '' : 's'}
                      {live.p50_ms != null && <> · p50 {fmtMs(live.p50_ms)}</>}
                      {live.trace && <> · {live.trace.llm_calls} LLM calls/run</>}
                      {live.errors > 0 && <span style={{ color: 'var(--bad)' }}> · {live.errors} failed</span>}
                    </p>
                  )}
                  {live?.last_error && (
                    <p className="font-code text-[10px] mt-1 break-all line-clamp-2"
                      style={{ color: 'var(--bad)' }} title={live.last_error}>
                      {live.last_error}
                    </p>
                  )}
                </div>
              )
            })}
          </div>
        )}
        <div className={runtimeTarget ? 'hidden' : 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2'}>
          {scenarios.map((s) => {
            const on = mix === 'tile' || enabled.includes(s.id)
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
                  {mix === 'tile' ? (
                    <span className="flex-none font-code text-[10px] px-1.5 py-0.5 rounded-full"
                      title="Sessions of this profile per tile"
                      style={{ background: 'rgba(124,135,245,.15)', color: 'var(--accent)' }}>
                      ×{tile[s.id] ?? 0}
                    </span>
                  ) : (
                  <button disabled={active} title={on ? 'Remove from the mix' : 'Add to the mix'}
                    onClick={() => setEnabled((prev) => on ? prev.filter((x) => x !== s.id) : [...prev, s.id])}
                    className="flex-none w-4 h-4 grid place-items-center rounded border disabled:cursor-default"
                    style={{ borderColor: on ? 'var(--accent)' : 'var(--line)',
                             background: on ? 'var(--accent)' : 'transparent' }}>
                    {on && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#0b0f18" strokeWidth="3.5"><path d="M20 6 9 17l-5-5" /></svg>}
                  </button>
                  )}
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
                {active && live?.last_error && (
                  <p className="font-code text-[10px] mt-1 break-all line-clamp-2"
                    style={{ color: 'var(--bad)' }} title={live.last_error}>
                    {live.last_error}
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

      {/* final result (live) or a history result being viewed */}
      {viewing ? (
        <div>
          <button onClick={() => setViewing(null)}
            className="mb-2 text-[11.5px] font-code text-[var(--muted)] hover:text-[var(--text)]">
            ← back to latest
          </button>
          <ResultCard result={viewing} />
        </div>
      ) : (!active && result && <ResultCard result={result} />)}

      {/* DB-persisted benchmark history */}
      <CapacityHistory activePhase={status?.phase ?? 'idle'} onView={setViewing} />
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

/* ── cloud model + spend circuit breaker ───────────────────────────────────── */

function CloudModelSetup({ models, modelId, onModelId, apiKey, onApiKey,
  maxCost, onMaxCost, customBaseUrl, onCustomBaseUrl, customModel, onCustomModel,
  customInputCost, onCustomInputCost, customOutputCost, onCustomOutputCost, disabled,
}: {
  models: CapacityCloudModel[]; modelId: string; onModelId: (v: string) => void
  apiKey: string; onApiKey: (v: string) => void
  maxCost: number; onMaxCost: (v: number) => void
  customBaseUrl: string; onCustomBaseUrl: (v: string) => void
  customModel: string; onCustomModel: (v: string) => void
  customInputCost: number; onCustomInputCost: (v: number) => void
  customOutputCost: number; onCustomOutputCost: (v: number) => void
  disabled: boolean
}) {
  const selected = models.find((m) => m.id === modelId)
  const custom = modelId === 'custom'
  const providers = ['openai', 'anthropic', 'google'] as const
  const inputClass = 'bg-[var(--ink)] border rounded px-2 py-1.5 font-code text-[11px] disabled:opacity-60'
  return (
    <div className="mt-3 p-3 rounded-xl border" style={{ borderColor: 'var(--line)', background: 'var(--ink)' }}>
      <div className="eyebrow mb-1.5">Cloud model and token price</div>
      <select value={modelId} disabled={disabled} onChange={(e) => onModelId(e.target.value)}
        className={`${inputClass} w-full`} style={{ borderColor: 'var(--line)' }}>
        {providers.map((provider) => (
          <optgroup key={provider} label={provider[0].toUpperCase() + provider.slice(1)}>
            {models.filter((m) => m.provider === provider).map((m) => (
              <option key={m.id} value={m.id}>
                {m.name} — ${m.input_per_mtok}/$${m.output_per_mtok} per 1M in/out
              </option>
            ))}
          </optgroup>
        ))}
        <option value="custom">Set up your own endpoint…</option>
      </select>

      {custom ? (
        <div className="grid grid-cols-2 gap-2 mt-2">
          <label className="col-span-2 text-[10.5px] text-[var(--muted)]">Endpoint address
            <input value={customBaseUrl} disabled={disabled} placeholder="https://host.example/v1"
              onChange={(e) => onCustomBaseUrl(e.target.value)} className={`${inputClass} w-full mt-0.5`}
              style={{ borderColor: 'var(--line)' }} />
          </label>
          <label className="col-span-2 text-[10.5px] text-[var(--muted)]">Model ID
            <input value={customModel} disabled={disabled} placeholder="model-name"
              onChange={(e) => onCustomModel(e.target.value)} className={`${inputClass} w-full mt-0.5`}
              style={{ borderColor: 'var(--line)' }} />
          </label>
          <PriceInput label="input $ / 1M" value={customInputCost} onChange={onCustomInputCost} disabled={disabled} />
          <PriceInput label="output $ / 1M" value={customOutputCost} onChange={onCustomOutputCost} disabled={disabled} />
          <p className="col-span-2 text-[10px] text-[var(--faint)]">Custom endpoints must support OpenAI Chat Completions. Prices are supplied by you.</p>
        </div>
      ) : selected && (
        <p className="font-code text-[10.5px] mt-1.5 text-[var(--faint)]">
          {selected.model} · ${selected.input_per_mtok.toFixed(2)} input / ${selected.output_per_mtok.toFixed(2)} output per 1M tokens
          {selected.pricing_note ? ` · ${selected.pricing_note}` : ''}
        </p>
      )}

      <div className="grid grid-cols-2 gap-2 mt-2">
        <label className="text-[10.5px] text-[var(--muted)]">API key
          <input type="password" value={apiKey} disabled={disabled}
            placeholder={selected?.api_key_configured ? 'server key configured' : custom ? 'optional' : 'required'}
            onChange={(e) => onApiKey(e.target.value)} autoComplete="off"
            className={`${inputClass} w-full mt-0.5`} style={{ borderColor: 'var(--line)' }} />
        </label>
        <label className="text-[10.5px] text-[var(--muted)]">Dollar circuit breaker
          <div className="relative mt-0.5"><span className="absolute left-2 top-1.5 font-code text-[11px] text-[var(--faint)]">$</span>
            <input type="number" value={maxCost} min={0.01} max={100000} step={1} disabled={disabled}
              onChange={(e) => onMaxCost(Number(e.target.value))}
              className={`${inputClass} w-full pl-5`} style={{ borderColor: 'var(--line)' }} />
          </div>
        </label>
      </div>
      <p className="text-[10px] mt-1.5 text-[var(--faint)]">The key is used for this run only and is never stored. The workload has no session or time ceiling; projected and cumulative cost are measured at every rung.</p>
    </div>
  )
}

function PriceInput({ label, value, onChange, disabled }: {
  label: string; value: number; onChange: (v: number) => void; disabled: boolean
}) {
  return <label className="text-[10.5px] text-[var(--muted)]">{label}
    <input type="number" value={value} min={0} step={0.01} disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
      className="w-full mt-0.5 bg-[var(--ink)] border rounded px-2 py-1.5 font-code text-[11px]"
      style={{ borderColor: 'var(--line)' }} />
  </label>
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
  const target = status.benchmark_target ?? (status.mode === 'e2e' ? 'agent_host' : 'inference_engine')
  const backend = status.inference_backend ?? (status.mode === 'e2e' ? 'remote_mock' : status.mode)
  const resourceMetricsMeaningful = target !== 'inference_engine' || backend === 'local'
  return (
    <div className="console-panel p-4 mb-4">
      <div className="flex items-center gap-2 mb-3">
        <span className={clsx('w-2 h-2 rounded-full', status.active && 'anim-dot-pulse')}
          style={{ background: status.active ? 'var(--accent)' : 'var(--faint)' }} />
        <span className="eyebrow">
          {status.active ? `${status.phase} — ${status.users} virtual sessions` : 'last run timeline'}
        </span>
        <span className="ml-auto font-code text-[11px] text-[var(--faint)]">
          {status.total_requests ?? 0} requests
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <Dial label={status.mix === 'tile' && status.tile_size ? 'tiles · sessions' : 'sessions'}
          value={status.mix === 'tile' && status.tile_size
            ? `${Math.floor((status.users ?? 0) / status.tile_size)} · ${status.users ?? 0}`
            : String(status.users ?? 0)} accent />
        <Dial label="tokens/s" value={fmtNum(latest.tps)} />
        <Dial label="req/min" value={fmtNum(latest.rpm)} />
        <Dial label="p95" value={fmtMs(latest.p95_ms)} />
        {status.max_cost_usd != null && <Dial label="cloud spend / guard"
          value={`$${(status.committed_cost_usd ?? status.cost_usd ?? 0).toFixed(3)} / $${status.max_cost_usd.toFixed(2)}`} />}
        {status.max_cost_usd != null && <Dial label="projected cost / hour"
          value={`$${(latest.cost_per_hour ?? 0).toFixed(2)}`} />}
        {target !== 'inference_engine' && <Dial label="workflows in flight" value={fmtNum(latest.in_flight)} />}
        {resourceMetricsMeaningful && <Dial label="CPU" value={latest.cpu_pct != null ? `${latest.cpu_pct}%` : '—'} />}
        {resourceMetricsMeaningful && <Dial label="memory" value={latest.mem_pct != null ? `${latest.mem_pct}%` : '—'} />}
        {backend === 'local' && <Dial label="mem b/w" value={latest.bw_gbs != null ? `${latest.bw_gbs} GB/s` : '—'} />}
        {resourceMetricsMeaningful && <Dial label={latest.kv_pct != null ? 'KV cache' : 'power'}
          value={latest.kv_pct != null ? `${latest.kv_pct}%`
            : latest.power_w != null ? `${latest.power_w} W` : '—'} />}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Spark title={resourceMetricsMeaningful ? 'agents · CPU % · memory %' : 'synthetic sessions'} series={[
          { color: 'var(--accent)', pts: timeline.map((s) => s.users) },
          ...(resourceMetricsMeaningful ? [
            { color: 'var(--t5)', pts: timeline.map((s) => s.cpu_pct ?? null) },
            { color: 'var(--t1)', pts: timeline.map((s) => s.mem_pct ?? null) },
          ] : []),
        ]} maxHint={100} />
        <Spark title="throughput tok/s" series={[
          { color: 'var(--t2)', pts: timeline.map((s) => s.tps) },
        ]} />
        <Spark title="p95 latency ms" series={[
          { color: 'var(--t3)', pts: timeline.map((s) => s.p95_ms ?? null) },
        ]} />
        {backend === 'local' && <Spark title="memory bandwidth GB/s · KV cache %" series={[
          { color: 'var(--t4)', pts: timeline.map((s) => s.bw_gbs ?? null) },
          { color: 'var(--t1)', pts: timeline.map((s) => s.kv_pct ?? null) },
        ]} />}
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
  slo: 'latency SLO breached beyond this level — capacity measured at the last level that held it',
  cpu: 'CPU saturated — this is the box’s ceiling',
  memory: 'system memory saturated — RAM gated before the cores did',
  kv: 'engine KV cache saturated — model memory gated before CPU',
  plateau: 'throughput plateaued — no headroom past this point',
  errors: 'error rate exceeded the limit',
  capped: 'safety ceiling reached without saturation — this is a lower bound, not the system limit',
  timeout: 'time safety limit reached without saturation — this is a lower bound when the SLO was certified',
  budget: 'live-API spend guard reached — this is a lower bound, not a capacity boundary',
  spend_guard: 'dollar circuit breaker reached — the run stopped before a system limit, so this is not a capacity result',
  stopped: 'stopped manually',
}

function ResultCard({ result }: { result: CapacityResult }) {
  const s = result.steady
  const target = result.benchmark_target ?? (result.mode === 'e2e' ? 'agent_host' : 'inference_engine')
  const backend = result.inference_backend ?? (result.mode === 'e2e' ? 'remote_mock' : result.mode)
  const targetLabel = target === 'agent_host' ? 'Agent host capacity'
    : target === 'integrated_node' ? 'Integrated agent node capacity' : 'Inference engine diagnostic'
  const backendLabel = backend === 'remote_mock' ? 'remote mock'
    : backend === 'remote_real' ? 'remote cloud' : 'local inference'
  const isRuntime = target !== 'inference_engine'
  const capacityCertified = result.capacity_certified ?? true
  const safetyStop = ['capped', 'timeout', 'budget', 'spend_guard'].includes(result.verdict ?? '')
  const lowerBound = Boolean(capacityCertified && safetyStop)
  const capacityLabel = capacityCertified
    ? (result.mix === 'tile' && result.capacity_tiles != null
        ? `tile${result.capacity_tiles === 1 ? '' : 's'} (${result.capacity_users} ${isRuntime ? 'agent workflow' : 'synthetic'} sessions) within SLO`
        : 'concurrent agent sessions within SLO')
    : `capacity unknown — peaked at ${result.peak_users ?? result.max_users} sessions; no rung was SLO-certified`
  const verdictText = !capacityCertified && safetyStop
    ? 'safety stop occurred before a healthy rung could be certified — result is inconclusive'
    : (VERDICT_TEXT[result.verdict ?? ''] ?? result.verdict)
  const resourceMetricsMeaningful = target !== 'inference_engine' || backend === 'local'
  return (
    <div className="console-panel p-5" style={{ borderColor: 'rgba(124,135,245,.4)' }}>
      <div className="eyebrow mb-1">{targetLabel} · inference: {backendLabel}{result.cloud_model ? ` · ${result.cloud_model.name}` : ''} · {isRuntime ? 'real agent workflows' : 'synthetic agent traces'}</div>
      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="font-display font-bold text-[40px] tracking-[-0.03em]" style={{ color: 'var(--accent)' }}>
          {lowerBound && '≥'}
          {!capacityCertified ? '—'
            : result.mix === 'tile' && result.capacity_tiles != null
              ? result.capacity_tiles : result.capacity_users}
        </span>
        <span className="text-[15px] text-[var(--text)]">
          {capacityLabel}
        </span>
        <span className="text-[12.5px] text-[var(--muted)]">
          {verdictText}
        </span>
      </div>
      {result.breach && (
        <p className="text-[12.5px] mt-1" style={{ color: 'var(--warn)' }}>
          The next rung failed the <b>{result.breach.profile}</b> {result.breach.metric === 'p95_ms' ? 'p95 latency' : 'error-rate'} SLO
          {result.breach.metric === 'p95_ms'
            ? ` (${Math.round(result.breach.value)}ms > ${Math.round(result.breach.limit)}ms limit${result.breach.baseline_ms != null ? `, baseline ${Math.round(result.breach.baseline_ms)}ms` : ''})`
            : ` (${(result.breach.value * 100).toFixed(1)}% > ${(result.breach.limit * 100).toFixed(0)}%)`}
        </p>
      )}
      {result.comparable === false && (
        <p className="font-code text-[10.5px] mt-1" style={{ color: 'var(--faint)' }}>
          custom mix — not comparable across runs or systems
        </p>
      )}
      <p className="font-code text-[11px] mt-1" style={{ color: 'var(--faint)' }}>
        SLO: p95 ≤ {result.slo?.p95_ms != null ? `${result.slo.p95_ms}ms`
          : `${result.slo?.p95_x ?? 3}× each profile’s healthy baseline`}
        {' '}· errors ≤ {((result.slo?.err ?? 0.05) * 100).toFixed(0)}%
        {(result.peak_users ?? result.max_users) > (result.capacity_users ?? result.peak_users ?? result.max_users) &&
          ` · ramped to ${result.peak_users ?? result.max_users}, scaled back to measure at ${result.capacity_users}`}
      </p>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-2 mt-4 text-[12.5px]">
        <Kv k="throughput" v={`${fmtNum(s.tps)} tok/s`} />
        <Kv k="requests" v={`${fmtNum(s.rpm)}/min`} />
        <Kv k="latency p50 / p95" v={`${fmtMs(s.p50_ms)} / ${fmtMs(s.p95_ms)}`} />
        <Kv k="error rate" v={`${((s.err_rate ?? 0) * 100).toFixed(1)}%`} />
        {resourceMetricsMeaningful && <Kv k="CPU at steady state" v={s.cpu_pct != null ? `${s.cpu_pct}%` : '—'} />}
        {resourceMetricsMeaningful && <Kv k="memory" v={s.mem_pct != null ? `${s.mem_pct}%` : '—'} />}
        {backend === 'local' && <Kv k="memory bandwidth" v={s.bw_gbs != null ? `${s.bw_gbs} GB/s` : '—'} />}
        {backend === 'local' && <Kv k="KV cache" v={s.kv_pct != null ? `${s.kv_pct}%` : '—'} />}
        {resourceMetricsMeaningful && <Kv k="memory / added agent" v={result.mem_mb_per_user != null ? `${fmtNum(result.mem_mb_per_user)} MB` : '—'} />}
        {resourceMetricsMeaningful && <Kv k="power" v={s.power_w != null ? `${s.power_w} W` : '—'} />}
        {resourceMetricsMeaningful && <Kv k="energy used" v={result.energy_wh != null ? `${result.energy_wh} Wh` : '—'} />}
        <Kv k="duration" v={`${Math.round(result.duration_s)}s`} />
        <Kv k="requests started" v={String(result.total_requests)} />
        {result.completed_requests != null && <Kv k="requests completed" v={String(result.completed_requests)} />}
        {(result.unfinished_requests ?? 0) > 0 && <Kv k="unfinished at stop" v={String(result.unfinished_requests)} />}
        {isRuntime && <Kv k="peak workflows in flight" v={String(result.max_in_flight ?? 0)} />}
        <Kv k="total tokens out" v={result.total_tokens_out.toLocaleString()} />
        {result.total_tokens_in != null && <Kv k="total tokens in" v={result.total_tokens_in.toLocaleString()} />}
        {result.workflows_per_hour != null && (
          <Kv k="workflows / hour" v={String(result.workflows_per_hour)} />
        )}
        {result.cost && <Kv k="cloud cost this run" v={`$${result.cost.run_total_usd.toFixed(4)} observed${(result.cost.in_flight_reserved_usd ?? 0) > 0 ? ` + $${result.cost.in_flight_reserved_usd?.toFixed(4)} in-flight estimate` : ''} / $${result.cost.circuit_breaker_usd.toFixed(2)} guard`} />}
        {result.cost && <Kv k="steady-state cost / hour" v={`$${result.cost.steady_cost_per_hour.toFixed(2)}`} />}
        {result.cost?.steady_cost_per_workflow != null && <Kv k="cost / workflow" v={`$${result.cost.steady_cost_per_workflow.toFixed(4)}`} />}
        {!isRuntime && result.cost?.steady_cost_per_1k_requests != null && <Kv k="cost / 1K requests" v={`$${result.cost.steady_cost_per_1k_requests.toFixed(2)}`} />}
      </div>

      {result.capacity_levels && result.capacity_levels.length > 0 && result.cost && (
        <div className="mt-4 pt-3 border-t overflow-x-auto" style={{ borderColor: 'var(--line-soft)' }}>
          <div className="eyebrow mb-2">Cost by capacity level</div>
          <table className="w-full text-[11px] font-code">
            <thead className="text-[var(--faint)]"><tr className="text-left">
              <th className="pb-1 pr-3">level</th><th className="pb-1 pr-3">state</th>
              <th className="pb-1 pr-3">p95</th><th className="pb-1 pr-3">rate</th>
              <th className="pb-1 pr-3">$/hour</th><th className="pb-1 pr-3">rung cost</th>
              <th className="pb-1">run total</th>
            </tr></thead>
            <tbody>
              {result.capacity_levels.map((level, i) => (
                <tr key={`${level.phase}-${level.users}-${i}`}
                  className={level.phase === 'steady' ? 'text-[var(--text)]' : 'text-[var(--muted)]'}
                  style={{ borderTop: '1px solid var(--line-soft)' }}>
                  <td className="py-1.5 pr-3">{level.phase === 'steady' ? 'steady · ' : ''}{level.tiles != null ? `${level.tiles} tile / ` : ''}{level.users} usr</td>
                  <td className="py-1.5 pr-3">{level.slo_state}</td>
                  <td className="py-1.5 pr-3">{fmtMs(level.p95_ms)}</td>
                  <td className="py-1.5 pr-3">{fmtNum(level.rpm)}/min</td>
                  <td className="py-1.5 pr-3">${level.projected_cost_per_hour.toFixed(2)}</td>
                  <td className="py-1.5 pr-3">${level.incremental_cost_usd.toFixed(4)}</td>
                  <td className="py-1.5">${level.cumulative_cost_usd.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[10px] mt-1.5 text-[var(--faint)]">Token-list-price estimate only; provider caching, batch discounts, tools, storage, and taxes are not included.</p>
        </div>
      )}

      <div className="mt-4 pt-3 border-t" style={{ borderColor: 'var(--line-soft)' }}>
        <div className="eyebrow mb-2">Per {isRuntime ? 'workflow' : 'agent trace type'}</div>
        <div className="flex flex-col gap-1">
          {Object.entries(result.per_scenario).map(([sid, sc]) => (
            <div key={sid} className="flex flex-col gap-0.5">
            <div className="flex items-center gap-3 text-[12px]">
              <span className="w-[38%] truncate text-[var(--text)]">{sc.name}</span>
              <span className="font-code text-[11px] text-[var(--muted)] w-14">{sc.users} usr</span>
              <span className="font-code text-[11px] text-[var(--muted)] w-20">{sc.calls} calls</span>
              <span className="font-code text-[11px] text-[var(--muted)] w-24">p50 {fmtMs(sc.p50_ms)}</span>
              <span className="font-code text-[11px] text-[var(--muted)] w-32"
                title="ESTIMATED average tokens concurrently in flight (token-seconds per second over request lifetimes) — the engine's KV gauge is the measured value">
                {sc.avg_tokens_in_flight != null ? `~${sc.avg_tokens_in_flight.toLocaleString()} tok in flight` : ''}
              </span>
              <span className="font-code text-[11px] w-16"
                style={{ color: sc.errors ? 'var(--bad)' : 'var(--faint)' }}>
                {sc.errors ? `${sc.errors} err` : 'clean'}
              </span>
              {sc.trace && (
                <span className="font-code text-[10.5px] text-[var(--faint)] whitespace-nowrap"
                  title="measured per-workflow trace: LLM calls / worker steps / validations — compare against the synthetic profiles">
                  {sc.trace.llm_calls} LLM · {sc.trace.steps} steps · {sc.trace.validations} val
                </span>
              )}
            </div>
            {sc.last_error && (
              <p className="font-code text-[10px] break-all line-clamp-2 pl-1"
                style={{ color: 'var(--bad)' }} title={sc.last_error}>
                ↳ {sc.last_error}
              </p>
            )}
            </div>
          ))}
        </div>
      </div>

      {result.repro && (
        <p className="font-code text-[10px] mt-3 pt-2 border-t leading-relaxed"
          style={{ color: 'var(--faint)', borderColor: 'var(--line-soft)' }}>
          repro: seed {result.repro.seed}{result.repro.cache_mode ? ` · ${result.repro.cache_mode} cache` : ''} · scenarios v{result.repro.benchmark_version}
          {result.repro.scenario_fingerprint && ` (${result.repro.scenario_fingerprint})`}
          {result.repro.git_commit && ` · commit ${result.repro.git_commit}`}
          {result.repro.model && ` · ${result.repro.model}`}
          {result.repro.host?.cpu_count && ` · ${result.repro.host.cpu_count} cores`}
          {result.repro.host?.mem_total_gb != null && ` / ${result.repro.host.mem_total_gb} GB`}
          {result.repro.host?.numa_nodes != null && ` / ${result.repro.host.numa_nodes} NUMA`}
        </p>
      )}
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
