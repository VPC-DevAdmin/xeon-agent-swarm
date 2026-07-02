import { useMemo, useState } from 'react'
import { CATEGORIES, SAMPLE_PROMPTS } from '../data/samplePrompts'

interface Props {
  open: boolean
  onClose: () => void
  onSelect: (prompt: string, recurring: boolean) => void
}

/**
 * SamplePromptGallery — a browsable library of prompts that decompose well
 * into multi-agent task plans. Category filter + free-text search.
 */
export function SamplePromptGallery({ open, onClose, onSelect }: Props) {
  const [category, setCategory] = useState<string>('All')
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return SAMPLE_PROMPTS.filter((p) => {
      if (category !== 'All' && p.category !== category) return false
      if (q && !(`${p.title} ${p.prompt}`.toLowerCase().includes(q))) return false
      return true
    })
  }, [category, search])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-4xl mx-4 max-h-[80vh] flex flex-col rounded-lg border border-gray-700 bg-gray-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-800">
          <span className="font-semibold text-gray-200 text-sm">Sample prompts</span>
          <span className="text-xs text-gray-500">{filtered.length} of {SAMPLE_PROMPTS.length}</span>
          <input
            autoFocus
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            className="ml-auto w-56 bg-gray-950 border border-gray-700 rounded px-2.5 py-1 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500"
          />
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-lg leading-none">×</button>
        </div>

        <div className="flex gap-1.5 px-5 py-2.5 border-b border-gray-800 overflow-x-auto">
          {['All', ...CATEGORIES].map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`flex-shrink-0 text-xs px-2.5 py-1 rounded-full border transition-colors ${
                category === c
                  ? 'bg-blue-900/60 text-blue-300 border-blue-700'
                  : 'text-gray-400 border-gray-800 hover:border-gray-600'
              }`}
            >
              {c}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto p-4 grid grid-cols-1 md:grid-cols-2 gap-2.5">
          {filtered.map((p) => (
            <button
              key={p.id}
              onClick={() => onSelect(p.prompt, !!p.recurring)}
              className="text-left rounded-lg border border-gray-800 bg-gray-950 p-3 hover:border-blue-700 transition-colors group"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="text-sm font-medium text-gray-200 group-hover:text-blue-300 transition-colors">
                  {p.title}
                </span>
                {p.recurring && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-purple-900/60 text-purple-300 border border-purple-800">
                    recurring
                  </span>
                )}
              </div>
              <p className="text-xs text-gray-500 line-clamp-3">{p.prompt}</p>
            </button>
          ))}
          {filtered.length === 0 && (
            <p className="text-sm text-gray-500 col-span-2 text-center py-8">No prompts match.</p>
          )}
        </div>
      </div>
    </div>
  )
}
