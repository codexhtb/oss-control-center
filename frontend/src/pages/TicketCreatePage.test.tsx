import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { SubscriberCard, Ticket } from '../features/tickets/types'
import { ApiError } from '../shared/api/client'
import type { MeResponse } from '../shared/api/types'
import { TicketCreatePage } from './TicketCreatePage'

vi.mock('../features/tickets/ticket-api', () => ({ ticketApi: { create: vi.fn(), lookup: vi.fn(), getLookup: vi.fn(), activeSubscriberTickets: vi.fn(), categories: vi.fn() } }))

const categoryId = '00000000-0000-4000-8000-000000000099'

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
  vi.mocked(ticketApi.categories).mockReset().mockResolvedValue([{ id: categoryId, name: 'Нет интернета', sort_order: 10, is_active: true, is_other: false, version: 1 }])
  vi.mocked(ticketApi.activeSubscriberTickets).mockReset().mockResolvedValue({
    items: [], page: 1, size: 20, total_visible: 0, has_hidden_active: false,
  })
})

it.each([
  ['MANUAL', 'Ручная'],
  ['INTERNAL', 'Внутренняя'],
] as const)('creates %s through the real form state and idempotency payload', async (kind, label) => {
  vi.mocked(ticketApi.create).mockResolvedValue({ id: '00000000-0000-4000-8000-000000000002' } as Ticket)
  renderCreate()
  fireEvent.click(screen.getByRole('button', { name: new RegExp(label) }))
  if (kind === 'MANUAL') {
    fireEvent.change(await screen.findByRole('combobox', { name: 'Тема обращения' }), { target: { value: '__custom__' } })
    fireEvent.change(screen.getByRole('textbox', { name: /Контактный номер/ }), { target: { value: '+7 900 111 22 33' } })
    fireEvent.change(screen.getByRole('textbox', { name: /Город/ }), { target: { value: 'Москва' } })
  }
  fireEvent.change(screen.getByRole('textbox', { name: /тема/i }), { target: { value: 'Тестовая тема' } })
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
  fireEvent.change(await screen.findByRole('combobox', { name: 'Тема обращения' }), { target: { value: categoryId } })
  fireEvent.change(screen.getByRole('textbox', { name: /Другой номер/ }), { target: { value: '+7 900 111 22 33' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Город/ }), { target: { value: 'Москва' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Описание проблемы/ }), { target: { value: 'Не работает связь' } })
  expect(screen.getByRole('button', { name: 'Создать заявку' })).toBeDisabled()
  fireEvent.change(screen.getByRole('combobox', { name: 'Услуга' }), { target: { value: 'service-b' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Подключение' }), { target: { value: card.connections[1]!.connection_snapshot_id } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать заявку' }))
  await waitFor(() => expect(ticketApi.create).toHaveBeenCalledWith(expect.objectContaining({
    ticket_type: 'SUBSCRIBER_LINKED', lookup_id: card.lookup_id,
    selected_service_id: 'service-b', connection_snapshot_id: card.connections[1]!.connection_snapshot_id, category_id: categoryId,
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

it('shows active subscriber tickets and requires a different-problem reason', async () => {
  const card: SubscriberCard = {
    lookup_id: '00000000-0000-4000-8000-000000000010', source: 'MOCK',
    fetched_at: '2026-01-01T00:00:00Z', expires_at: '2999-01-01T00:00:00Z', is_expired: false,
    subscriber: { ...subscriberDefaults, billing_account: 'MOCK-ETH-001', full_name: 'Тестовый абонент', organization_name: null, phone: null, actual_address: null },
    accounts: [], financial_accounts: [], contracts: [], services: [], connections: [], technical_state: 'UNKNOWN',
  }
  vi.mocked(ticketApi.lookup).mockResolvedValue(card)
  vi.mocked(ticketApi.activeSubscriberTickets).mockResolvedValue({
    items: [{
      id: '00000000-0000-4000-8000-000000000021', number: 21, subject: 'Нет доступа',
      status: 'IN_PROGRESS', created_at: '2026-01-01T00:00:00Z',
      assigned_engineer_name: 'Инженер', description: 'Первая проблема',
    }], page: 1, size: 20, total_visible: 1, has_hidden_active: false,
  })
  vi.mocked(ticketApi.create).mockResolvedValue({ id: '00000000-0000-4000-8000-000000000022' } as Ticket)
  renderCreate()
  fireEvent.click(screen.getByRole('button', { name: /По абоненту/ }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Лицевой счёт' }), { target: { value: 'MOCK-ETH-001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
  expect(await screen.findByText('У абонента уже есть открытые обращения')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Открыть существующую заявку/ })).toHaveAttribute('href', '/tickets/00000000-0000-4000-8000-000000000021')
  fireEvent.change(await screen.findByRole('combobox', { name: 'Тема обращения' }), { target: { value: categoryId } })
  fireEvent.change(screen.getByRole('textbox', { name: /Другой номер/ }), { target: { value: '+7 900 111 22 33' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Город/ }), { target: { value: 'Москва' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Описание проблемы/ }), { target: { value: 'Другая проблема со связью' } })
  expect(screen.getByRole('button', { name: 'Создать заявку' })).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: /Другая проблема/ }))
  expect(screen.getByRole('button', { name: 'Создать заявку' })).toBeDisabled()
  fireEvent.change(screen.getByRole('textbox', { name: /Почему требуется новая заявка/ }), { target: { value: 'Отдельный симптом' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать заявку' }))
  await waitFor(() => expect(ticketApi.create).toHaveBeenCalledWith(expect.objectContaining({ duplicate_reason: 'Отдельный симптом' })))
})

it('does not disclose a restricted active ticket in the warning', async () => {
  vi.mocked(ticketApi.lookup).mockResolvedValue({
    lookup_id: '00000000-0000-4000-8000-000000000010', source: 'MOCK',
    fetched_at: '2026-01-01T00:00:00Z', expires_at: '2999-01-01T00:00:00Z', is_expired: false,
    subscriber: { ...subscriberDefaults, billing_account: 'MOCK-ETH-001', full_name: null, organization_name: null, phone: null, actual_address: null },
    accounts: [], financial_accounts: [], contracts: [], services: [], connections: [], technical_state: 'UNKNOWN',
  })
  vi.mocked(ticketApi.activeSubscriberTickets).mockResolvedValue({
    items: [], page: 1, size: 20, total_visible: 0, has_hidden_active: true,
  })
  renderCreate()
  fireEvent.click(screen.getByRole('button', { name: /По абоненту/ }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Лицевой счёт' }), { target: { value: 'MOCK-ETH-001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
  expect(await screen.findByText(/Есть обращение, содержание которого вам недоступно/)).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /Открыть существующую заявку/ })).not.toBeInTheDocument()
})

it('does not create a linked ticket when billing is unavailable', async () => {
  vi.mocked(ticketApi.lookup).mockRejectedValue(new ApiError(503, 'billing_unavailable'))
  renderCreate()
  fireEvent.click(screen.getByRole('button', { name: /По абоненту/ }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Лицевой счёт' }), { target: { value: 'MOCK-ETH-001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
  expect(await screen.findByText(/Данные абонента не подтверждены/)).toBeInTheDocument()
  expect(ticketApi.activeSubscriberTickets).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Создать заявку' })).toBeDisabled()
})

it('uses a selected NetUP contact and allows a separate city', async () => {
  const card: SubscriberCard = {
    lookup_id: '00000000-0000-4000-8000-000000000010', source: 'MOCK',
    fetched_at: '2026-01-01T00:00:00Z', expires_at: '2999-01-01T00:00:00Z', is_expired: false,
    subscriber: { ...subscriberDefaults, billing_account: 'MOCK-ETH-001', full_name: 'Тестовый абонент', organization_name: null, phone: '+7 900 000 00 01', actual_address: null },
    accounts: [], financial_accounts: [], contracts: [],
    services: [{ ...serviceDefaults, account_id: null, service_id: 'service-eth', name: 'Synthetic Ethernet' }],
    connections: [{ ...connectionDefaults, connection_snapshot_id: '00000000-0000-4000-8000-000000000011', access_type: 'ETHERNET', device: null, port: null, vlan: null, service_id: 'service-eth' }],
    technical_state: 'UNKNOWN',
  }
  vi.mocked(ticketApi.lookup).mockResolvedValue(card)
  vi.mocked(ticketApi.create).mockResolvedValue({ id: '00000000-0000-4000-8000-000000000002' } as Ticket)
  renderCreate()
  fireEvent.change(screen.getByRole('textbox', { name: 'Лицевой счёт' }), { target: { value: 'MOCK-ETH-001' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
  expect(await screen.findByText('Тестовый абонент')).toBeInTheDocument()
  fireEvent.change(await screen.findByRole('combobox', { name: 'Тема обращения' }), { target: { value: categoryId } })
  fireEvent.change(screen.getByRole('textbox', { name: /Город/ }), { target: { value: 'Москва' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Описание проблемы/ }), { target: { value: 'Нет доступа' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать заявку' }))
  await waitFor(() => expect(ticketApi.create).toHaveBeenCalledWith(expect.objectContaining({
    category_id: categoryId, caller_phone: '+7 900 000 00 01', city: 'Москва', description: 'Нет доступа',
  })))
})

it('creates a manual ticket from a ready topic with structured contact fields', async () => {
  vi.mocked(ticketApi.create).mockResolvedValue({ id: 'ticket-ready' } as Ticket)
  renderCreate()
  fireEvent.click(screen.getByRole('button', { name: /Ручная/ }))
  await screen.findByRole('option', { name: 'Нет интернета' })
  fireEvent.change(await screen.findByRole('combobox', { name: 'Тема обращения' }), { target: { value: categoryId } })
  fireEvent.change(screen.getByRole('textbox', { name: /Контактный номер/ }), { target: { value: '+7 900 000 00 01' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Город/ }), { target: { value: 'Москва' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Описание проблемы/ }), { target: { value: 'Пропала связь' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать заявку' }))
  await waitFor(() => expect(ticketApi.create).toHaveBeenCalledWith(expect.objectContaining({
    ticket_type: 'MANUAL', category_id: categoryId, subject: undefined,
    caller_phone: '+7 900 000 00 01', city: 'Москва', description: 'Пропала связь',
  })))
})

it('creates a linked ticket with a free topic and overridden NetUP contact', async () => {
  const card: SubscriberCard = {
    lookup_id: '00000000-0000-4000-8000-000000000010', source: 'NETUP',
    fetched_at: '2026-01-01T00:00:00Z', expires_at: '2999-01-01T00:00:00Z', is_expired: false,
    subscriber: { ...subscriberDefaults, billing_account: '123', full_name: 'Абонент', organization_name: null,
      phone: '+7 900 000 00 01', actual_address: 'г. Москва, ул. Сетевая, д. 1' },
    accounts: [], financial_accounts: [], contracts: [], services: [], connections: [], technical_state: 'UNKNOWN',
  }
  vi.mocked(ticketApi.lookup).mockResolvedValue(card)
  vi.mocked(ticketApi.create).mockResolvedValue({ id: 'ticket-custom' } as Ticket)
  renderCreate()
  fireEvent.change(screen.getByRole('textbox', { name: 'Лицевой счёт' }), { target: { value: '123' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
  expect(await screen.findByText('Абонент')).toBeInTheDocument()
  fireEvent.change(await screen.findByRole('combobox', { name: 'Тема обращения' }), { target: { value: '__custom__' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Своя тема/ }), { target: { value: 'Повреждён кабель' } })
  expect(screen.getByRole('textbox', { name: /Город/ })).toHaveValue('Москва')
  fireEvent.change(screen.getByRole('textbox', { name: /Город/ }), { target: { value: 'Химки' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Источник контактного номера' }), { target: { value: '__manual__' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Другой номер/ }), { target: { value: '+7 900 222 33 44' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Описание проблемы/ }), { target: { value: 'Связь отсутствует' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать заявку' }))
  await waitFor(() => expect(ticketApi.create).toHaveBeenCalledWith(expect.objectContaining({
    ticket_type: 'SUBSCRIBER_LINKED', subject: 'Повреждён кабель', caller_phone: '+7 900 222 33 44',
    city: 'Химки', description: 'Связь отсутствует',
  })))
  expect(vi.mocked(ticketApi.create).mock.calls[0]?.[0]).not.toHaveProperty('category_id')
})
