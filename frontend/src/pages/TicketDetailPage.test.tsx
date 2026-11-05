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
  decisions: vi.fn(), decision: vi.fn(), callbacks: vi.fn(), recordCallback: vi.fn(), engineers: vi.fn(), start: vi.fn(), claim: vi.fn(), release: vi.fn(), addComment: vi.fn(),
  lookup: vi.fn(), checkTicket: vi.fn(), confirmCheck: vi.fn(), ticketOnu: vi.fn(),
  assign: vi.fn(), requestFieldWork: vi.fn(), latestFieldWork: vi.fn(), sendFieldWork: vi.fn(),
  fieldWorkMessages: vi.fn(), verifyFieldWork: vi.fn(),
  addDecision: vi.fn(), resolve: vi.fn(), close: vi.fn(), remove: vi.fn(),
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
      ? ['subscribers.read', 'tickets.read.all', 'tickets.comment', 'tickets.start', 'tickets.assign', 'tickets.diagnose', 'diagnostics.read', 'diagnostics.run', 'tickets.resolve', 'tickets.close']
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
  vi.mocked(ticketApi.decision).mockImplementation(async (_id, decisionId) => ({ id: decisionId, ticket_id: ticketId, engineer_id: session('engineer').user.id, engineer_name: 'Инженер теста', decision_text: 'Синтетический вердикт', reason: 'NETWORK_FAULT', performed_actions: null, created_at: base.created_at }))
  vi.mocked(ticketApi.callbacks).mockResolvedValue(empty)
  vi.mocked(ticketApi.engineers).mockResolvedValue({ items: [], page: 1, size: 100, total: 0 })
  vi.mocked(ticketApi.latestFieldWork).mockResolvedValue({ handoff: null })
  vi.mocked(ticketApi.fieldWorkMessages).mockResolvedValue(empty)
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

it('lets an operator with work and decision grants claim a ticket', async () => {
  const operator = session('operator')
  vi.mocked(ticketApi.claim).mockResolvedValue({ ...base, status: 'IN_PROGRESS', assigned_engineer_id: operator.user.id, version: 4 })
  renderDetail('operator', { ...operator, permissions: [...operator.permissions, 'tickets.start', 'tickets.resolve'] })
  fireEvent.click(await screen.findByRole('button', { name: /Взять в работу/ }))
  await waitFor(() => expect(ticketApi.claim).toHaveBeenCalledWith(ticketId, 3))
})

it('keeps the decision revision form collapsed until requested', async () => {
  const engineer = session('engineer')
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'RESOLVED', resolved_decision_id: 'decision', assigned_engineer_id: engineer.user.id })
  renderDetail('engineer')
  expect(await screen.findByText('Синтетический вердикт')).toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: 'Новая версия решения' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Уточнить решение' }))
  expect(screen.getByRole('textbox', { name: 'Новая версия решения' })).toBeInTheDocument()
})

it('lets only the assigned engineer release an active ticket', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'IN_PROGRESS', assigned_engineer_id: session('engineer').user.id })
  vi.mocked(ticketApi.release).mockResolvedValue({ ...base, status: 'IN_PROGRESS', version: 4 })
  renderDetail('engineer')
  fireEvent.click(await screen.findByRole('button', { name: 'Освободить заявку' }))
  await waitFor(() => expect(ticketApi.release).toHaveBeenCalledWith(ticketId, 3))
})

it('saves a field-work reason before an explicit Telegram send', async () => {
  const staff = session('engineer')
  const privileged: MeResponse = {
    ...staff,
    permissions: [...staff.permissions, 'tickets.field_work'],
  }
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base,
    ticket_type: 'SUBSCRIBER_LINKED',
    status: 'IN_PROGRESS',
    assigned_engineer_id: staff.user.id,
    account_no: 'SYNTHETIC-001',
    subscriber: {
      full_name: 'Синтетический абонент', organization_name: null, phone: '+000000',
      actual_address: 'г. Тест, ул. Безопасная', house_number: '20',
      fetched_at: base.created_at, source: 'MOCK', services: [], connections: [],
    },
  })
  const handoff = {
    id: '00000000-0000-4000-8000-000000000040', ticket_id: ticketId,
    reason: 'Проверить кабель', delivery_status: 'DRAFT' as const,
    work_status: 'AWAITING_REPORT' as const, chat_id: '-5319791765',
    telegram_message_id: null, message_preview: 'Заявка #73\nПричина передачи: Проверить кабель',
    ready_to_send: true, missing_fields: [], created_by: staff.user.id,
    sent_by: null, sent_by_name: null, last_error_code: null,
    created_at: base.created_at, send_started_at: null, sent_at: null,
    reported_at: null, verified_by: null, verified_by_name: null, verified_at: null,
    updated_at: base.updated_at,
  }
  vi.mocked(ticketApi.latestFieldWork)
    .mockResolvedValueOnce({ handoff: null })
    .mockResolvedValue({ handoff })
  vi.mocked(ticketApi.requestFieldWork).mockResolvedValue({ ...base, version: 4 })
  vi.mocked(ticketApi.sendFieldWork).mockResolvedValue({
    ...handoff, delivery_status: 'SENT', telegram_message_id: 701,
    sent_by: staff.user.id, sent_at: base.updated_at, ready_to_send: false,
  })

  renderDetail('engineer', privileged)
  expect(await screen.findByRole('heading', { name: 'Выездные работы' })).toBeVisible()
  fireEvent.change(screen.getByRole('textbox', { name: 'Причина передачи' }), {
    target: { value: 'Проверить кабель' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Зафиксировать запрос' }))
  await waitFor(() => expect(ticketApi.requestFieldWork).toHaveBeenCalledWith(
    ticketId, 'Проверить кабель', 3,
  ))
  expect(ticketApi.sendFieldWork).not.toHaveBeenCalled()
  const send = await screen.findByRole('button', { name: 'Отправить боту' })
  expect(screen.queryByRole('textbox', { name: 'Адрес для выезда' })).not.toBeInTheDocument()
  fireEvent.click(send)
  await waitFor(() => expect(ticketApi.sendFieldWork).toHaveBeenCalledWith(
    ticketId, handoff.id, expect.any(String), undefined,
  ))
})

it.each([
  ['г. Тест, ул. Ручная, д. 7', 'г. Тест, ул. Ручная, д. 7'],
  ['', 'не указан'],
])('previews and sends an optional visit address (%s)', async (input, address) => {
  const staff = session('engineer')
  const privileged: MeResponse = { ...staff, permissions: [...staff.permissions, 'tickets.field_work'] }
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base, ticket_type: 'SUBSCRIBER_LINKED', status: 'IN_PROGRESS',
    assigned_engineer_id: staff.user.id, account_no: 'SYNTHETIC-001',
    subscriber: { full_name: 'Синтетический абонент', organization_name: null,
      phone: '+000000', actual_address: null, house_number: null,
      fetched_at: base.created_at, source: 'MOCK', services: [], connections: [] },
  })
  const handoff = {
    id: '00000000-0000-4000-8000-000000000040', ticket_id: ticketId,
    reason: 'Проверить кабель', delivery_status: 'DRAFT' as const,
    work_status: 'AWAITING_REPORT' as const, chat_id: '-5319791765',
    telegram_message_id: null, message_preview: 'Заявка #73\nАдрес: не указан\nПричина передачи: Проверить кабель',
    visit_address: null, ready_to_send: true, missing_fields: [], created_by: staff.user.id,
    sent_by: null, sent_by_name: null, last_error_code: null,
    created_at: base.created_at, send_started_at: null, sent_at: null,
    reported_at: null, verified_by: null, verified_by_name: null, verified_at: null,
    updated_at: base.updated_at,
  }
  vi.mocked(ticketApi.latestFieldWork).mockResolvedValue({ handoff })
  vi.mocked(ticketApi.sendFieldWork).mockResolvedValue({ ...handoff, delivery_status: 'SENT' })
  renderDetail('engineer', privileged)
  const field = await screen.findByRole('textbox', { name: 'Адрес для выезда' })
  fireEvent.change(field, { target: { value: input } })
  expect(document.querySelector('.field-dispatch-preview pre')?.textContent).toContain(`Адрес: ${address}`)
  fireEvent.click(screen.getByRole('button', { name: 'Отправить боту' }))
  await waitFor(() => expect(ticketApi.sendFieldWork).toHaveBeenCalledWith(
    ticketId, handoff.id, expect.any(String), input || null,
  ))
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
      actual_address: 'г. Тест, ул. Примерная', house_number: '20', fetched_at: base.created_at, source: 'MOCK',
      services: [], connections: [],
    },
    selected_connection: {
      id: '00000000-0000-4000-8000-000000000020', access_type: 'UNKNOWN',
      device: 'synthetic-device', port: 'synthetic-port', vlan: '100', onu_id: null,
      address: null, service_id: null, ip_group: null, ip_groups: [{ group_id: 'synthetic-group', field_meta: {}, rows: [{
        row_id: 'synthetic-row', ip_type_code: 4, configured_ipv4: '192.0.2.10', configured_prefix_length: 32,
        address_assignment: 'CONFIGURED', ip_raw: 3221225994, mask_raw: -1, ip_ext_raw: null,
        mask_ext_raw: null, dhcp_pool_id: null, switch_id: null, device: null, port_id: null,
        vlan_id: null, field_meta: {},
      }] }], field_meta: {},
    },
  })
  vi.mocked(ticketApi.lookup).mockResolvedValue({ lookup_id: '00000000-0000-4000-8000-000000000030' } as never)
  renderDetail('operator')
  expect(await screen.findByRole('heading', { name: 'Проверка OSS' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: 'Абонент' }))
  expect(screen.getByText(`ЛС ${accountNo}`)).toBeInTheDocument()
  expect(screen.getByText('Адрес: г. Тест, ул. Примерная, д. 20')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: 'Диагностика' }))
  expect(screen.getByText('Устройство: synthetic-device')).toBeInTheDocument()
  expect(screen.getByText('Порт: synthetic-port')).toBeInTheDocument()
  expect(screen.getByText('VLAN: 100')).toBeInTheDocument()
  expect(screen.getByText('IPv4 в NetUP: 192.0.2.10/32')).toBeInTheDocument()
  expect(screen.queryByText(/Технология: UNKNOWN|ONU: UNKNOWN|Техническое состояние: UNKNOWN/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: 'Абонент' }))
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
  fireEvent.click(screen.getByRole('tab', { name: 'История' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Новый комментарий' }), { target: { value: 'Уточнение' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить комментарий' }))
  await waitFor(() => expect(ticketApi.addComment).toHaveBeenCalledWith(ticketId, 'Уточнение'))
})

it('loads diagnostics and history only when their tabs open', async () => {
  renderDetail('engineer')
  expect(await screen.findByRole('heading', { name: 'Проверка OSS' })).toBeInTheDocument()
  expect(ticketApi.history).not.toHaveBeenCalled()
  expect(ticketApi.diagnostics).not.toHaveBeenCalled()
  expect(ticketApi.decisions).not.toHaveBeenCalled()
  expect(screen.queryByRole('textbox', { name: 'Наблюдение инженера' })).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('tab', { name: 'Диагностика' }))
  await waitFor(() => expect(ticketApi.diagnostics).toHaveBeenCalledTimes(1))
  expect(ticketApi.history).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('tab', { name: 'История' }))
  await waitFor(() => {
    expect(ticketApi.decisions).toHaveBeenCalledTimes(1)
    expect(ticketApi.history).toHaveBeenCalledTimes(1)
  })
})

it('keeps work and comment drafts when switching tabs by mouse or keyboard', async () => {
  const staff = session('engineer')
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'IN_PROGRESS', assigned_engineer_id: staff.user.id })
  renderDetail('engineer', { ...staff, permissions: [...staff.permissions, 'tickets.field_work'] })
  const decision = await screen.findByRole('textbox', { name: 'Решение инженера' })
  fireEvent.change(decision, { target: { value: 'Проверить порт' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Причина передачи' }), { target: { value: 'Осмотр линии' } })

  const workTab = screen.getByRole('tab', { name: 'Работа с заявкой' })
  fireEvent.keyDown(workTab, { key: 'End' })
  expect(screen.getByRole('tab', { name: 'История' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('heading', { name: 'Комментарии' })).toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'Новый комментарий' }), { target: { value: 'Связаться с абонентом' } })
  fireEvent.click(workTab)
  expect(screen.getByRole('textbox', { name: 'Решение инженера' })).toHaveValue('Проверить порт')
  expect(screen.getByRole('textbox', { name: 'Причина передачи' })).toHaveValue('Осмотр линии')
  fireEvent.click(screen.getByRole('tab', { name: 'История' }))
  expect(screen.getByRole('textbox', { name: 'Новый комментарий' })).toHaveValue('Связаться с абонентом')
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
  fireEvent.click(screen.getByRole('tab', { name: 'Диагностика' }))
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
      access_type: 'EPON', device: 'synthetic-olt', port: '7', vlan: '101',
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
  expect(await screen.findByRole('heading', { name: 'Проверка OSS' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: 'Диагностика' }))
  expect(await screen.findByText(/EPON0\/1:17/)).toBeInTheDocument()
  expect(screen.getAllByRole('heading', { name: 'Диагностика подключения' })).toHaveLength(1)
  expect(screen.queryByRole('heading', { name: 'Абонент и подключение' })).not.toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Комментарии' })).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'BDCOM ONU' })).toBeVisible()
  expect(screen.getByText('ONU опрошена')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Мониторинг подключения' })).not.toBeInTheDocument()
  expect(screen.queryByText('power-off')).not.toBeInTheDocument()
  expect(screen.getByRole('group', { name: 'ONU Rx' })).toHaveTextContent('-21 dBm')
  expect(screen.queryByText('Проверка OLT в мониторинге')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Проверить OLT' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Проверить подключение' }))
  await waitFor(() => expect(ticketApi.checkTicket).toHaveBeenCalledWith(ticketId, 3))
  expect(await screen.findByText('device:1')).toBeInTheDocument()
})

it.each([
  { technology: 'GPON' as const, availability: 'AVAILABLE' as const, state: 'ONLINE' as const },
  { technology: 'GPON' as const, availability: 'UNAVAILABLE' as const, state: null },
  { technology: 'EPON' as const, availability: 'AVAILABLE' as const, state: 'OFFLINE' as const },
  { technology: 'UNKNOWN' as const, availability: 'UNAVAILABLE' as const, state: null },
])('keeps LibreNMS/Icinga available for $technology with ONU $availability $state', async ({ technology, availability, state }) => {
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base, ticket_type: 'SUBSCRIBER_LINKED', status: 'IN_PROGRESS',
    subscriber_snapshot_id: '00000000-0000-4000-8000-000000000040',
    connection_snapshot_id: '00000000-0000-4000-8000-000000000041',
    selected_connection: { id: '00000000-0000-4000-8000-000000000041',
      access_type: technology, device: 'synthetic-olt', port: '7', vlan: '101',
      onu_id: null, address: null, service_id: null, ip_group: null, ip_groups: [], field_meta: {} },
  })
  vi.mocked(ticketApi.ticketOnu).mockResolvedValue({
    source: 'BDCOM', retrieved_at: base.created_at, availability,
    association: state ? 'MATCHED_BY_RULE' : 'UNCONFIRMED',
    limitations: state ? [] : ['onu_source_not_configured'], time_basis: 'SOURCE_LOCAL_UNVERIFIED',
    candidates: state ? [{ olt: 'synthetic-olt', interface: `${technology}0/1:17`, pon_type: technology,
      mac: null, serial: null, state, reason: null, onu_rx_dbm: null, olt_rx_dbm: null,
      onu_tx_dbm: null, inventory_polled_at: null, optical_measured_at: null }] : [],
  })
  vi.mocked(ticketApi.checkTicket).mockResolvedValue({
    check_id: 'check-id', expires_at: base.created_at, report: {
      connection_snapshot_id: '00000000-0000-4000-8000-000000000041', checked_at: base.created_at,
      device_match: 'KNOWN', interface_match: 'UNKNOWN', limitations: ['interface_mapping_unconfirmed'],
      observations: [
        { source: 'LIBRENMS', measured_object: 'device:1', result: 'DOWN', quality: 'KNOWN', observed_at: base.created_at, retrieved_at: base.created_at },
        { source: 'ICINGA', measured_object: 'host:1', result: 'WARNING', quality: 'KNOWN', observed_at: base.created_at, retrieved_at: base.created_at },
      ],
    },
  })
  renderDetail('engineer')
  fireEvent.click(await screen.findByRole('tab', { name: 'Диагностика' }))
  expect(screen.getByRole('heading', { name: 'Диагностика подключения' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Проверить подключение' })).toBeVisible()
  await waitFor(() => expect(ticketApi.ticketOnu).toHaveBeenCalledWith(ticketId, expect.anything()))
  if (availability === 'UNAVAILABLE') expect(await screen.findByText('Источник BDCOM ONU не подключён или недоступен.')).toBeInTheDocument()
  else expect(await screen.findByText(new RegExp(`${technology}0/1:17`))).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Проверить подключение' }))
  await waitFor(() => expect(ticketApi.checkTicket).toHaveBeenCalledWith(ticketId, 3))
  expect(await screen.findByText('device:1')).toBeInTheDocument()
  expect(screen.getByText('host:1')).toBeInTheDocument()
})

it('shows a confirmed GPON ONU for an UNKNOWN ticket connection alongside monitoring reasons', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base, ticket_type: 'SUBSCRIBER_LINKED', status: 'IN_PROGRESS',
    connection_snapshot_id: '00000000-0000-4000-8000-000000000041',
    selected_connection: { id: '00000000-0000-4000-8000-000000000041',
      access_type: 'UNKNOWN', device: 'synthetic-olt', port: '21', vlan: '350',
      onu_id: null, address: null, service_id: null, ip_group: null, ip_groups: [], field_meta: {} },
  })
  vi.mocked(ticketApi.ticketOnu).mockResolvedValue({
    source: 'BDCOM', retrieved_at: base.created_at, availability: 'AVAILABLE',
    association: 'MATCHED_BY_RULE', limitations: [], time_basis: 'SOURCE_LOCAL_UNVERIFIED',
    match: { netup_port: 21, pon_port: 8, vlan: 350, olt_mapping_basis: 'manual_verified',
      billing_observed_at: null, billing_source: 'NETUP', onu_source: 'LIBRENMS_BDCOM' },
    candidates: [{ olt: 'synthetic-olt', interface: 'GPON0/8:26', pon_type: 'GPON',
      mac: null, serial: null, state: 'ONLINE', reason: null,
      onu_rx_dbm: '-21', olt_rx_dbm: '-24', onu_tx_dbm: '-3',
      inventory_polled_at: '2026-01-01 00:00:00', optical_measured_at: '2026-01-01 00:00:00' }],
  })
  vi.mocked(ticketApi.checkTicket).mockResolvedValue({
    check_id: 'check-id', expires_at: base.created_at, report: {
      connection_snapshot_id: '00000000-0000-4000-8000-000000000041', checked_at: base.created_at,
      device_match: 'KNOWN', interface_match: 'UNKNOWN',
      limitations: ['interface_mapping_unconfirmed', 'icinga_mapping_missing', 'icinga_not_configured'],
      observations: [{ source: 'LIBRENMS', measured_object: 'device:20', result: 'UP',
        quality: 'KNOWN', observed_at: base.created_at, retrieved_at: base.created_at }],
    },
  })
  renderDetail('engineer')
  fireEvent.click(await screen.findByRole('tab', { name: 'Диагностика' }))
  expect(await screen.findByText('GPON0/8:26')).toBeInTheDocument()
  expect(screen.getByText('Сопоставление ONU подтверждено по устройству, PON-порту и VLAN.')).toBeInTheDocument()
  expect(screen.getByText('Online')).toBeInTheDocument()
  expect(screen.getByRole('group', { name: 'ONU Rx' })).toHaveTextContent('-21 dBm')
  expect(screen.getByText('ONU опрошена')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Проверить подключение' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Проверить подключение' }))
  expect(await screen.findByText('device:20')).toBeInTheDocument()
  expect(screen.getByText('Для устройства нет утверждённого сопоставления с Icinga2.')).toBeInTheDocument()
  expect(screen.getByText('Icinga2 не настроена.')).toBeInTheDocument()
})

it('keeps monitoring available when the BDCOM request fails and shows source failures', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'IN_PROGRESS',
    connection_snapshot_id: '00000000-0000-4000-8000-000000000041',
    selected_connection: { id: '00000000-0000-4000-8000-000000000041', access_type: 'GPON',
      device: 'synthetic-olt', port: '7', vlan: '101', onu_id: null, address: null,
      service_id: null, ip_group: null, ip_groups: [], field_meta: {} },
  })
  vi.mocked(ticketApi.ticketOnu).mockRejectedValue(new Error('BDCOM unavailable'))
  vi.mocked(ticketApi.checkTicket).mockResolvedValue({ check_id: 'check-id', expires_at: base.created_at,
    report: { connection_snapshot_id: '00000000-0000-4000-8000-000000000041', checked_at: base.created_at,
      device_match: 'UNKNOWN', interface_match: 'UNKNOWN',
      observations: [{ source: 'LIBRENMS', measured_object: 'mapped_device', result: 'UNAVAILABLE',
        quality: 'UNAVAILABLE', observed_at: null, retrieved_at: base.created_at }],
      limitations: ['librenms_unavailable', 'icinga_unavailable'] },
  })
  renderDetail('engineer')
  fireEvent.click(await screen.findByRole('tab', { name: 'Диагностика' }))
  expect(await screen.findByText('Не удалось связаться с сервером. Проверьте подключение.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Проверить подключение' }))
  expect(await screen.findByText('LibreNMS не ответил на запрос.')).toBeInTheDocument()
  expect(screen.getByText('Icinga2 не ответила на запрос.')).toBeInTheDocument()
  expect(screen.getAllByText('Источник недоступен')).toHaveLength(2)
})

it('does not offer monitoring without a connection snapshot', async () => {
  renderDetail('engineer')
  fireEvent.click(await screen.findByRole('tab', { name: 'Диагностика' }))
  expect(screen.queryByRole('button', { name: 'Проверить подключение' })).not.toBeInTheDocument()
})

it('does not offer monitoring without diagnostics.run', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base,
    connection_snapshot_id: '00000000-0000-4000-8000-000000000041',
    selected_connection: { id: '00000000-0000-4000-8000-000000000041', access_type: 'ETHERNET',
      device: 'synthetic-switch', port: '1', vlan: '101', onu_id: null, address: null,
      service_id: null, ip_group: null, ip_groups: [], field_meta: {} },
  })
  renderDetail('operator')
  fireEvent.click(await screen.findByRole('tab', { name: 'Диагностика' }))
  expect(screen.queryByRole('button', { name: 'Проверить подключение' })).not.toBeInTheDocument()
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
  expect(await screen.findByRole('heading', { name: 'Проверка OSS' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: 'Диагностика' }))
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
  const panel = within(await screen.findByRole('tabpanel', { name: 'Работа с заявкой' }))
  expect(screen.getByText('SERVICEDESK / ЗАЯВКА #73')).toBeInTheDocument()
  expect(panel.getByText('Автор теста')).toBeInTheDocument()
  expect(panel.getByText('Инженер теста')).toBeInTheDocument()
  expect(screen.getByText('Открыта')).toBeInTheDocument()
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
    decision_text: 'Результат', reason: 'CUSTOMER_EQUIPMENT' as const, performed_actions: null, created_at: base.created_at }
  vi.mocked(ticketApi.resolve).mockImplementation(async () => {
    vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'RESOLVED', assigned_engineer_id: session('engineer').user.id, version: 4, resolved_by: session('engineer').user.id, resolved_at: base.created_at, resolved_decision_id: decision.id })
    vi.mocked(ticketApi.decision).mockResolvedValue(decision)
    return { ...base, status: 'RESOLVED', version: 4, resolved_decision_id: decision.id }
  })
  vi.mocked(ticketApi.close).mockResolvedValue({ ...base, status: 'CLOSED', version: 5 })
  renderDetail('engineer')
  const panel = within(await screen.findByRole('tabpanel', { name: 'Работа с заявкой' }))
  expect(panel.queryByRole('button', { name: /Взять в работу/ })).not.toBeInTheDocument()
  expect(panel.queryByText('Закрыть без обзвона')).not.toBeInTheDocument()
  const input = panel.getByRole('textbox', { name: 'Решение инженера' })
  fireEvent.change(input, { target: { value: 'Результат' } })
  fireEvent.change(panel.getByRole('combobox', { name: 'Причина решения' }), { target: { value: 'CUSTOMER_EQUIPMENT' } })
  fireEvent.click(panel.getByRole('button', { name: 'Решено' }))
  await waitFor(() => expect(ticketApi.resolve).toHaveBeenCalledWith(ticketId, 'Результат', 'CUSTOMER_EQUIPMENT', 3))
  expect(screen.queryByRole('textbox', { name: 'Выполненные действия' })).not.toBeInTheDocument()
  expect(await panel.findByText(/Следующий шаг — закрытие без обзвона/)).toBeInTheDocument()
  expect(panel.queryByRole('button', { name: 'Освободить заявку' })).not.toBeInTheDocument()
  fireEvent.click(await panel.findByText('Закрыть без обзвона'))
  fireEvent.change(panel.getByRole('textbox', { name: 'Причина закрытия без звонка' }), { target: { value: 'Внутренняя задача' } })
  fireEvent.click(panel.getByRole('button', { name: 'Закрыть заявку' }))
  await waitFor(() => expect(ticketApi.close).toHaveBeenCalledWith(ticketId, 'decision-id', 4, 'Внутренняя задача'))
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
      decision_text: 'Синтетический вердикт', reason: null, performed_actions: 'Проверка',
      created_at: base.created_at }],
  })
  renderDetail('operator', { ...session('operator'), permissions: ['tickets.read.all', 'tickets.edit.all'] })
  const panel = within(await screen.findByRole('tabpanel', { name: 'Работа с заявкой' }))
  expect(await panel.findByText(/Следующий шаг — закрытие без обзвона/)).toBeInTheDocument()
  expect(await panel.findByText('Синтетический вердикт')).toBeInTheDocument()
  expect(ticketApi.decision).toHaveBeenCalledWith(ticketId, 'verdict-id', expect.anything())
  fireEvent.click(screen.getByRole('tab', { name: 'История' }))
  expect(await screen.findByText('Синтетический вердикт')).toBeInTheDocument()
  expect(screen.getByText(/Инженер теста · Причина не указана/)).toBeInTheDocument()
  expect(screen.getByText(/Ранее указанные действия: Проверка/)).toBeInTheDocument()
  expect(panel.queryByRole('button', { name: /Взять в работу|Освободить заявку|Решено/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: 'Решение инженера' })).not.toBeInTheDocument()
})

it('shows the current decision to a reader even when the history page has other versions', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'RESOLVED', resolved_decision_id: 'new-decision', resolved_at: base.created_at })
  vi.mocked(ticketApi.decisions).mockResolvedValue({ ...empty, total: 21, items: [{
    id: 'old-decision', ticket_id: ticketId, engineer_id: 'engineer-id',
    decision_text: 'Старая версия', reason: null, performed_actions: null, created_at: base.created_at,
  }] })
  vi.mocked(ticketApi.decision).mockResolvedValue({
    id: 'new-decision', ticket_id: ticketId, engineer_id: 'engineer-id', engineer_name: 'Другой инженер',
    decision_text: 'Новая актуальная версия', reason: 'NETWORK_FAULT', performed_actions: null, created_at: base.created_at,
  })
  renderDetail('operator')
  const panel = within(await screen.findByRole('tabpanel', { name: 'Работа с заявкой' }))
  expect(await panel.findByText('Новая актуальная версия')).toBeInTheDocument()
  expect(panel.getByText(/Другой инженер/)).toBeInTheDocument()
  expect(panel.queryByText('Старая версия')).not.toBeInTheDocument()
  expect(ticketApi.decisions).not.toHaveBeenCalled()
  expect(panel.queryByRole('textbox', { name: 'Уточнить решение' })).not.toBeInTheDocument()
})

it('shows the closing decision on a closed ticket', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'CLOSED', closed_decision_id: 'closing-decision', closed_at: base.created_at })
  vi.mocked(ticketApi.decision).mockResolvedValue({
    id: 'closing-decision', ticket_id: ticketId, engineer_id: 'engineer-id', engineer_name: 'Инженер закрытия',
    decision_text: 'Работа завершена', reason: 'CUSTOMER_EQUIPMENT', performed_actions: null, created_at: base.created_at,
  })
  renderDetail('engineer')
  expect(await screen.findByText('Работа завершена')).toBeInTheDocument()
  expect(ticketApi.decision).toHaveBeenCalledWith(ticketId, 'closing-decision', expect.anything())
  expect(screen.queryByRole('heading', { name: 'Завершение заявки' })).not.toBeInTheDocument()
})

it('records a callback outcome only with an individual grant and preserves phones separately', async () => {
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base, ticket_type: 'SUBSCRIBER_LINKED', status: 'RESOLVED',
    account_no: '0001499', caller_phone: '+000 caller', resolved_at: base.created_at,
    resolved_by_name: 'Инженер теста', resolved_decision_id: 'verdict-id',
    subscriber: { full_name: 'Синтетический абонент', organization_name: null,
      phone: '+000 subscriber', contacts: [], actual_address: null,
      fetched_at: base.created_at, source: 'MOCK', services: [], connections: [] },
  })
  vi.mocked(ticketApi.decision).mockResolvedValue({
    id: 'verdict-id', ticket_id: ticketId, engineer_id: 'engineer-id',
    decision_text: 'Проверка завершена', reason: 'NETWORK_FAULT', performed_actions: null,
    created_at: base.created_at,
  })
  vi.mocked(ticketApi.recordCallback).mockResolvedValue({ ...base, status: 'RESOLVED' })
  const staff = session('operator')
  renderDetail('operator', { ...staff, permissions: [...staff.permissions, 'tickets.callback'] })
  expect(await screen.findByText('+000 caller')).toBeInTheDocument()
  expect(screen.getAllByText('+000 caller')).toHaveLength(1)
  expect(screen.getByText('Дополнительный контакт: +000 subscriber')).toBeInTheDocument()
  expect(await screen.findByText('Проверка завершена')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Контрольный звонок' })).toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: 'Уточнить решение' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'Комментарий обзвона' }), {
    target: { value: 'Синтетическая проблема осталась' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Проблема осталась' }))
  await waitFor(() => expect(ticketApi.recordCallback).toHaveBeenCalledWith(
    ticketId, 'STILL_BROKEN', 'Синтетическая проблема осталась', 3, expect.any(String),
  ))
  expect(screen.queryByRole('button', { name: 'Закрыть заявку' })).not.toBeInTheDocument()
})

it.each(['operator', 'admin'] as const)('uses grants rather than role name for %s', async (role) => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'IN_PROGRESS' })
  renderDetail('operator', { ...session('operator'), user: { ...session('operator').user, role } })
  const panel = within(await screen.findByRole('tabpanel', { name: 'Работа с заявкой' }))
  expect(panel.queryByText('Назначить инженера')).not.toBeInTheDocument()
  expect(panel.queryByText('Выездные работы')).not.toBeInTheDocument()
  for (const button of panel.queryAllByRole('button')) expect(button).not.toBeVisible()
  expect(panel.queryByText('Дополнительно')).not.toBeInTheDocument()
})

it.each([true, false])('applies delete.own to ownership, own=%s, including CLOSED tickets', async (own) => {
  vi.mocked(ticketApi.get).mockResolvedValue({ ...base, status: 'CLOSED', created_by: own ? base.created_by : 'another-author' })
  const staff = session('engineer')
  renderDetail('engineer', { ...staff, permissions: [...staff.permissions, 'tickets.delete.own'] })
  const panel = within(await screen.findByRole('tabpanel', { name: 'Работа с заявкой' }))
  for (const button of panel.queryAllByRole('button')) expect(button).not.toBeVisible()
  expect(panel.queryByText('Назначить инженера')).not.toBeInTheDocument()
  expect(panel.queryByText('Выездные работы')).not.toBeInTheDocument()
  expect(panel.queryByText('Дополнительно') !== null).toBe(own)
})

it('shows installer replies and lets an authorised engineer verify reported field work', async () => {
  const staff = session('engineer')
  const privileged: MeResponse = { ...staff, permissions: [...staff.permissions, 'tickets.field_work'] }
  vi.mocked(ticketApi.get).mockResolvedValue({
    ...base, status: 'IN_PROGRESS', assigned_engineer_id: staff.user.id,
  })
  const handoff = {
    id: '00000000-0000-4000-8000-000000000041', ticket_id: ticketId,
    reason: 'Проверить линию', delivery_status: 'SENT' as const,
    work_status: 'REPORTED' as const, chat_id: '-5319791765', telegram_message_id: 702,
    message_preview: 'Заявка #73', ready_to_send: false, missing_fields: [],
    created_by: staff.user.id, sent_by: staff.user.id, sent_by_name: staff.user.display_name,
    last_error_code: null, created_at: base.created_at, send_started_at: base.created_at,
    sent_at: base.created_at, reported_at: base.updated_at, verified_by: null,
    verified_by_name: null, verified_at: null, updated_at: base.updated_at,
  }
  vi.mocked(ticketApi.latestFieldWork).mockResolvedValue({ handoff })
  vi.mocked(ticketApi.fieldWorkMessages).mockResolvedValue({
    items: [{
      id: '00000000-0000-4000-8000-000000000042', handoff_id: handoff.id,
      kind: 'TEXT_REPLY', sender_name: 'Монтажник Тест', body: 'Работы выполнены.',
      telegram_created_at: base.updated_at, received_at: base.updated_at,
    }], page: 1, size: 50, total: 1,
  })
  vi.mocked(ticketApi.verifyFieldWork).mockResolvedValue({
    ...handoff, work_status: 'VERIFIED', verified_by: staff.user.id,
    verified_by_name: staff.user.display_name, verified_at: base.updated_at,
  })

  renderDetail('engineer', privileged)
  expect(await screen.findByRole('heading', { name: 'Выездные работы' })).toBeVisible()
  expect(await screen.findByText('Работы выполнены.')).toBeInTheDocument()
  expect(screen.getByText('Монтажники сообщили о выполнении')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить результат' }))
  await waitFor(() => expect(ticketApi.verifyFieldWork).toHaveBeenCalledWith(ticketId, handoff.id))
  expect(ticketApi.close).not.toHaveBeenCalled()
})
