import { useSwarmStore } from '../store/swarmStore'
import { TaskGraph } from './TaskGraph'
import { WorkerCard } from './WorkerCard'

export function SwarmPanel() {
  const taskGraph = useSwarmStore((s) => s.taskGraph)
  const synthesizing = useSwarmStore((s) => s.synthesizing)
  const isRunning = useSwarmStore((s) => s.isRunning)
  const runCompleted = useSwarmStore((s) => s.runCompleted)

  return (
    <div className="flex flex-col gap-4 min-h-0">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-blue-400">Swarm Pipeline</h2>
        <span className="text-xs text-gray-500">parallel specialized agents</span>
        {isRunning && !runCompleted && (
          <span className="ml-auto text-xs text-blue-400 animate-pulse">● running</span>
        )}
        {runCompleted && (
          <span className="ml-auto text-xs text-green-400">● done</span>
        )}
      </div>

      {/* Task Graph DAG */}
      {taskGraph && (
        <>
          <div className="text-xs text-gray-500 italic border-l-2 border-gray-700 pl-2">
            {taskGraph.reasoning}
          </div>
          <TaskGraph />
        </>
      )}

      {/* Worker Cards */}
      {taskGraph && taskGraph.tasks.length > 0 && (
        <div className="flex flex-col gap-2">
          {taskGraph.tasks.map((task) => (
            <WorkerCard key={task.id} task={task} />
          ))}
        </div>
      )}

      {/* Synthesis indicator */}
      {synthesizing && (
        <div className="flex items-center gap-2 text-sm text-amber-400">
          <span className="animate-spin">⚙</span>
          Synthesizing results…
        </div>
      )}

      {/* Empty state */}
      {!taskGraph && !isRunning && (
        <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
          Submit a query to see the swarm in action
        </div>
      )}
    </div>
  )
}
