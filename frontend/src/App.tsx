import { Routes, Route } from 'react-router-dom'
import { NavBar } from './components/NavBar'
import { LiveRunPage } from './pages/LiveRunPage'
import { JobsPage } from './pages/JobsPage'
import { RunsPage, RunDetailPage } from './pages/RunsPage'
import { ConnectorsPage } from './pages/ConnectorsPage'

export default function App() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      <NavBar />
      <div className="flex-1">
        <Routes>
          <Route path="/" element={<LiveRunPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/connectors" element={<ConnectorsPage />} />
        </Routes>
      </div>
    </div>
  )
}
