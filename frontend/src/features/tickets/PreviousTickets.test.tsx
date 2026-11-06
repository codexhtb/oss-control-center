import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import type { PreviousTicket } from './types'
import { ticketApi } from './ticket-api'
import { PreviousTickets } from './PreviousTickets'

vi.mock('./ticket-api', () => ({ ticketApi: { previousTickets: vi.fn() } }))

const tickets: PreviousTicket[] = Array.from({ length: 12 }, (_, index) => ({
  id: `previous-${12 - index}`, number: 12 - index, subject: `Тема ${12 - index}`,
  status: index === 0 ? 'OPEN' : 'CLOSED', assigned_engineer_name: index === 0 ? 'Инженер' : null,
  created_at: `2026-01-${String(12 - index).padStart(2, '0')}T10:00:00Z`,
}))

function renderHistory() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/']}><Routes>
    <Route path="/" element={<PreviousTickets ticketId="current" snapshotId="snapshot" />} />
    <Route path="/tickets/:id" element={<p>Предыдущее обращение открыто</p>} />
  </Routes></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(ticketApi.previousTickets).mockReset()
  vi.mocked(ticketApi.previousTickets).mockImplementation(async (_id, page, size) => ({
    items: tickets.slice((page - 1) * size, page * size), page, size, total: tickets.length,
  }))
})

it('shows three recent tickets, highlights open work, expands and paginates', async () => {
  renderHistory()
  expect(await screen.findByText('Предыдущих обращений: 12')).toBeInTheDocument()
  expect(screen.getAllByRole('link', { name: /^Открыть заявку #/ })).toHaveLength(3)
  expect(screen.getByRole('link', { name: 'Открыть заявку #12' }).closest('tr')).toHaveClass('previous-tickets__row--open')
  expect(screen.getByRole('link', { name: 'Открыть заявку #12' })).toHaveAttribute('href', '/tickets/previous-12')
  fireEvent.click(screen.getByRole('button', { name: 'Все обращения' }))
  await waitFor(() => expect(screen.getAllByRole('link', { name: /^Открыть заявку #/ })).toHaveLength(10))
  fireEvent.click(screen.getByRole('button', { name: 'Далее' }))
  await waitFor(() => expect(screen.getAllByRole('link', { name: /^Открыть заявку #/ })).toHaveLength(2))
  fireEvent.click(screen.getByRole('link', { name: 'Открыть заявку #2' }))
  expect(screen.getByText('Предыдущее обращение открыто')).toBeInTheDocument()
})

it('shows empty and error states without inventing a history', async () => {
  vi.mocked(ticketApi.previousTickets).mockResolvedValueOnce({ items: [], page: 1, size: 3, total: 0 })
  const empty = renderHistory()
  expect(await screen.findByText('Предыдущих обращений нет.')).toBeInTheDocument()
  empty.unmount()
  vi.mocked(ticketApi.previousTickets).mockRejectedValueOnce(new Error('history unavailable'))
  renderHistory()
  expect(await screen.findByText(/Не удалось загрузить обращения/)).toBeInTheDocument()
})
