import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { MeResponse } from '../shared/api/types'
import { apiRequest } from '../shared/api/client'
import { WorkspacePage } from './WorkspacePage'

vi.mock('../features/tickets/ticket-api', () => ({ ticketApi: { list: vi.fn(), recentEvents: vi.fn() } }))
vi.mock('../shared/api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../shared/api/client')>()
  return { ...original, apiRequest: vi.fn() }
})

const user: MeResponse = {
  user: { id: '00000000-0000-4000-8000-000000000001', login: 'synthetic',
    display_name: 'Тестовый оператор', role: 'operator', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  permissions: ['tickets.read.all'],
}

function renderWorkspace(session: MeResponse) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><WorkspacePage session={session} /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(ticketApi.list).mockReset()
  vi.mocked(ticketApi.recentEvents).mockReset()
  vi.mocked(apiRequest).mockReset()
  vi.mocked(apiRequest).mockResolvedValue({ status: 'ready', dependencies: { postgres: 'up' } })
})

it('takes dashboard totals and recent events from ServiceDesk API', async () => {
  vi.mocked(ticketApi.list).mockImplementation(async (filters) => ({
    items: [], page: 1, size: filters.size, total: filters.unassigned ? 2 : filters.status === 'OPEN' ? 4 : 1,
  }))
  vi.mocked(ticketApi.recentEvents).mockResolvedValue({ items: [{
    id: '00000000-0000-4000-8000-000000000002', ticket_id: '00000000-0000-4000-8000-000000000003',
    ticket_number: 12, event_type: 'ticket_created', actor_id: null, ticket_version: 1,
    occurred_at: '2026-01-01T00:00:00Z',
  }], page: 1, size: 8, total: 1 })
  renderWorkspace(user)
  expect(await screen.findByText('Заявка создана')).toBeInTheDocument()
  await waitFor(() => expect(document.querySelector('a.metric-card--accent')).toHaveTextContent('2'))
  expect(ticketApi.list).toHaveBeenCalledWith(expect.objectContaining({ unassigned: true }), expect.anything())
})

it('does not request ServiceDesk information for technical admin', async () => {
  renderWorkspace({ ...user, user: { ...user.user, role: 'admin' }, permissions: ['users.manage'] })
  expect(await screen.findByText(/Для просмотра заявок необходимо разрешение/)).toBeInTheDocument()
  expect(ticketApi.list).not.toHaveBeenCalled()
  expect(ticketApi.recentEvents).not.toHaveBeenCalled()
})
