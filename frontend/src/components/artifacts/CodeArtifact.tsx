import { useState } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { Artifact, CodeContent } from '../../types/swarm'

export function CodeArtifact({ artifact }: { artifact: Artifact }) {
  const c = artifact.content as unknown as CodeContent
  const [copied, setCopied] = useState(false)

  if (!c?.code) return null

  const copy = async () => {
    await navigator.clipboard.writeText(c.code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="rounded-lg border border-cyan-900/50 bg-gray-950 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-800 bg-gray-900/60">
        <span className="text-xs font-mono text-cyan-400">{c.language}</span>
        {c.description && (
          <span className="text-xs text-gray-400">— {c.description}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {/* Syntax validity badge */}
          {c.syntax_valid !== undefined && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${
              c.syntax_valid
                ? 'bg-green-950 text-green-400 border border-green-800'
                : 'bg-red-950 text-red-400 border border-red-800'
            }`}>
              {c.syntax_valid ? '✓ syntax valid' : '✗ syntax error'}
            </span>
          )}
          <button
            onClick={copy}
            className="text-[10px] px-2 py-0.5 rounded bg-gray-800 text-gray-400 hover:text-white transition-colors"
          >
            {copied ? '✓ copied' : 'copy'}
          </button>
        </div>
      </div>

      {/* Code block */}
      <div className="overflow-x-auto">
        <SyntaxHighlighter
          language={c.language || 'text'}
          style={vscDarkPlus}
          customStyle={{ margin: 0, background: 'transparent', fontSize: '12px', padding: '16px' }}
          wrapLongLines={false}
        >
          {c.code}
        </SyntaxHighlighter>
      </div>

      <div className="px-3 py-1.5 text-[10px] text-gray-600 border-t border-gray-900">
        Produced by {artifact.worker_id} · conf {(artifact.confidence * 100).toFixed(0)}%
      </div>
    </div>
  )
}
