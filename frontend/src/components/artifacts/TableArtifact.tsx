import { useState } from 'react'
import type { Artifact, TableContent } from '../../types/swarm'

export function TableArtifact({ artifact }: { artifact: Artifact }) {
  const c = artifact.content as unknown as TableContent
  const [sortCol, setSortCol] = useState<number | null>(null)
  const [sortAsc, setSortAsc] = useState(true)

  if (!c?.headers?.length) return null

  const rows = sortCol !== null
    ? [...(c.rows ?? [])].sort((a, b) => {
        const cmp = String(a[sortCol] ?? '').localeCompare(String(b[sortCol] ?? ''))
        return sortAsc ? cmp : -cmp
      })
    : c.rows ?? []

  const handleSort = (col: number) => {
    if (sortCol === col) setSortAsc((a) => !a)
    else { setSortCol(col); setSortAsc(true) }
  }

  return (
    <div className="rounded-lg border border-purple-900/50 bg-gray-950 overflow-hidden">
      {c.caption && (
        <div className="px-4 py-2 border-b border-gray-800 text-xs text-purple-300 font-semibold">
          📊 {c.caption}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-gray-900/80 border-b border-gray-800">
              {c.headers.map((h, i) => (
                <th
                  key={i}
                  onClick={() => handleSort(i)}
                  className="text-left px-3 py-2 text-gray-300 font-semibold cursor-pointer hover:text-white select-none whitespace-nowrap"
                >
                  {h}
                  {sortCol === i && <span className="ml-1 text-purple-400">{sortAsc ? '↑' : '↓'}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={i}
                className={`border-b border-gray-900 ${i % 2 === 0 ? 'bg-gray-950' : 'bg-gray-900/30'} hover:bg-gray-800/40 transition-colors`}
              >
                {row.map((cell, j) => (
                  <td key={j} className="px-3 py-2 text-gray-300">
                    {cell === '✓' || cell === '✅'
                      ? <span className="text-green-400 font-bold">{cell}</span>
                      : cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="px-3 py-1.5 text-[10px] text-gray-600 border-t border-gray-900">
        Produced by {artifact.worker_id} · conf {(artifact.confidence * 100).toFixed(0)}%
      </div>
    </div>
  )
}
