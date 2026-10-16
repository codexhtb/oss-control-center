import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
  permissions: ['tickets.read.all', 'tickets.create'],
}

const ticket: Ticket = {
  id: '00000000-0000-4000-8000-000000000002', number: 42, ticket_type: 'MANUAL',
  subject: 'Синтетическая задача', description: 'Тестовое описание', status: 'OPEN',
  priority: 'HIGH', account_no: null, subscriber_snapshot_id: null,
  connection_snapshot_id: null, selected_service_id: null, created_by: session.user.id,
  assigned_engineer_id: null, closed_by: null, closed_decision_id: null, closed_at: null,
  deleted_at: null, deleted_by: null, delete_reason: null,
  version: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
}

function renderList(initial = '/tickets') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[initial]}><TicketListPage session={session} /></MemoryRouter></QueryClientProvider>)
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

it('shows snapshot subscriber identity and account alongside linked tickets', async () => {
  const linked: Ticket = {
    ...ticket,
    id: '00000000-0000-4000-8000-000000000005',
    ticket_type: 'SUBSCRIBER_LINKED',
    subscriber_name: 'Синтетический абонент',
    account_no: '0001499',
    subject: 'Нет интернета',
  }
  vi.mocked(ticketApi.list).mockResolvedValue({ items: [linked, ticket], page: 1, size: 20, total: 2 })
  renderList()
  const subscriberRow = (await screen.findByText('Синтетический абонент')).closest('tr')
  expect(subscriberRow).not.toBeNull()
  expect(within(subscriberRow!).getByText('ЛС 0001499')).toBeInTheDocument()
  expect(within(subscriberRow!).getByRole('link', { name: /Нет интернета/ })).toHaveAttribute('href', `/tickets/${linked.id}`)
  const manualRow = screen.getByText('Синтетическая задача').closest('tr')
  expect(manualRow).not.toBeNull()
  expect(within(manualRow!).queryByText(/ЛС /)).not.toBeInTheDocument()
})

it('shows organization and a neutral label when a linked snapshot has no name', async () => {
  vi.mocked(ticketApi.list).mockResolvedValue({
    items: [
      { ...ticket, id: '00000000-0000-4000-8000-000000000003', ticket_type: 'SUBSCRIBER_LINKED', subscriber_name: 'Синтетическая организация', account_no: '001' },
      { ...ticket, id: '00000000-0000-4000-8000-000000000004', ticket_type: 'SUBSCRIBER_LINKED', subscriber_name: null, account_no: '002' },
    ],
    page: 1, size: 20, total: 2,
  })
  renderList()
  expect(await screen.findByText('Синтетическая организация')).toBeInTheDocument()
  expect(screen.getByText('Абонент не указан')).toBeInTheDocument()
  expect(screen.getByText('ЛС 002')).toBeInTheDocument()
})

it('does not fabricate an account number when linked data is incomplete', async () => {
  vi.mocked(ticketApi.list).mockResolvedValue({
    items: [{ ...ticket, ticket_type: 'SUBSCRIBER_LINKED', subscriber_name: null, account_no: null }],
    page: 1, size: 20, total: 1,
  })
  renderList()
  expect(await screen.findByText('Абонент не указан')).toBeInTheDocument()
  expect(screen.queryByText(/ЛС /)).not.toBeInTheDocument()
})

it('debounces the universal search and hides stale results while the query changes', async () => {
  vi.mocked(ticketApi.list).mockImplementation(async (filters) => ({
    items: filters.q ? [] : [ticket], page: 1, size: 20, total: filters.q ? 0 : 1,
  }))
  renderList()
  expect(await screen.findByText('Синтетическая задача')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('searchbox', { name: 'Поиск по заявкам' }), { target: { value: '1499' } })
  expect(screen.getByText('Обновляем поиск…')).toBeInTheDocument()
  expect(screen.queryByText('Синтетическая задача')).not.toBeInTheDocument()
  await waitFor(() => expect(ticketApi.list).toHaveBeenCalledWith(expect.objectContaining({ q: '1499', page: 1 }), expect.anything()))
  expect(await screen.findByText('Заявок по этим условиям нет')).toBeInTheDocument()
})

it('combines saved URL filters, local calendar dates and reset', async () => {
  vi.mocked(ticketApi.list).mockResolvedValue({ items: [ticket], page: 1, size: 20, total: 1 })
  renderList('/tickets?q=wire-down&category=Нет+интернета&active_only=true&ticket_type=MANUAL')
  await waitFor(() => expect(ticketApi.list).toHaveBeenCalledWith(expect.objectContaining({
    q: 'wire-down', category: 'Нет интернета', active_only: true, ticket_type: 'MANUAL',
  }), expect.anything()))
  expect(screen.getByRole('searchbox', { name: 'Поиск по заявкам' })).toHaveValue('wire-down')
  fireEvent.change(screen.getByLabelText('Создана от'), { target: { value: '2026-10-10' } })
  fireEvent.change(screen.getByLabelText('Создана до'), { target: { value: '2026-10-16' } })
  await waitFor(() => expect(ticketApi.list).toHaveBeenCalledWith(expect.objectContaining({
    created_from: new Date(2026, 9, 10).toISOString(),
    created_before: new Date(2026, 9, 17).toISOString(),
  }), expect.anything()))
  fireEvent.click(screen.getByRole('button', { name: 'Сбросить фильтры' }))
  await waitFor(() => expect(vi.mocked(ticketApi.list).mock.lastCall?.[0]).toEqual({ page: 1, size: 20 }))
  expect(screen.getByRole('searchbox', { name: 'Поиск по заявкам' })).toHaveValue('')
})
