import { QueryClientProvider } from '@tanstack/react-query'
import { LoaderCircle, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'
import { ApiError, errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { LoginPage } from '../features/auth/LoginPage'
import { PasswordChangePage } from '../features/auth/PasswordChangePage'
import { useSession } from '../features/auth/use-session'
import { AppShell } from '../layout/AppShell'
import { AccountPage } from '../pages/AccountPage'
import { WorkspacePage } from '../pages/WorkspacePage'
import { TicketListPage } from '../pages/TicketListPage'
import { TicketCreatePage } from '../pages/TicketCreatePage'
import { TicketDetailPage } from '../pages/TicketDetailPage'
import { SubscriberPage } from '../pages/SubscriberPage'
import { UsersPage } from '../pages/UsersPage'
import { queryClient } from './query-client'

function AppRoutes() {
  const [sessionRevision, setSessionRevision] = useState(0)
  const [revoked, setRevoked] = useState(false)
  const session = useSession(sessionRevision)
  const sessionChanged = () => {
    setRevoked(false)
    setSessionRevision((revision) => revision + 1)
  }
  useEffect(() => {
    if (!revoked && session.error instanceof ApiError && session.error.status === 401) {
      queryClient.clear()
      setRevoked(true)
    }
  }, [revoked, session.error])
  if (revoked) return <LoginPage onAuthenticated={sessionChanged} />
  if (session.isPending) {
    return <main className="centered-state" role="status"><LoaderCircle className="spin" size={28} /><p>Проверяем сессию…</p></main>
  }
  if (session.error instanceof ApiError && session.error.status === 401) {
    return <LoginPage onAuthenticated={sessionChanged} />
  }
  if (session.isError) {
    return (
      <main className="centered-state centered-state--error">
        <div className="brand-mark"><span>O</span><span className="brand-mark__dot" /></div>
        <h1>Не удалось открыть рабочее пространство</h1>
        <Notice tone="error">{errorText(session.error)}</Notice>
        <Button icon={<RefreshCw size={16} />} onClick={() => void session.refetch()}>Повторить проверку</Button>
      </main>
    )
  }
  if (!session.data) return <LoginPage onAuthenticated={sessionChanged} />
  if (session.data.user.must_change_password) return <PasswordChangePage onChanged={sessionChanged} />
  const canReadTickets = session.data.permissions.includes('tickets.read.all') || session.data.permissions.includes('tickets.read.own')
  return (
    <AppShell session={session.data} onLoggedOut={sessionChanged}>
      <Routes>
        <Route path="/" element={<WorkspacePage session={session.data} />} />
        <Route path="/account" element={<AccountPage session={session.data} />} />
        <Route path="/tickets" element={canReadTickets ? <TicketListPage session={session.data} /> : <Navigate to="/" replace />} />
        <Route path="/tickets/new" element={session.data.permissions.includes('tickets.create') ? <TicketCreatePage session={session.data} /> : <Navigate to="/" replace />} />
        <Route path="/tickets/:id" element={canReadTickets ? <TicketDetailPage session={session.data} /> : <Navigate to="/" replace />} />
        <Route path="/users" element={session.data.permissions.includes('users.manage') ? <UsersPage /> : <Navigate to="/" replace />} />
        <Route path="/subscribers" element={session.data.permissions.includes('subscribers.read') ? <SubscriberPage session={session.data} /> : <Navigate to="/" replace />} />
        <Route path="/subscribers/:lookupId" element={session.data.permissions.includes('subscribers.read') ? <SubscriberPage session={session.data} /> : <Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  )
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter><AppRoutes /></BrowserRouter>
    </QueryClientProvider>
  )
}
