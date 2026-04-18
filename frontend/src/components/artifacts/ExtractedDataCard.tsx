import { motion } from 'framer-motion'
import type { Artifact, ExtractedDataContent } from '../../types/swarm'

export function ExtractedDataCard({ artifact }: { artifact: Artifact }) {
  const c = artifact.content as unknown as ExtractedDataContent
  if (!c?.data_points?.length) return null

  return (
    <div className="rounded-lg border border-violet-900/50 bg-gray-950 overflow-hidden">
      <div className="px-4 py-2 border-b border-gray-800 text-xs text-violet-300 font-semibold">
        🔬 Extracted Data
      </div>

      {c.description && (
        <div className="px-4 pt-2 pb-1 text-xs text-gray-400">{c.description}</div>
      )}

      <div className="p-3 grid grid-cols-2 sm:grid-cols-3 gap-2">
        {c.data_points.map((dp, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: i * 0.04 }}
            className="rounded border border-violet-900/40 bg-violet-950/20 px-3 py-2"
          >
            <div className="text-[10px] text-violet-400 uppercase tracking-wide font-semibold truncate">
              {dp.label}
            </div>
            <div className="text-sm text-white font-bold mt-0.5">
              {dp.value}
              {dp.unit && (
                <span className="text-xs font-normal text-gray-400 ml-1">{dp.unit}</span>
              )}
            </div>
          </motion.div>
        ))}
      </div>

      {c.source_image && (
        <div className="px-3 pb-2 text-[10px] text-gray-600">
          Source: {c.source_image}
        </div>
      )}

      <div className="px-3 py-1.5 text-[10px] text-gray-600 border-t border-gray-900">
        Produced by {artifact.worker_id} · conf {(artifact.confidence * 100).toFixed(0)}%
      </div>
    </div>
  )
}
