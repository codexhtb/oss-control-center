import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { Ticket } from '../features/tickets/types'
import type { MeResponse } from '../shared/api/types'
import { TicketListPage } from './TicketListPage'

vi.mock('../features/tickets/ticket-api', () => ({
  ticketApi: { list: vi.fn(), engineers: vi.fn() },
}))

const session: MeResponse = {
  user: {
    id: '00000000-0000-4000-8000-000000000001', login: 'test-operator',
    display_name: 'Тестовый оператор', role: 'operator', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  },
  permissions: ['ticket.read', 'ticket.create'],
}

const ticket: Ticket = {
  id: '00000000-0000-4000-8000-000000000002', number: 42, ticket_type: 'MANUAL',
  subject: 'Синтетическая задача', description: 'Тестовое описание', status: 'OPEN',
  priority: 'HIGH', account_no: null, subscriber_snapshot_id: null,
  connection_snapshot_id: null, selected_service_id: null, created_by: session.user.id,
  assigned_engineer_id: null, closed_by: null, closed_decision_id: null, closed_at: null,
  version: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><TicketListPage session={session} /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(ticketApi.list).mockReset()
  vi.mocked(ticketApi.engineers).mockReset()
})

it('renders server data and sends unassigned and type filters to the API', async () => {
  vi.mocked(ticketApi.list).mockResolvedValue({ items: [ticket], page: 1, size: 20, total: 1 })
  renderList()
  expect(await screen.findByText('Синтетическая задача')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Синтетическая задача/ })).toHaveAttribute('href', `/tickets/${ticket.id}`)
  fireEvent.click(screen.getByRole('checkbox', { name: 'Без исполнителя' }))
  await waitFor(() => expect(ticketApi.list).toHaveBeenCalledWith(expect.objectContaining({ unassigned: true }), expect.anything()))
  fireEvent.change(screen.getByRole('combobox', { name: 'Тип' }), { target: { value: 'MANUAL' } })
  await waitFor(() => expect(ticketApi.list).toHaveBeenCalledWith(expect.objectContaining({ unassigned: true, ticket_type: 'MANUAL' }), expect.anything()))
})

it('shows the empty state from an empty server page', async () => {
  vi.mocked(ticketApi.list).mockResolvedValue({ items: [], page: 1, size: 20, total: 0 })
  renderList()
  expect(await screen.findByText('Заявок по этим условиям нет')).toBeInTheDocument()
})
