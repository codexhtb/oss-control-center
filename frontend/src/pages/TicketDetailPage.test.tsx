import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { Page, TicketDetail } from '../features/tickets/types'
import type { MeResponse } from '../shared/api/types'
import { TicketDetailPage } from './TicketDetailPage'

vi.mock('../features/tickets/ticket-api', () => ({ ticketApi: {
  get: vi.fn(), comments: vi.fn(), history: vi.fn(), diagnostics: vi.fn(),
  decisions: vi.fn(), engineers: vi.fn(), start: vi.fn(), claim: vi.fn(), release: vi.fn(), addComment: vi.fn(),
  lookup: vi.fn(), checkTicket: vi.fn(), confirmCheck: vi.fn(), ticketOnu: vi.fn(),
  assign: vi.fn(), requestFieldWork: vi.fn(), addDecision: vi.fn(), resolve: vi.fn(), close: vi.fn(), remove: vi.fn(),
} }))

const ticketId = '00000000-0000-4000-8000-000000000010'
const base: TicketDetail = {
  id: ticketId, number: 73, ticket_type: 'INTERNAL', subject: 'Проверка OSS',
  description: 'Синтетическое описание', status: 'OPEN', priority: 'NORMAL',
  account_no: null, subscriber_snapshot_id: null, connection_snapshot_id: null,
  selected_service_id: null, created_by: '00000000-0000-4000-8000-000000000001',
  assigned_engineer_id: null, closed_by: null, closed_decision_id: null,
  resolved_by: null, resolved_decision_id: null, resolved_at: null,
  closed_at: null, deleted_at: null, deleted_by: null, delete_reason: null,
  version: 3, created_at: '2026-01-01T00:00:00Z',
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

function renderDetail(role: 'operator' | 'engineer', customSession?: MeResponse) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/tickets/${ticketId}`]}><Routes><Route path="/tickets/:id" element={<TicketDetailPage session={customSession || session(role)} />} /><Route path="/subscribers/:lookupId" element={<div>Карточка абонента открыта</div>} /></Routes></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  for (const fn of Object.values(ticketApi)) vi.mocked(fn).mockReset()
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
  vi.mocked(ticketApi.claim).mockResolvedValue({ ...base, status: 'IN_PROGRESS', assigned_engineer_id: session('engineer').user.id, version: 4 })
  renderDetail('engineer')
  expect(await screen.findByRole('heading', { name: 'Проверка OSS' })).toBeInTheDocument()
  expect(screen.getByText(/Заявка без привязки к абоненту/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Взять в работу/ }))
  await waitFor(() => expect(ticketApi.claim).toHaveBeenCalledWith(ticketId, 3))
})

it('lets only the assigned engineer release an active ticket', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'IN_PROGRESS', assigned_engineer_id: session('engineer').user.id })
  vi.mocked(ticketApi.release).mockResolvedValue({ ...base, status: 'IN_PROGRESS', version: 4 })
  renderDetail('engineer')
  fireEvent.click(await screen.findByRole('button', { name: 'Освободить заявку' }))
  await waitFor(() => expect(ticketApi.release).toHaveBeenCalledWith(ticketId, 3))
})

it('does not offer claim or reassignment of another engineer ticket', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'IN_PROGRESS', assigned_engineer_id: 'other-engineer', assigned_engineer_name: 'Другой инженер' })
  renderDetail('engineer')
  expect(await screen.findByText('Другой инженер')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Взять в работу|Освободить заявку/ })).not.toBeInTheDocument()
  expect(screen.queryByText('Назначить инженера')).not.toBeInTheDocument()
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
    selected_connection: { id: '00000000-0000-4000-8000-000000000041',
      access_type: 'ETHERNET', device: 'synthetic-switch', port: '1', vlan: '101',
      onu_id: null, address: null, service_id: null, ip_group: null, ip_groups: [], field_meta: {} },
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

it('shows PON diagnostics in one section and keeps equipment checks available on demand', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base, ticket_type: 'SUBSCRIBER_LINKED', status: 'IN_PROGRESS',
    account_no: 'SYNTHETIC-ACCOUNT',
    subscriber: { full_name: 'Синтетический абонент', organization_name: null, phone: null,
      actual_address: null, fetched_at: base.created_at, source: 'MOCK', services: [], connections: [] },
    connection_snapshot_id: '00000000-0000-4000-8000-000000000041',
    selected_connection: { id: '00000000-0000-4000-8000-000000000041',
      access_type: 'UNKNOWN', device: 'synthetic-olt', port: '7', vlan: '101',
      onu_id: null, address: null, service_id: null, ip_group: null, ip_groups: [], field_meta: {} },
  })
  vi.mocked(ticketApi.ticketOnu).mockResolvedValue({
    source: 'BDCOM', retrieved_at: base.created_at, availability: 'AVAILABLE',
    association: 'MATCHED_BY_RULE', limitations: [], time_basis: 'SOURCE_LOCAL_UNVERIFIED',
    candidates: [{ olt: 'synthetic-olt', interface: 'EPON0/1:17', pon_type: 'EPON',
      mac: '02:00:00:00:00:01', serial: null, state: 'ONLINE', reason: 'power-off',
      onu_rx_dbm: '-21', olt_rx_dbm: '-24', onu_tx_dbm: null,
      inventory_polled_at: '2026-01-01 00:00:00', optical_measured_at: '2026-01-01 00:00:00' }],
  })
  vi.mocked(ticketApi.checkTicket).mockResolvedValue({
    check_id: 'check-id', expires_at: base.created_at, report: {
      connection_snapshot_id: '00000000-0000-4000-8000-000000000041', checked_at: base.created_at,
      device_match: 'KNOWN', interface_match: 'UNKNOWN', limitations: ['interface_mapping_unconfirmed'],
      observations: [{ source: 'LIBRENMS', measured_object: 'device:1', result: 'UP',
        quality: 'KNOWN', observed_at: base.created_at, retrieved_at: base.created_at }],
    },
  })
  vi.mocked(ticketApi.confirmCheck).mockResolvedValue({ id: 'diagnostic-id' } as never)
  renderDetail('engineer')
  expect(await screen.findByText(/EPON0\/1:17/)).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { name: 'Диагностика подключения' })).toHaveLength(1)
  const diagnosticsHeading = screen.getByRole('heading', { name: 'Диагностика подключения' })
  const subscriberHeading = screen.getByRole('heading', { name: 'Абонент и подключение' })
  const commentHeading = screen.getByRole('heading', { name: 'Комментарии' })
  expect(diagnosticsHeading.compareDocumentPosition(subscriberHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(subscriberHeading.compareDocumentPosition(commentHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'BDCOM ONU' })).not.toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Мониторинг подключения' })).not.toBeInTheDocument()
  expect(screen.queryByText('power-off')).not.toBeInTheDocument()
  expect(screen.getByRole('group', { name: 'ONU Rx' })).toHaveTextContent('-21 dBm')
  expect(screen.queryByText('Проверка OLT в мониторинге')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Проверить OLT' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Проверить подключение' })).not.toBeInTheDocument()
  expect(screen.queryByText('device:1')).not.toBeInTheDocument()
  expect(ticketApi.checkTicket).not.toHaveBeenCalled()
})

it('keeps equipment monitoring primary for a confirmed Ethernet connection', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base, ticket_type: 'SUBSCRIBER_LINKED',
    connection_snapshot_id: '00000000-0000-4000-8000-000000000041',
    selected_connection: { id: '00000000-0000-4000-8000-000000000041',
      access_type: 'ETHERNET', device: 'synthetic-switch', port: '1', vlan: '101',
      onu_id: null, address: null, service_id: null, ip_group: null, ip_groups: [], field_meta: {} },
  })
  renderDetail('engineer')
  expect(await screen.findByRole('heading', { name: 'Диагностика подключения' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Проверить подключение' })).toBeVisible()
  expect(screen.queryByText('Проверка OLT в мониторинге')).not.toBeInTheDocument()
  expect(ticketApi.ticketOnu).not.toHaveBeenCalled()
})

it('groups metadata and permitted OPEN actions in one panel, with deletion behind confirmation', async () => {
  const admin: MeResponse = { ...session('engineer'), user: { ...session('engineer').user, role: 'admin' },
    permissions: [...session('engineer').permissions, 'tickets.delete.all'] }
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, created_by_name: 'Автор теста', assigned_engineer_name: 'Инженер теста', assigned_engineer_id: 'engineer-id' })
  vi.mocked(ticketApi.engineers).mockResolvedValue({ items: [{ id: 'other-engineer', display_name: 'Другой инженер' }], page: 1, size: 100, total: 1 })
  vi.mocked(ticketApi.assign).mockResolvedValue(base)
  vi.mocked(ticketApi.remove).mockResolvedValue({ ...base, deleted_at: base.updated_at })
  renderDetail('engineer', admin)
  const panel = within(await screen.findByRole('complementary', { name: 'Управление заявкой' }))
  expect(panel.getByRole('heading', { name: 'Заявка #73' })).toBeInTheDocument()
  expect(panel.getByText('Автор теста')).toBeInTheDocument()
  expect(panel.getByText('Инженер теста')).toBeInTheDocument()
  expect(panel.getByText('Открыта')).toBeInTheDocument()
  expect(panel.queryByRole('button', { name: 'Записать решение' })).not.toBeInTheDocument()
  expect(panel.queryByRole('button', { name: 'Закрыть заявку' })).not.toBeInTheDocument()
  fireEvent.click(panel.getByText('Назначить инженера'))
  fireEvent.change(await panel.findByRole('combobox', { name: 'Активный инженер' }), { target: { value: 'other-engineer' } })
  fireEvent.click(panel.getByRole('button', { name: 'Назначить' }))
  await waitFor(() => expect(ticketApi.assign).toHaveBeenCalledWith(ticketId, 'other-engineer', 3))
  expect(panel.getByRole('button', { name: 'Удалить' })).not.toBeVisible()
  fireEvent.click(panel.getByText('Дополнительно'))
  fireEvent.click(panel.getByRole('button', { name: 'Удалить' }))
  expect(panel.getByRole('group', { name: 'Подтверждение удаления' })).toHaveTextContent('№73 «Проверка OSS»')
  expect(panel.getByRole('textbox', { name: 'Причина (необязательно)' })).toHaveFocus()
  fireEvent.keyDown(panel.getByRole('group', { name: 'Подтверждение удаления' }), { key: 'Escape' })
  await waitFor(() => expect(panel.getByRole('button', { name: 'Удалить' })).toHaveFocus())
  fireEvent.click(panel.getByRole('button', { name: 'Удалить' }))
  expect(ticketApi.remove).not.toHaveBeenCalled()
  fireEvent.change(panel.getByRole('textbox', { name: 'Причина (необязательно)' }), { target: { value: 'Дубликат' } })
  fireEvent.click(panel.getByRole('button', { name: 'Подтвердить удаление' }))
  await waitFor(() => expect(ticketApi.remove).toHaveBeenCalledWith(ticketId, 3, 'Дубликат'))
})

it('records a verdict in the action panel before offering a separate closure', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'IN_PROGRESS', assigned_engineer_id: session('engineer').user.id })
  const decision = { id: 'decision-id', ticket_id: ticketId, engineer_id: session('engineer').user.id,
    decision_text: 'Результат', performed_actions: 'Проверка', created_at: base.created_at }
  vi.mocked(ticketApi.resolve).mockImplementation(async () => {
    vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'RESOLVED', assigned_engineer_id: session('engineer').user.id, version: 4, resolved_by: session('engineer').user.id, resolved_at: base.created_at, resolved_decision_id: decision.id })
    vi.mocked(ticketApi.decisions).mockResolvedValue({ ...empty, items: [decision], total: 1 })
    return { ...base, status: 'RESOLVED', version: 4, resolved_decision_id: decision.id }
  })
  vi.mocked(ticketApi.close).mockResolvedValue({ ...base, status: 'CLOSED', version: 5 })
  renderDetail('engineer')
  const panel = within(await screen.findByRole('complementary', { name: 'Управление заявкой' }))
  expect(panel.queryByRole('button', { name: /Взять в работу/ })).not.toBeInTheDocument()
  expect(panel.queryByText('Закрытие заявки')).not.toBeInTheDocument()
  const input = panel.getByRole('textbox', { name: 'Решение инженера' })
  fireEvent.change(input, { target: { value: 'Результат' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Выполненные действия' }), { target: { value: 'Проверка' } })
  fireEvent.click(panel.getByRole('button', { name: 'Решено' }))
  await waitFor(() => expect(ticketApi.resolve).toHaveBeenCalledWith(ticketId, 'Результат', 'Проверка', 3))
  expect(await panel.findByText(/Ожидает подтверждения абонента/)).toBeInTheDocument()
  expect(panel.queryByRole('button', { name: 'Освободить заявку' })).not.toBeInTheDocument()
  fireEvent.click(await panel.findByText('Закрытие заявки'))
  fireEvent.click(panel.getByRole('button', { name: 'Закрыть заявку' }))
  await waitFor(() => expect(ticketApi.close).toHaveBeenCalledWith(ticketId, 'decision-id', 4))
})

it('shows a resolved verdict without active engineer actions to a ticket editor', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base, status: 'RESOLVED', assigned_engineer_id: 'another-engineer',
    resolved_by: 'another-engineer', resolved_at: base.created_at,
    resolved_decision_id: 'verdict-id',
  })
  vi.mocked(ticketApi.decisions).mockResolvedValue({
    ...empty, total: 1, items: [{ id: 'verdict-id', ticket_id: ticketId,
      engineer_id: 'another-engineer', engineer_name: 'Инженер теста',
      decision_text: 'Синтетический вердикт', performed_actions: 'Проверка',
      created_at: base.created_at }],
  })
  renderDetail('operator', { ...session('operator'), permissions: ['tickets.read.all', 'tickets.edit.all'] })
  const panel = within(await screen.findByRole('complementary', { name: 'Управление заявкой' }))
  expect(await panel.findByText(/Ожидает подтверждения абонента/)).toBeInTheDocument()
  expect(await screen.findByText('Синтетический вердикт')).toBeInTheDocument()
  expect(screen.getByText(/Инженер теста · Выполнено/)).toBeInTheDocument()
  expect(panel.queryByRole('button', { name: /Взять в работу|Освободить заявку|Решено/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: 'Решение инженера' })).not.toBeInTheDocument()
})

it.each(['operator', 'admin'] as const)('uses grants rather than role name for %s', async (role) => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'IN_PROGRESS' })
  renderDetail('operator', { ...session('operator'), user: { ...session('operator').user, role } })
  const panel = within(await screen.findByRole('complementary', { name: 'Управление заявкой' }))
  expect(panel.queryByText('Назначить инженера')).not.toBeInTheDocument()
  expect(panel.queryByText('Выездные работы')).not.toBeInTheDocument()
  for (const button of panel.queryAllByRole('button')) expect(button).not.toBeVisible()
  expect(panel.queryByText('Дополнительно')).not.toBeInTheDocument()
})

it.each([true, false])('applies delete.own to ownership, own=%s, including CLOSED tickets', async (own) => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'CLOSED', created_by: own ? base.created_by : 'another-author' })
  const staff = session('engineer')
  renderDetail('engineer', { ...staff, permissions: [...staff.permissions, 'tickets.delete.own'] })
  const panel = within(await screen.findByRole('complementary', { name: 'Управление заявкой' }))
  for (const button of panel.queryAllByRole('button')) expect(button).not.toBeVisible()
  expect(panel.queryByText('Назначить инженера')).not.toBeInTheDocument()
  expect(panel.queryByText('Выездные работы')).not.toBeInTheDocument()
  expect(panel.queryByText('Дополнительно') !== null).toBe(own)
})
