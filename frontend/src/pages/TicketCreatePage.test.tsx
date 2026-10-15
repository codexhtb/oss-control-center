import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { SubscriberCard, Ticket } from '../features/tickets/types'
import type { MeResponse } from '../shared/api/types'
import { TicketCreatePage } from './TicketCreatePage'

vi.mock('../features/tickets/ticket-api', () => ({ ticketApi: { create: vi.fn(), lookup: vi.fn(), getLookup: vi.fn() } }))

const subscriberDefaults = {
  contacts: [], email: null, subscriber_id: null, field_meta: {},
}
const serviceDefaults = {
  account_id: 'MOCK-GPON-001', tariff_name: null,
  billing_active: null, billing_blocked: null, field_meta: {},
}
const connectionDefaults = {
  onu_id: null, ip_group: null, ip_groups: [], field_meta: {},
}

const session: MeResponse = {
  user: {
    id: '00000000-0000-4000-8000-000000000001', login: 'test-operator',
    display_name: 'Тестовый оператор', role: 'operator', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  },
  permissions: ['tickets.create'],
}

function renderCreate() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/tickets/new']}><Routes><Route path="/tickets/new" element={<TicketCreatePage session={session} />} /><Route path="/tickets/:id" element={<div>Карточка открыта</div>} /></Routes></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(ticketApi.create).mockReset()
  vi.mocked(ticketApi.lookup).mockReset()
})

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
  expect(await screen.findByText('Карточка открыта')).toBeInTheDocument()
})

it('requires explicit service and connection choices for an ambiguous subscriber card', async () => {
  const card: SubscriberCard = {
    lookup_id: '00000000-0000-4000-8000-000000000010', source: 'MOCK',
    fetched_at: '2026-01-01T00:00:00Z', expires_at: '2999-01-01T00:00:00Z', is_expired: false,
    subscriber: { ...subscriberDefaults, billing_account: 'MOCK-GPON-001', full_name: null, organization_name: 'Тестовая организация', phone: null, actual_address: null },
    accounts: [], financial_accounts: [], contracts: [],
    services: [
      { ...serviceDefaults, service_id: 'service-a', name: 'Услуга A' },
      { ...serviceDefaults, service_id: 'service-b', name: 'Услуга B' },
    ],
    connections: [
      { ...connectionDefaults, connection_snapshot_id: '00000000-0000-4000-8000-000000000011', access_type: 'GPON', device: null, port: null, vlan: null, service_id: 'service-a' },
      { ...connectionDefaults, connection_snapshot_id: '00000000-0000-4000-8000-000000000012', access_type: 'GPON', device: null, port: null, vlan: null, service_id: 'service-b' },
    ],
    technical_state: 'UNKNOWN',
  }
  vi.mocked(ticketApi.lookup).mockResolvedValue(card)
  vi.mocked(ticketApi.create).mockResolvedValue({ id: '00000000-0000-4000-8000-000000000002' } as Ticket)
  renderCreate()
  fireEvent.click(screen.getByRole('button', { name: /По абоненту/ }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Лицевой счёт' }), { target: { value: 'MOCK-GPON-001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
  expect(await screen.findByText('Тестовая организация')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: /Описание/ }), { target: { value: 'Проверить доступность' } })
  expect(screen.getByRole('button', { name: 'Создать заявку' })).toBeDisabled()
  fireEvent.change(screen.getByRole('combobox', { name: 'Услуга' }), { target: { value: 'service-b' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Подключение' }), { target: { value: card.connections[1]!.connection_snapshot_id } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать заявку' }))
  await waitFor(() => expect(ticketApi.create).toHaveBeenCalledWith(expect.objectContaining({
    ticket_type: 'SUBSCRIBER_LINKED', lookup_id: card.lookup_id,
    selected_service_id: 'service-b', connection_snapshot_id: card.connections[1]!.connection_snapshot_id,
  })))
})

it('does not show a late lookup result after the account field changed', async () => {
  let finishLookup: (card: SubscriberCard) => void = () => undefined
  vi.mocked(ticketApi.lookup).mockImplementation(() => new Promise((resolve) => { finishLookup = resolve }))
  renderCreate()
  fireEvent.click(screen.getByRole('button', { name: /По абоненту/ }))
  const account = screen.getByRole('textbox', { name: 'Лицевой счёт' })
  fireEvent.change(account, { target: { value: 'MOCK-ETH-001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
  fireEvent.change(account, { target: { value: 'MOCK-GPON-001' } })
  await act(async () => finishLookup({
    lookup_id: '00000000-0000-4000-8000-000000000010', source: 'MOCK',
    fetched_at: '2026-01-01T00:00:00Z', expires_at: '2999-01-01T00:00:00Z', is_expired: false,
    subscriber: { ...subscriberDefaults, billing_account: 'MOCK-ETH-001', full_name: 'Неверная карточка', organization_name: null, phone: null, actual_address: null },
    accounts: [], financial_accounts: [], contracts: [], services: [], connections: [], technical_state: 'UNKNOWN',
  }))
  await waitFor(() => expect(ticketApi.lookup).toHaveBeenCalledWith('MOCK-ETH-001'))
  expect(screen.queryByText('Неверная карточка')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Создать заявку' })).toBeDisabled()
})
