import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { Page, TicketDetail } from '../features/tickets/types'
import type { MeResponse } from '../shared/api/types'
import { TicketDetailPage } from './TicketDetailPage'

vi.mock('../features/tickets/ticket-api', () => ({ ticketApi: {
  get: vi.fn(), comments: vi.fn(), history: vi.fn(), diagnostics: vi.fn(),
  decisions: vi.fn(), engineers: vi.fn(), start: vi.fn(), addComment: vi.fn(),
} }))

const ticketId = '00000000-0000-4000-8000-000000000010'
const base: TicketDetail = {
  id: ticketId, number: 73, ticket_type: 'INTERNAL', subject: 'Проверка OSS',
  description: 'Синтетическое описание', status: 'OPEN', priority: 'NORMAL',
  account_no: null, subscriber_snapshot_id: null, connection_snapshot_id: null,
  selected_service_id: null, created_by: '00000000-0000-4000-8000-000000000001',
  assigned_engineer_id: null, closed_by: null, closed_decision_id: null,
  closed_at: null, version: 3, created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z', subscriber: null,
  selected_connection: null, technical_state: 'UNKNOWN',
}
const empty: Page<never> = { items: [], page: 1, size: 20, total: 0 }

function session(role: 'operator' | 'engineer'): MeResponse {
  return {
    user: { id: '00000000-0000-4000-8000-000000000001', login: 'test-user',
      display_name: 'Тестовый пользователь', role, is_active: true,
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
    permissions: role === 'engineer'
      ? ['ticket.read', 'ticket.comment', 'ticket.start', 'ticket.assign', 'ticket.manual_diagnostic', 'ticket.decision', 'ticket.close']
      : ['ticket.read', 'ticket.comment'],
  }
}

function renderDetail(role: 'operator' | 'engineer') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/tickets/${ticketId}`]}><Routes><Route path="/tickets/:id" element={<TicketDetailPage session={session(role)} />} /></Routes></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  for (const fn of [ticketApi.get, ticketApi.comments, ticketApi.history, ticketApi.diagnostics, ticketApi.decisions, ticketApi.engineers, ticketApi.start, ticketApi.addComment]) vi.mocked(fn).mockReset()
  vi.mocked(ticketApi.get).mockResolvedValue(base)
  vi.mocked(ticketApi.comments).mockResolvedValue(empty)
  vi.mocked(ticketApi.history).mockResolvedValue(empty)
  vi.mocked(ticketApi.diagnostics).mockResolvedValue(empty)
  vi.mocked(ticketApi.decisions).mockResolvedValue(empty)
  vi.mocked(ticketApi.engineers).mockResolvedValue({ items: [], page: 1, size: 100, total: 0 })
})

it('shows the saved ticket and sends the current version when an engineer starts work', async () => {
  vi.mocked(ticketApi.start).mockResolvedValue({ ...base, status: 'IN_PROGRESS', version: 4 })
  renderDetail('engineer')
  expect(await screen.findByRole('heading', { name: 'Проверка OSS' })).toBeInTheDocument()
  expect(screen.getByText(/Заявка не связана с абонентом/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Взять в работу/ }))
  await waitFor(() => expect(ticketApi.start).toHaveBeenCalledWith(ticketId, 3))
})

it('keeps operator actions within comment permission', async () => {
  vi.mocked(ticketApi.addComment).mockResolvedValue({ id: '00000000-0000-4000-8000-000000000011', ticket_id: ticketId, author_id: session('operator').user.id, body: 'Уточнение', created_at: '2026-01-01T00:00:00Z' })
  renderDetail('operator')
  expect(await screen.findByRole('heading', { name: 'Проверка OSS' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Взять в работу/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Назначить/ })).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'Новый комментарий' }), { target: { value: 'Уточнение' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить комментарий' }))
  await waitFor(() => expect(ticketApi.addComment).toHaveBeenCalledWith(ticketId, 'Уточнение'))
})
