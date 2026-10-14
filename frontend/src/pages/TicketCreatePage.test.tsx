import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { Ticket } from '../features/tickets/types'
import type { MeResponse } from '../shared/api/types'
import { TicketCreatePage } from './TicketCreatePage'

vi.mock('../features/tickets/ticket-api', () => ({ ticketApi: { create: vi.fn() } }))

const session: MeResponse = {
  user: {
    id: '00000000-0000-4000-8000-000000000001', login: 'test-operator',
    display_name: 'Тестовый оператор', role: 'operator', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  },
  permissions: ['ticket.create'],
}

function renderCreate() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/tickets/new']}><Routes><Route path="/tickets/new" element={<TicketCreatePage session={session} />} /><Route path="/tickets" element={<div>Реестр открыт</div>} /></Routes></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => vi.mocked(ticketApi.create).mockReset())

it.each([
  ['MANUAL', 'Ручная'],
  ['INTERNAL', 'Внутренняя'],
] as const)('creates %s through the real form state and idempotency payload', async (kind, label) => {
  vi.mocked(ticketApi.create).mockResolvedValue({ id: '00000000-0000-4000-8000-000000000002' } as Ticket)
  renderCreate()
  fireEvent.click(screen.getByRole('button', { name: new RegExp(label) }))
  fireEvent.change(screen.getByRole('textbox', { name: /Тема/ }), { target: { value: 'Тестовая тема' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Описание/ }), { target: { value: 'Тестовое описание' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать заявку' }))
  await waitFor(() => expect(ticketApi.create).toHaveBeenCalledWith(expect.objectContaining({
    ticket_type: kind, subject: 'Тестовая тема', description: 'Тестовое описание',
    client_request_id: expect.any(String),
  })))
  expect(await screen.findByText('Реестр открыт')).toBeInTheDocument()
})
