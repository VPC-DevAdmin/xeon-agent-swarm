/**
 * OutputPanel — the growing bottom zone where typed artifacts appear.
 *
 * Renders artifacts in priority order:
 *   1. Executive summary card (with TTS audio player)
 *   2. Comparison table(s)
 *   3. Mermaid diagram(s)
 *   4. Chart(s)
 *   5. Code block(s) with syntax-valid badge
 *   6. Claim verdicts from fact-checker
 *   7. Citations from research
 *   8. Extracted data from vision
 */
import { useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useSwarmStore } from '../store/swarmStore'
import type { Artifact, ArtifactType } from '../types/swarm'
import { TableArtifact }       from './artifacts/TableArtifact'
import { DiagramArtifact }     from './artifacts/DiagramArtifact'
import { ChartArtifact }       from './artifacts/ChartArtifact'
import { CodeArtifact }        from './artifacts/CodeArtifact'
import { ClaimVerdictList }    from './artifacts/ClaimVerdictList'
import { CitationList }        from './artifacts/CitationList'
import { ExtractedDataCard }   from './artifacts/ExtractedDataCard'
import { ExecutiveSummaryCard } from './artifacts/ExecutiveSummaryCard'

// Priority order for rendering artifacts
const ARTIFACT_ORDER: ArtifactType[] = [
  'table', 'diagram', 'chart', 'code',
  'claim_verdict', 'citation_set', 'extracted_data',
]

function groupArtifacts(artifacts: Artifact[]): Record<ArtifactType, Artifact[]> {
  const groups: Partial<Record<ArtifactType, Artifact[]>> = {}
  for (const art of artifacts) {
    if (!groups[art.type]) groups[art.type] = []
    groups[art.type]!.push(art)
  }
  return groups as Record<ArtifactType, Artifact[]>
}

function ArtifactSection({ type, artifacts }: { type: ArtifactType; artifacts: Artifact[] }) {
  if (!artifacts.length) return null

  const sectionVariants = {
    hidden:  { opacity: 0, y: 16 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] } },
  }

  return (
    <motion.div variants={sectionVariants} initial="hidden" animate="visible">
      {type === 'table' && artifacts.map((a, i) => (
        <TableArtifact key={i} artifact={a} />
      ))}
      {type === 'diagram' && artifacts.map((a, i) => (
        <DiagramArtifact key={i} artifact={a} />
      ))}
      {type === 'chart' && artifacts.map((a, i) => (
        <ChartArtifact key={i} artifact={a} />
      ))}
      {type === 'code' && artifacts.map((a, i) => (
        <CodeArtifact key={i} artifact={a} />
      ))}
      {type === 'claim_verdict' && (
        <ClaimVerdictList artifacts={artifacts} />
      )}
      {type === 'citation_set' && artifacts.map((a, i) => (
        <CitationList key={i} artifact={a} />
      ))}
      {type === 'extracted_data' && artifacts.map((a, i) => (
        <ExtractedDataCard key={i} artifact={a} />
      ))}
    </motion.div>
  )
}

export function OutputPanel() {
  const document      = useSwarmStore((s) => s.document)
  const artifacts     = useSwarmStore((s) => s.artifacts)
  const runCompleted  = useSwarmStore((s) => s.runCompleted)
  const synthesizing  = useSwarmStore((s) => s.synthesizing)
  const retryRun      = useSwarmStore((s) => s.retryRun)
  const panelRef      = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom as new artifacts appear
  useEffect(() => {
    if (panelRef.current) {
      panelRef.current.scrollTop = panelRef.current.scrollHeight
    }
  }, [artifacts.length, document])

  if (!synthesizing && !runCompleted && !artifacts.length) {
    return null
  }

  const groups = groupArtifacts(
    // Use document.artifacts when available (post-run), otherwise live artifacts
    document?.artifacts?.length ? document.artifacts : artifacts
  )

  return (
    <div ref={panelRef} className="max-w-screen-xl mx-auto px-6 py-5 space-y-5 overflow-y-auto" style={{ maxHeight: '100%' }}>

      {/* Synthesis header */}
      {synthesizing && !document && (
        <motion.div
          animate={{ opacity: [0.6, 1, 0.6] }}
          transition={{ duration: 1.4, repeat: Infinity }}
          className="text-xs text-green-400 text-center py-2"
        >
          Synthesizer assembling report…
        </motion.div>
      )}

      {/* Executive summary */}
      <AnimatePresence>
        {document && <ExecutiveSummaryCard document={document} />}
      </AnimatePresence>

      {/* Key findings */}
      {document?.key_findings?.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-lg border border-blue-900/50 bg-blue-950/20 p-4"
        >
          <h3 className="text-xs font-bold text-blue-300 uppercase tracking-wide mb-3">Key Findings</h3>
          <ul className="space-y-1.5">
            {document.key_findings.map((f, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-gray-300">
                <span className="text-blue-400 font-bold shrink-0">{i + 1}.</span>
                {f}
              </li>
            ))}
          </ul>
        </motion.div>
      )}

      {/* Typed artifacts in priority order */}
      {ARTIFACT_ORDER.map((type) => (
        <ArtifactSection key={type} type={type} artifacts={groups[type] ?? []} />
      ))}

      {/* Report sections */}
      {document?.sections?.map((section, i) => (
        <motion.div
          key={i}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0, transition: { delay: i * 0.1 } }}
          className="rounded-lg border border-gray-800 bg-gray-900/50 p-4"
        >
          <h3 className="text-sm font-semibold text-gray-200 mb-2">{section.title}</h3>
          <div className="text-sm text-gray-400 leading-relaxed whitespace-pre-wrap">{section.content}</div>
          {section.sources?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {section.sources.map((src, j) => (
                <a
                  key={j}
                  href={src}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-blue-400 hover:text-blue-300 transition-colors"
                >
                  [{j + 1}]
                </a>
              ))}
            </div>
          )}
        </motion.div>
      ))}

      {/* Run again footer */}
      {runCompleted && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8 }}
          className="flex justify-center pt-2 pb-4"
        >
          <button
            onClick={() => retryRun()}
            className="flex items-center gap-1.5 text-xs px-4 py-2 rounded-full border border-gray-700 bg-gray-900/60 text-gray-400 hover:text-white hover:border-blue-700 transition-colors"
          >
            ↺ Run again with same query
          </button>
        </motion.div>
      )}
    </div>
  )
}
