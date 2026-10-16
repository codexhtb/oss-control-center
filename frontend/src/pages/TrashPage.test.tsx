import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { Ticket } from '../features/tickets/types'
import type { MeResponse } from '../shared/api/types'
import { TrashPage } from './TrashPage'

vi.mock('../features/tickets/ticket-api', () => ({
  ticketApi: { trash: vi.fn(), restore: vi.fn(), purge: vi.fn() },
}))

const ticket: Ticket = {
  id: '00000000-0000-4000-8000-000000000050', number: 51,
  ticket_type: 'MANUAL', subject: 'Синтетическая заявка', description: 'Тест',
  status: 'OPEN', priority: 'NORMAL', account_no: null,
  subscriber_snapshot_id: null, connection_snapshot_id: null, selected_service_id: null,
  created_by: '00000000-0000-4000-8000-000000000051', assigned_engineer_id: null,
  closed_by: null, closed_decision_id: null, closed_at: null,
  resolved_by: null, resolved_decision_id: null, resolved_at: null,
  deleted_at: '2026-01-01T00:00:00Z', deleted_by: '00000000-0000-4000-8000-000000000051',
  delete_reason: null, version: 3,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
}
const session: MeResponse = {
  user: { id: ticket.created_by, login: 'admin', display_name: 'Администратор',
    role: 'admin', is_active: true, created_at: ticket.created_at, updated_at: ticket.updated_at },
  permissions: ['tickets.read.all', 'tickets.restore', 'tickets.purge'],
}

function renderTrash() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><TrashPage session={session} /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(ticketApi.trash).mockReset().mockResolvedValue({ items: [ticket], page: 1, size: 20, total: 1 })
  vi.mocked(ticketApi.restore).mockReset().mockResolvedValue({ ...ticket, deleted_at: null })
  vi.mocked(ticketApi.purge).mockReset().mockResolvedValue(undefined)
})

it('restores a deleted ticket using its current version', async () => {
  renderTrash()
  fireEvent.click(await screen.findByRole('button', { name: 'Восстановить' }))
  await waitFor(() => expect(ticketApi.restore).toHaveBeenCalledWith(ticket.id, 3))
})

it('requires the number and a second confirmation before purge', async () => {
  renderTrash()
  fireEvent.click(await screen.findByRole('button', { name: 'Окончательно удалить' }))
  expect(screen.getByText(/Заявка №51/)).toBeInTheDocument()
  const continueButton = screen.getByRole('button', { name: 'Продолжить' })
  expect(continueButton).toBeDisabled()
  fireEvent.change(screen.getByRole('textbox', { name: 'Введите номер заявки для подтверждения' }), { target: { value: '51' } })
  fireEvent.click(continueButton)
  expect(ticketApi.purge).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Да, удалить без возможности восстановления' }))
  await waitFor(() => expect(ticketApi.purge).toHaveBeenCalledWith(ticket.id, 3, 51))
})
