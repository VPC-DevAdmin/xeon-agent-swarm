import type { Artifact, CitationSetContent } from '../../types/swarm'

export function CitationList({ artifact }: { artifact: Artifact }) {
  const c = artifact.content as unknown as CitationSetContent
  if (!c?.citations?.length) return null

  return (
    <div className="rounded-lg border border-blue-900/40 bg-gray-950 overflow-hidden">
      <div className="px-4 py-2 border-b border-gray-800 text-xs text-blue-300 font-semibold">
        📎 Sources
      </div>
      <div className="p-3 space-y-2">
        {c.citations.map((cit, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            <span className="text-blue-500 font-semibold shrink-0 w-5 text-right">[{i + 1}]</span>
            <div className="flex-1">
              <a
                href={cit.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-300 hover:text-blue-200 font-medium hover:underline"
              >
                {cit.title}
              </a>
              {cit.snippet && (
                <p className="text-gray-500 text-[11px] mt-0.5 line-clamp-2">{cit.snippet}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
