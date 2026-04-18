import { useEffect, useRef, useState } from 'react'
import type { Artifact, DiagramContent } from '../../types/swarm'

let mermaidInitialized = false

async function ensureMermaid() {
  const mermaid = (await import('mermaid')).default
  if (!mermaidInitialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      themeVariables: {
        background: '#0a0a0f',
        primaryColor: '#1e3a5f',
        primaryTextColor: '#e2e8f0',
        lineColor: '#4a6fa5',
        edgeLabelBackground: '#0f172a',
        fontSize: '13px',
      },
    })
    mermaidInitialized = true
  }
  return mermaid
}

export function DiagramArtifact({ artifact }: { artifact: Artifact }) {
  const c      = artifact.content as unknown as DiagramContent
  const ref    = useRef<HTMLDivElement>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!ref.current || !c?.mermaid) return
    const id = `mmd-${Math.random().toString(36).slice(2)}`
    ensureMermaid().then(async (mermaid) => {
      try {
        const { svg } = await mermaid.render(id, c.mermaid)
        if (ref.current) ref.current.innerHTML = svg
      } catch (e) {
        setErr(String(e))
      }
    })
  }, [c?.mermaid])

  if (!c?.mermaid) return null

  return (
    <div className="rounded-lg border border-cyan-900/50 bg-gray-950 overflow-hidden">
      {c.caption && (
        <div className="px-4 py-2 border-b border-gray-800 text-xs text-cyan-300 font-semibold">
          🔷 {c.caption}
        </div>
      )}
      <div className="p-4 overflow-x-auto">
        {err ? (
          <pre className="text-red-400 text-xs">{err}</pre>
        ) : (
          <div ref={ref} className="[&>svg]:max-w-full [&>svg]:mx-auto" />
        )}
      </div>
      <div className="px-3 py-1.5 text-[10px] text-gray-600 border-t border-gray-900">
        Produced by {artifact.worker_id} · conf {(artifact.confidence * 100).toFixed(0)}%
      </div>
    </div>
  )
}
