import { useCallback, useMemo } from 'react'
import ReactFlow, {
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
  MarkerType,
} from 'reactflow'
import 'reactflow/dist/style.css'
import clsx from 'clsx'
import { useSwarmStore } from '../store/swarmStore'
import type { TaskSpec } from '../types/swarm'

const TYPE_COLORS: Record<string, string> = {
  research:      '#7c3aed',
  analysis:      '#d97706',
  code:          '#0d9488',
  summarization: '#2563eb',
  vision:        '#db2777',
  fact_check:    '#0891b2',
  writing:       '#16a34a',
  general:       '#6b7280',
}

const STATUS_RING: Record<string, string> = {
  pending:   'border-gray-600',
  running:   'border-blue-400 animate-pulse-border',
  completed: 'border-green-500',
  failed:    'border-red-500',
}

function TaskNode({ data }: NodeProps) {
  const { task, status, meta } = data as {
    task: TaskSpec
    status: string
    meta: { model?: string; hardware?: string } | null
  }

  const typeColor = TYPE_COLORS[task.type] ?? '#6b7280'

  return (
    <>
      <Handle type="target" position={Position.Top} className="!bg-gray-600" />
      <div
        className={clsx(
          'px-3 py-2 rounded-lg border-2 bg-gray-900 min-w-36 max-w-52 text-center transition-all duration-300',
          STATUS_RING[status ?? 'pending'],
        )}
      >
        <div
          className="text-xs font-bold px-1.5 py-0.5 rounded mb-1 inline-block"
          style={{ background: typeColor + '33', color: typeColor, border: `1px solid ${typeColor}66` }}
        >
          {task.type}
        </div>
        <p className="text-xs text-gray-200 leading-tight line-clamp-2">{task.description}</p>
        {meta && (
          <div className="mt-1 flex items-center justify-center gap-1">
            <span className="text-gray-500 text-xs truncate max-w-24">{meta.model?.split('/').pop()}</span>
            {meta.hardware && (
              <span
                className={clsx(
                  'text-xs px-1 rounded',
                  meta.hardware === 'gpu' ? 'text-green-400' : 'text-gray-500',
                )}
              >
                {meta.hardware.toUpperCase()}
              </span>
            )}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-gray-600" />
    </>
  )
}

const nodeTypes = { task: TaskNode }

function layoutNodes(tasks: TaskSpec[]): { x: number; y: number }[] {
  // Simple layered layout based on dependency depth
  const depth: Record<string, number> = {}
  const queue = tasks.filter((t) => t.dependencies.length === 0)
  queue.forEach((t) => (depth[t.id] = 0))

  let changed = true
  while (changed) {
    changed = false
    for (const t of tasks) {
      const d = Math.max(0, ...t.dependencies.map((dep) => (depth[dep] ?? 0) + 1))
      if (depth[t.id] !== d) {
        depth[t.id] = d
        changed = true
      }
    }
  }

  const byLayer: Record<number, TaskSpec[]> = {}
  for (const t of tasks) {
    const layer = depth[t.id] ?? 0
    ;(byLayer[layer] ??= []).push(t)
  }

  const positions: { x: number; y: number }[] = tasks.map((t) => {
    const layer = depth[t.id] ?? 0
    const siblings = byLayer[layer] ?? []
    const idx = siblings.indexOf(t)
    const spread = 220
    return {
      x: (idx - (siblings.length - 1) / 2) * spread,
      y: layer * 130,
    }
  })

  return positions
}

export function TaskGraph() {
  const taskGraph = useSwarmStore((s) => s.taskGraph)
  const taskStatuses = useSwarmStore((s) => s.taskStatuses)
  const taskMeta = useSwarmStore((s) => s.taskMeta)

  const { nodes, edges } = useMemo<{ nodes: Node[]; edges: Edge[] }>(() => {
    if (!taskGraph) return { nodes: [], edges: [] }

    const positions = layoutNodes(taskGraph.tasks)

    const nodes: Node[] = taskGraph.tasks.map((task, i) => ({
      id: task.id,
      type: 'task',
      position: positions[i],
      data: {
        task,
        status: taskStatuses[task.id] ?? 'pending',
        meta: taskMeta[task.id] ?? null,
      },
    }))

    const edges: Edge[] = taskGraph.tasks.flatMap((task) =>
      task.dependencies.map((dep) => ({
        id: `${dep}->${task.id}`,
        source: dep,
        target: task.id,
        animated: taskStatuses[task.id] === 'running',
        markerEnd: { type: MarkerType.ArrowClosed, color: '#4b5563' },
        style: { stroke: '#4b5563' },
      })),
    )

    return { nodes, edges }
  }, [taskGraph, taskStatuses, taskMeta])

  if (!taskGraph) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
        Waiting for task graph…
      </div>
    )
  }

  return (
    <div className="h-64 rounded-lg border border-gray-800 overflow-hidden bg-gray-950">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#1f2937" gap={20} />
        <Controls showInteractive={false} className="!bg-gray-900 !border-gray-700" />
      </ReactFlow>
    </div>
  )
}
