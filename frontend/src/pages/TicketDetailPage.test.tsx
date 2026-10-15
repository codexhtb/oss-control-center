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
  lookup: vi.fn(), checkTicket: vi.fn(), confirmCheck: vi.fn(), ticketOnu: vi.fn(),
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
      ? ['subscribers.read', 'tickets.read.all', 'tickets.comment', 'tickets.start', 'tickets.assign', 'tickets.diagnose', 'diagnostics.run', 'tickets.resolve', 'tickets.close']
      : ['subscribers.read', 'tickets.read.all', 'tickets.comment'],
  }
}

function renderDetail(role: 'operator' | 'engineer') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/tickets/${ticketId}`]}><Routes><Route path="/tickets/:id" element={<TicketDetailPage session={session(role)} />} /><Route path="/subscribers/:lookupId" element={<div>Карточка абонента открыта</div>} /></Routes></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  for (const fn of [ticketApi.get, ticketApi.comments, ticketApi.history, ticketApi.diagnostics, ticketApi.decisions, ticketApi.engineers, ticketApi.start, ticketApi.addComment, ticketApi.lookup, ticketApi.checkTicket, ticketApi.confirmCheck, ticketApi.ticketOnu]) vi.mocked(fn).mockReset()
  vi.mocked(ticketApi.get).mockResolvedValue(base)
  vi.mocked(ticketApi.comments).mockResolvedValue(empty)
  vi.mocked(ticketApi.history).mockResolvedValue(empty)
  vi.mocked(ticketApi.diagnostics).mockResolvedValue(empty)
  vi.mocked(ticketApi.decisions).mockResolvedValue(empty)
  vi.mocked(ticketApi.engineers).mockResolvedValue({ items: [], page: 1, size: 100, total: 0 })
  vi.mocked(ticketApi.ticketOnu).mockResolvedValue({
    source: 'BDCOM', retrieved_at: base.created_at,
    availability: 'UNAVAILABLE', association: 'UNCONFIRMED', candidates: [],
    limitations: ['onu_source_not_configured'], time_basis: 'SOURCE_LOCAL_UNVERIFIED',
  })
})

it('shows the saved ticket and sends the current version when an engineer starts work', async () => {
  vi.mocked(ticketApi.start).mockResolvedValue({ ...base, status: 'IN_PROGRESS', version: 4 })
  renderDetail('engineer')
  expect(await screen.findByRole('heading', { name: 'Проверка OSS' })).toBeInTheDocument()
  expect(screen.getByText(/Заявка без привязки к абоненту/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Взять в работу/ }))
  await waitFor(() => expect(ticketApi.start).toHaveBeenCalledWith(ticketId, 3))
})

it('shows only confirmed connection fields and opens a fresh subscriber lookup', async () => {
  const accountNo = 'SYNTHETIC-ACCOUNT'
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base, ticket_type: 'SUBSCRIBER_LINKED', account_no: accountNo,
    subscriber: {
      full_name: 'Синтетический абонент', organization_name: null, phone: null,
      actual_address: null, fetched_at: base.created_at, source: 'MOCK',
      services: [], connections: [],
    },
    selected_connection: {
      id: '00000000-0000-4000-8000-000000000020', access_type: 'UNKNOWN',
      device: 'synthetic-device', port: 'synthetic-port', vlan: '100', onu_id: null,
      address: null, service_id: null, ip_group: null, ip_groups: [], field_meta: {},
    },
  })
  vi.mocked(ticketApi.lookup).mockResolvedValue({ lookup_id: '00000000-0000-4000-8000-000000000030' } as never)
  renderDetail('operator')
  expect(await screen.findByText('Устройство: synthetic-device')).toBeInTheDocument()
  expect(screen.getByText(`ЛС ${accountNo}`)).toBeInTheDocument()
  expect(screen.getByText('Порт: synthetic-port')).toBeInTheDocument()
  expect(screen.getByText('VLAN: 100')).toBeInTheDocument()
  expect(screen.queryByText(/Технология: UNKNOWN|ONU: UNKNOWN|Техническое состояние: UNKNOWN/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Открыть карточку абонента/ }))
  await waitFor(() => expect(ticketApi.lookup).toHaveBeenCalledWith(accountNo))
  expect(await screen.findByText('Карточка абонента открыта')).toBeInTheDocument()
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

it('shows an engineer a monitoring result and requires a separate confirm action', async () => {
  const linked: TicketDetail = {
    ...base, ticket_type: 'SUBSCRIBER_LINKED', status: 'IN_PROGRESS',
    account_no: 'synthetic-account',
    subscriber_snapshot_id: '00000000-0000-4000-8000-000000000040',
    connection_snapshot_id: '00000000-0000-4000-8000-000000000041',
    subscriber: { full_name: 'Синтетический абонент', organization_name: null, phone: null,
      actual_address: null, fetched_at: base.created_at, source: 'MOCK', services: [], connections: [] },
  }
  vi.mocked(ticketApi.get).mockResolvedValue(linked)
  const report = {
    connection_snapshot_id: linked.connection_snapshot_id!, checked_at: base.created_at,
    device_match: 'KNOWN' as const, interface_match: 'UNKNOWN' as const,
    observations: [{ source: 'LIBRENMS' as const, measured_object: 'device:42', result: 'UP',
      observed_at: base.created_at, retrieved_at: base.created_at, quality: 'KNOWN' as const }],
    limitations: ['interface_mapping_unconfirmed'],
  }
  vi.mocked(ticketApi.checkTicket).mockResolvedValue({ check_id: '00000000-0000-4000-8000-000000000042', expires_at: base.created_at, report })
  vi.mocked(ticketApi.confirmCheck).mockResolvedValue({ id: '00000000-0000-4000-8000-000000000043' } as never)
  renderDetail('engineer')
  expect(await screen.findByRole('heading', { name: 'Проверка OSS' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Проверить подключение' }))
  await waitFor(() => expect(ticketApi.checkTicket).toHaveBeenCalledWith(ticketId, 3))
  expect(await screen.findByText('Соответствие порта NetUP и интерфейса не подтверждено.')).toBeInTheDocument()
  expect(ticketApi.confirmCheck).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить и сохранить в истории' }))
  await waitFor(() => expect(ticketApi.confirmCheck).toHaveBeenCalledWith(ticketId, '00000000-0000-4000-8000-000000000042', 3))
})
