import { useState, useRef } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { MermaidDiagram } from './MermaidDiagram'
import { TaskGraph } from './TaskGraph'
import { WorkerCard } from './WorkerCard'
import { useSwarmStore } from '../store/swarmStore'
import type { DocumentSection, CodeSnippet } from '../types/swarm'

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

// ── Sub-components ────────────────────────────────────────────────────────────

function AudioPlayer({ url }: { url: string }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)

  const toggle = () => {
    const el = audioRef.current
    if (!el) return
    if (playing) {
      el.pause()
      setPlaying(false)
    } else {
      el.play().then(() => setPlaying(true)).catch(console.error)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={toggle}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-900/40 border border-blue-700 text-blue-300 text-xs hover:bg-blue-900/60 transition-colors"
      >
        <span>{playing ? '⏸' : '▶'}</span>
        <span>{playing ? 'Pause' : 'Play'} executive summary</span>
      </button>
      <audio
        ref={audioRef}
        src={`${API_BASE}${url}`}
        onEnded={() => setPlaying(false)}
        className="hidden"
      />
    </div>
  )
}

function Section({ section, index }: { section: DocumentSection; index: number }) {
  const [open, setOpen] = useState(index < 2)

  return (
    <div className="border border-gray-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-900 hover:bg-gray-800/80 transition-colors text-left"
      >
        <span className="text-sm font-medium text-gray-200">{section.title}</span>
        <span className="text-gray-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="px-4 pb-4 pt-3 bg-gray-950">
          <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">{section.content}</p>
          {section.sources.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1">
              {section.sources.map((src, i) => (
                <a
                  key={i}
                  href={src}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-blue-400 hover:text-blue-300 underline break-all"
                >
                  {src}
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function CodeBlock({ snippet }: { snippet: CodeSnippet }) {
  const [copied, setCopied] = useState(false)

  const copy = () => {
    navigator.clipboard.writeText(snippet.code).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="border border-gray-800 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-800">
        <div>
          <span className="text-xs font-mono text-teal-400 mr-2">{snippet.language}</span>
          <span className="text-xs text-gray-400">{snippet.description}</span>
        </div>
        <button
          onClick={copy}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <SyntaxHighlighter
        language={snippet.language}
        style={vscDarkPlus}
        customStyle={{ margin: 0, borderRadius: 0, background: '#0d1117', fontSize: 12 }}
        showLineNumbers
      >
        {snippet.code}
      </SyntaxHighlighter>
    </div>
  )
}

// ── Live swarm view (shown while running) ─────────────────────────────────────

function LiveSwarmView() {
  const taskGraph = useSwarmStore((s) => s.taskGraph)
  const synthesizing = useSwarmStore((s) => s.synthesizing)
  const isRunning = useSwarmStore((s) => s.isRunning)

  return (
    <div className="flex flex-col gap-4">
      {taskGraph ? (
        <>
          <div className="text-xs text-gray-500 italic border-l-2 border-gray-700 pl-2">
            {taskGraph.reasoning}
          </div>
          <TaskGraph />
          <div className="flex flex-col gap-2">
            {taskGraph.tasks.map((task) => (
              <WorkerCard key={task.id} task={task} />
            ))}
          </div>
        </>
      ) : (
        <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
          {isRunning ? (
            <span className="animate-pulse">Orchestrating task graph…</span>
          ) : (
            'Submit a query to see the swarm in action'
          )}
        </div>
      )}

      {synthesizing && (
        <div className="flex items-center gap-2 text-sm text-amber-400">
          <span className="animate-spin inline-block">⚙</span>
          Synthesizing intelligence report…
        </div>
      )}
    </div>
  )
}

// ── Main DocumentViewer ───────────────────────────────────────────────────────

export function DocumentViewer() {
  const document = useSwarmStore((s) => s.document)
  const finalAnswer = useSwarmStore((s) => s.finalAnswer)
  const isRunning = useSwarmStore((s) => s.isRunning)
  const swarmLatencyMs = useSwarmStore((s) => s.swarmLatencyMs)
  const swarmTaskCount = useSwarmStore((s) => s.swarmTaskCount)

  const formatLatency = (ms: number) =>
    ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(1)}s`

  return (
    <div className="flex flex-col gap-4 min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-blue-400">Swarm Intelligence Report</h2>
        <span className="text-xs text-gray-500">parallel specialist agents</span>
        {isRunning && (
          <span className="ml-auto text-xs text-blue-400 animate-pulse">● running</span>
        )}
        {finalAnswer && !isRunning && (
          <span className="ml-auto text-xs text-green-400">
            ● done {swarmTaskCount && `· ${swarmTaskCount} tasks`}
            {swarmLatencyMs && ` · ${formatLatency(swarmLatencyMs)}`}
          </span>
        )}
      </div>

      {/* Live view while running */}
      {(isRunning || (!document && !finalAnswer)) && <LiveSwarmView />}

      {/* Intelligence report once complete */}
      {document && (
        <div className="flex flex-col gap-5">
          {/* Title */}
          <div>
            <h1 className="text-lg font-bold text-white leading-snug">{document.title}</h1>
          </div>

          {/* Executive summary + audio */}
          <div className="rounded-lg border border-blue-900/60 bg-blue-950/20 p-4">
            <div className="flex items-start justify-between gap-3 mb-2">
              <h3 className="text-xs font-semibold text-blue-400 uppercase tracking-wide">
                Executive Summary
              </h3>
              {document.tts_audio_url && (
                <AudioPlayer url={document.tts_audio_url} />
              )}
            </div>
            <p className="text-sm text-gray-200 leading-relaxed">{document.executive_summary}</p>
          </div>

          {/* Key findings */}
          {document.key_findings.length > 0 && (
            <div className="rounded-lg border border-amber-900/50 bg-amber-950/10 p-4">
              <h3 className="text-xs font-semibold text-amber-400 uppercase tracking-wide mb-3">
                Key Findings
              </h3>
              <ul className="flex flex-col gap-1.5">
                {document.key_findings.map((finding, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-gray-200">
                    <span className="text-amber-500 mt-0.5 flex-shrink-0">▸</span>
                    {finding}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Sections */}
          {document.sections.length > 0 && (
            <div className="flex flex-col gap-2">
              {document.sections.map((section, i) => (
                <Section key={i} section={section} index={i} />
              ))}
            </div>
          )}

          {/* Mermaid diagram */}
          {document.diagram_mermaid && (
            <div>
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
                Architecture Diagram
              </h3>
              <MermaidDiagram code={document.diagram_mermaid} />
            </div>
          )}

          {/* Code snippets */}
          {document.code_snippets.length > 0 && (
            <div className="flex flex-col gap-3">
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
                Code Examples
              </h3>
              {document.code_snippets.map((snippet, i) => (
                <CodeBlock key={i} snippet={snippet} />
              ))}
            </div>
          )}

          {/* Sources */}
          {document.sources.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Sources
              </h3>
              <div className="flex flex-col gap-1">
                {document.sources.map((src, i) => (
                  <a
                    key={i}
                    href={src}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-blue-400 hover:text-blue-300 underline break-all"
                  >
                    {src}
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Fallback: plain final answer if no document */}
      {!document && finalAnswer && !isRunning && (
        <div className="rounded-lg border border-green-700 bg-gray-900 p-4">
          <h3 className="text-sm font-semibold text-green-400 mb-2">Synthesized Answer</h3>
          <p className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">{finalAnswer}</p>
        </div>
      )}
    </div>
  )
}
