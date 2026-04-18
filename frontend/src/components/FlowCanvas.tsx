/**
 * FlowCanvas — the animated left-to-right pipeline visualization.
 *
 * Five stages rendered as nodes with Framer Motion:
 *   Query → Orchestrator → Worker grid → Fact-checker → Synthesizer
 *
 * Narrative cards fade in as each stage activates.
 * Pacing gates ensure each stage stays visible long enough for a viewer to read.
 */
import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useSwarmStore, PACING } from '../store/swarmStore'
import { WorkerGrid } from './WorkerGrid'

// ── Stage types ───────────────────────────────────────────────────────────────

type Stage = 'orchestrating' | 'working' | 'fact_checking' | 'synthesizing' | 'done'

interface NarrativeCard {
  stage: Stage
  title: string
  body: string
}

const NARRATIVES: NarrativeCard[] = [
  {
    stage: 'orchestrating',
    title: 'Structured handoff',
    body: 'The orchestrator produces typed tasks with explicit dependencies — not instructions in prose. Downstream workers receive a contract, not a conversation. This is what makes the rest of the system predictable.',
  },
  {
    stage: 'working',
    title: 'Parallel specialists',
    body: 'Each worker receives a focused 800-token prompt and runs simultaneously across CPU cores. No worker ever sees the full query context. This is how the system avoids context rot and scales on a single Xeon.',
  },
  {
    stage: 'fact_checking',
    title: 'Claim-level grounding',
    body: 'The fact-checker verifies individual claims against retrieved sources, not the full document. Unsupported claims are rejected before they reach the synthesizer. This is where hallucinations get caught.',
  },
  {
    stage: 'synthesizing',
    title: 'Structured composition',
    body: 'The synthesizer never sees raw sources — only verified, typed artifacts. It composes them into a final document with consistent citations. This is why the output is grounded and renderable, not freeform prose.',
  },
]

// ── Motion variants ───────────────────────────────────────────────────────────

const nodeVariants = {
  hidden:  { opacity: 0, scale: 0.82 },
  visible: { opacity: 1, scale: 1, transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] } },
}

const narrativeVariants = {
  hidden:  { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.5, delay: 0.3 } },
  exit:    { opacity: 0, y: -8, transition: { duration: 0.2 } },
}

const edgeVariants = {
  hidden:  { scaleX: 0, originX: 0 },
  visible: { scaleX: 1, transition: { duration: 0.5, ease: 'easeOut' } },
}

// Pulse keyframes used directly in StageNode's animate object (not a spread,
// to avoid TypeScript's "animate specified more than once" error with variants)
const PULSE_BOX_SHADOW = [
  '0 0 0 0 rgba(59,130,246,0)',
  '0 0 0 6px rgba(59,130,246,0.3)',
  '0 0 0 0 rgba(59,130,246,0)',
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function stageIndex(s: Stage | 'idle'): number {
  const order = ['orchestrating', 'working', 'fact_checking', 'synthesizing', 'done']
  return order.indexOf(s as string)
}

function useMinDwell(condition: boolean, minMs: number): boolean {
  const [dwelt, setDwelt] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (condition && !dwelt) {
      timerRef.current = setTimeout(() => setDwelt(true), minMs)
    }
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [condition, dwelt, minMs])
  return dwelt
}

// ── Stage node components ─────────────────────────────────────────────────────

function StageNode({
  label,
  icon,
  active,
  done,
  color,
}: {
  label: string
  icon: string
  active: boolean
  done: boolean
  color: string
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.82 }}
      animate={active && !done
        ? { opacity: 1, scale: 1, boxShadow: PULSE_BOX_SHADOW }
        : { opacity: 1, scale: 1 }
      }
      transition={active && !done
        ? { duration: 0.4, ease: [0.22, 1, 0.36, 1], boxShadow: { duration: 2, repeat: Infinity } }
        : { duration: 0.4, ease: [0.22, 1, 0.36, 1] }
      }
      className={`
        relative flex flex-col items-center gap-1 px-5 py-3 rounded-xl border
        ${done
          ? 'border-green-700 bg-green-950/40'
          : active
          ? `border-${color}-600 bg-${color}-950/40`
          : 'border-gray-700 bg-gray-900/50'
        }
        transition-colors duration-500
      `}
    >
      <span className="text-xl">{icon}</span>
      <span className={`text-xs font-semibold ${done ? 'text-green-400' : active ? `text-${color}-300` : 'text-gray-500'}`}>
        {label}
      </span>
      {done && (
        <motion.span
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          className="absolute -top-2 -right-2 w-4 h-4 rounded-full bg-green-500 flex items-center justify-center text-[9px]"
        >
          ✓
        </motion.span>
      )}
    </motion.div>
  )
}

function Edge({ active }: { active: boolean }) {
  return (
    <div className="flex items-center w-8 shrink-0">
      {active ? (
        <motion.div
          className="h-px bg-blue-500 w-full"
          variants={edgeVariants}
          initial="hidden"
          animate="visible"
        />
      ) : (
        <div className="h-px bg-gray-700 w-full" />
      )}
      <div className={`w-1.5 h-1.5 rotate-45 border-t border-r -ml-1 ${active ? 'border-blue-500' : 'border-gray-700'}`} />
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function FlowCanvas() {
  const demoStage    = useSwarmStore((s) => s.demoStage)
  const taskGraph    = useSwarmStore((s) => s.taskGraph)
  const taskStatuses = useSwarmStore((s) => s.taskStatuses)
  const synthesizing = useSwarmStore((s) => s.synthesizing)
  const runCompleted = useSwarmStore((s) => s.runCompleted)

  // Pacing gates — each stage must dwell for minimum time before the next fires
  const orchDwelt  = useMinDwell(demoStage !== 'idle' && demoStage !== 'orchestrating', PACING.ORCHESTRATOR_DWELL_MS)
  const workDwelt  = useMinDwell(demoStage === 'fact_checking' || demoStage === 'synthesizing' || demoStage === 'done', 0)
  const factDwelt  = useMinDwell(demoStage === 'synthesizing' || demoStage === 'done', PACING.FACT_CHECK_DWELL_MS)
  const synthDwelt = useMinDwell(demoStage === 'done', PACING.SYNTHESIS_DWELL_MS)

  // Derive displayed stage index (respects pacing)
  const si = stageIndex(demoStage === 'idle' ? 'orchestrating' : demoStage as Stage)

  // Current narrative to show
  const activeNarrative = NARRATIVES.find((n) => n.stage === demoStage) ?? null

  // Count fact_check and writing task statuses
  const factCheckIds = taskGraph?.tasks.filter(t => t.type === 'fact_check').map(t => t.id) ?? []
  const factCheckDone = factCheckIds.length > 0 && factCheckIds.every(id => taskStatuses[id] === 'completed' || taskStatuses[id] === 'failed')

  return (
    <div className="h-full flex flex-col bg-gray-950 select-none">
      {/* ── Pipeline row ────────────────────────────────────────────── */}
      <div className="flex items-center justify-center gap-0 px-6 pt-5 pb-2 shrink-0">
        <StageNode label="Query"       icon="❓" active={si >= 0} done={si > 0} color="gray" />
        <Edge active={si >= 1} />
        <StageNode label="Orchestrate" icon="🧭" active={si >= 1} done={si > 1} color="blue" />
        <Edge active={orchDwelt && si >= 2} />
        <StageNode label="Workers"     icon="⚡" active={si >= 2} done={si > 2} color="purple" />
        <Edge active={si >= 3} />
        <StageNode label="Fact-check"  icon="🔍" active={si >= 3} done={si > 3} color="amber" />
        <Edge active={factDwelt && si >= 4} />
        <StageNode label="Synthesize"  icon="📋" active={si >= 4} done={si >= 5 || runCompleted} color="green" />
      </div>

      {/* ── Narrative card ───────────────────────────────────────────── */}
      <div className="flex justify-center px-8 shrink-0" style={{ minHeight: 64 }}>
        <AnimatePresence mode="wait">
          {activeNarrative && (
            <motion.div
              key={activeNarrative.stage}
              variants={narrativeVariants}
              initial="hidden"
              animate="visible"
              exit="exit"
              className="max-w-xl text-center"
            >
              <span className="text-xs font-bold text-blue-400 uppercase tracking-wide">
                {activeNarrative.title}
              </span>
              <p className="text-xs text-gray-400 mt-0.5 leading-relaxed">
                {activeNarrative.body}
              </p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* ── Worker grid (expands to fill remaining space) ────────────── */}
      <div className="flex-1 overflow-auto px-4 pb-3">
        {taskGraph && <WorkerGrid />}
        {!taskGraph && demoStage === 'orchestrating' && (
          <div className="flex items-center justify-center h-full">
            <motion.div
              animate={{ opacity: [0.4, 1, 0.4] }}
              transition={{ duration: 1.6, repeat: Infinity }}
              className="text-xs text-blue-400"
            >
              Decomposing query into specialist tasks…
            </motion.div>
          </div>
        )}
      </div>
    </div>
  )
}
