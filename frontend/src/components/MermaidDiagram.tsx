import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'

mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  themeVariables: {
    background: '#111827',
    primaryColor: '#1e3a5f',
    primaryTextColor: '#e5e7eb',
    primaryBorderColor: '#3b82f6',
    lineColor: '#6b7280',
    secondaryColor: '#1f2937',
    tertiaryColor: '#111827',
  },
})

let _counter = 0

interface Props {
  code: string
}

export function MermaidDiagram({ code }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!containerRef.current || !code.trim()) return

    const id = `mermaid-${++_counter}`
    setError(null)

    mermaid
      .render(id, code.trim())
      .then(({ svg }) => {
        if (containerRef.current) {
          containerRef.current.innerHTML = svg
        }
      })
      .catch((err) => {
        console.error('[mermaid] render failed:', err)
        setError('Diagram could not be rendered')
      })
  }, [code])

  if (error) {
    return (
      <div className="text-xs text-red-400 p-2 border border-red-900 rounded bg-red-950/30">
        {error}
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="overflow-x-auto rounded-lg bg-gray-900/50 p-4 [&_svg]:max-w-full [&_svg]:h-auto"
    />
  )
}
