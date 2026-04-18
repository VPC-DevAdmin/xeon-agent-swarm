import { motion } from 'framer-motion'
import type { Artifact, ClaimVerdictContent } from '../../types/swarm'

const VERDICT_STYLE = {
  supported:   { icon: '✓', color: 'text-green-400', bg: 'bg-green-950/30 border-green-900' },
  unsupported: { icon: '✗', color: 'text-red-400',   bg: 'bg-red-950/30 border-red-900' },
  uncertain:   { icon: '?', color: 'text-amber-400',  bg: 'bg-amber-950/30 border-amber-900' },
}

export function ClaimVerdictList({ artifacts }: { artifacts: Artifact[] }) {
  if (!artifacts.length) return null

  return (
    <div className="rounded-lg border border-amber-900/40 bg-gray-950 overflow-hidden">
      <div className="px-4 py-2 border-b border-gray-800 text-xs text-amber-300 font-semibold">
        🔍 Fact-check Results
      </div>
      <div className="p-3 space-y-2">
        {artifacts.map((artifact, i) => {
          const c = artifact.content as unknown as ClaimVerdictContent
          const style = VERDICT_STYLE[c.verdict] ?? VERDICT_STYLE.uncertain
          return (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.05 }}
              className={`rounded border px-3 py-2 text-xs ${style.bg}`}
            >
              <div className="flex items-start gap-2">
                <span className={`font-bold text-sm shrink-0 ${style.color}`}>{style.icon}</span>
                <div className="flex-1">
                  <div className="text-gray-200 font-medium">{c.claim}</div>
                  {c.evidence && (
                    <div className="text-gray-400 mt-0.5 text-[11px]">{c.evidence}</div>
                  )}
                  {c.source_url && (
                    <a
                      href={c.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-400 text-[10px] hover:underline"
                    >
                      source ↗
                    </a>
                  )}
                </div>
                <span className={`text-[10px] font-semibold shrink-0 ${style.color}`}>
                  {c.verdict}
                </span>
              </div>
            </motion.div>
          )
        })}
      </div>
    </div>
  )
}
