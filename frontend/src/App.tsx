import { Navigate, Routes, Route } from 'react-router-dom'
import { NavBar } from './components/NavBar'
import { LiveRunPage } from './pages/LiveRunPage'
import { ActivityPage } from './pages/ActivityPage'
import { RunDetailPage } from './pages/RunsPage'
import { ConnectorsPage } from './pages/ConnectorsPage'

export default function App() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      <NavBar />
      <div className="flex-1">
        <Routes>
          <Route path="/" element={<LiveRunPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/connectors" element={<ConnectorsPage />} />
          {/* Legacy routes fold into Activity */}
          <Route path="/jobs" element={<Navigate to="/activity?tab=scheduled" replace />} />
          <Route path="/runs" element={<Navigate to="/activity?tab=history" replace />} />
        </Routes>
      </div>
    </div>
  )
}
