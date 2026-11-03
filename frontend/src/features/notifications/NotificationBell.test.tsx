import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import type { MeResponse } from '../../shared/api/types'
import { NotificationBell } from './NotificationBell'
import { notificationApi } from './notification-api'

vi.mock('./notification-api', () => ({ notificationApi: { list: vi.fn(), markRead: vi.fn() } }))

const engineer: MeResponse = {
  user: {
    id: '00000000-0000-4000-8000-000000000001', login: 'engineer',
    display_name: 'Инженер', role: 'engineer', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  },
  permissions: ['tickets.read.all', 'tickets.field_work'],
}

function mount(session = engineer) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/']}>
    <Routes><Route path="/" element={<NotificationBell session={session} />} />
      <Route path="/tickets/:id" element={<p>Заявка открыта</p>} /></Routes>
  </MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(notificationApi.list).mockReset()
  vi.mocked(notificationApi.markRead).mockReset()
  vi.mocked(notificationApi.list).mockResolvedValue({
    items: [{ id: 'notice-1', ticket_id: 'ticket-1', ticket_number: 66,
      sender_name: 'Монтажник Тест', reported_at: '2026-11-03T10:00:00Z',
      read_at: null, awaiting_verification: true }],
    page: 1, size: 20, total: 1, unread_count: 1,
  })
  vi.mocked(notificationApi.markRead).mockResolvedValue(undefined)
})

it('shows persisted report and marks it read before opening its ticket', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: 'Уведомления, непрочитанных: 1' }))
  expect(screen.getByText('Монтажник Тест', { exact: false })).toBeInTheDocument()
  expect(screen.getByLabelText('Ожидает подтверждения')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Заявка #66/ }))
  await waitFor(() => expect(notificationApi.markRead).toHaveBeenCalledWith('notice-1'))
  expect(await screen.findByText('Заявка открыта')).toBeInTheDocument()
})

it('does not fetch or show engineer reports to an operator', () => {
  mount({ ...engineer, user: { ...engineer.user, role: 'operator' } })
  expect(screen.queryByRole('button', { name: /Уведомления/ })).not.toBeInTheDocument()
  expect(notificationApi.list).not.toHaveBeenCalled()
})
