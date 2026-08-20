import { Navigate, Outlet, Routes, Route } from 'react-router-dom'
import { NavBar } from './components/NavBar'
import { ConsolePage } from './pages/ConsolePage'
import { CapacityPage } from './pages/CapacityPage'
import { ActivityPage } from './pages/ActivityPage'
import { RunDetailPage } from './pages/RunsPage'
import { ConnectorsPage } from './pages/ConnectorsPage'

/** Secondary pages keep the classic nav shell; the console owns its own chrome. */
function WithNav() {
  return (
    <div className="min-h-screen flex flex-col">
      <NavBar />
      <div className="flex-1">
        <Outlet />
      </div>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      {/* Two demos, one app: the capacity.* hostname fronts the planner. */}
      <Route path="/" element={window.location.hostname.startsWith('capacity.')
        ? <CapacityPage /> : <ConsolePage />} />
      <Route path="/planner" element={<CapacityPage />} />
      <Route element={<WithNav />}>
        <Route path="/activity" element={<ActivityPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
        <Route path="/connectors" element={<ConnectorsPage />} />
        {/* Legacy routes fold into Activity */}
        <Route path="/jobs" element={<Navigate to="/activity?tab=scheduled" replace />} />
        <Route path="/runs" element={<Navigate to="/activity?tab=history" replace />} />
      </Route>
    </Routes>
  )
}
