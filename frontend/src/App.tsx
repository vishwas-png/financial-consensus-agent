import { BrowserRouter, Navigate, NavLink, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthProvider'
import { useAuth } from './auth/context'
import { HistoryPage } from './pages/HistoryPage'
import { LoginPage } from './pages/LoginPage'
import { NewResearchPage } from './pages/NewResearchPage'
import { RunPage } from './pages/RunPage'

/** Signed out: only the login page. Signed in: header + pages. */
export function AppRoutes() {
  const { user, signOut } = useAuth()
  if (!user) return <LoginPage />

  return (
    <>
      <header className="app-header">
        <strong>Financial Consensus Agent</strong>
        <nav>
          <NavLink to="/" end>
            New research
          </NavLink>
          <NavLink to="/history">History</NavLink>
        </nav>
        <span className="user">
          {user.email}{' '}
          <button type="button" onClick={signOut}>
            Sign out
          </button>
        </span>
      </header>
      <Routes>
        <Route path="/" element={<NewResearchPage />} />
        <Route path="/runs/:runId" element={<RunPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  )
}
