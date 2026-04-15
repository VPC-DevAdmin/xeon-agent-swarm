interface Props {
  title: string
  answer: string
  latencyMs: number | null
  model?: string
  hardware?: string
  taskCount?: number | null
}

export function ResultCard({ title, answer, latencyMs, model, hardware, taskCount }: Props) {
  const latencyText = latencyMs != null
    ? latencyMs < 1000
      ? `${latencyMs.toFixed(0)}ms`
      : `${(latencyMs / 1000).toFixed(1)}s`
    : null

  return (
    <div className="rounded-lg border border-green-700 bg-gray-900 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-green-400">{title}</h3>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          {model && <span className="truncate max-w-40">{model}</span>}
          {hardware && (
            <span className={
              hardware === 'gpu'
                ? 'text-green-400 border border-green-700 px-1 rounded'
                : 'text-gray-400 border border-gray-600 px-1 rounded'
            }>
              {hardware.toUpperCase()}
            </span>
          )}
          {taskCount != null && (
            <span className="text-blue-400">{taskCount} tasks</span>
          )}
          {latencyText && (
            <span className="text-amber-400 font-mono">{latencyText}</span>
          )}
        </div>
      </div>
      <div className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">
        {answer}
      </div>
    </div>
  )
}
