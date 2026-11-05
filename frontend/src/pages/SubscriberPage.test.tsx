import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { SubscriberCard } from '../features/tickets/types'
import type { MeResponse } from '../shared/api/types'
import { SubscriberPage } from './SubscriberPage'

vi.mock('../features/tickets/ticket-api', () => ({
  ticketApi: { lookup: vi.fn(), getLookup: vi.fn(), subscriberTickets: vi.fn(), subscriberFinance: vi.fn(), subscriberServices: vi.fn(), subscriberTariffHistory: vi.fn(), checkSubscriber: vi.fn(), subscriberOnu: vi.fn() },
}))

const timestamp = '2026-01-01T00:00:00Z'
const stale = { source: 'NETUP' as const, observed_at: timestamp, quality: 'STALE' as const }
const unknown = { source: 'NETUP' as const, observed_at: timestamp, quality: 'UNKNOWN' as const }
const card: SubscriberCard = {
  lookup_id: '00000000-0000-4000-8000-000000000001', source: 'NETUP',
  fetched_at: timestamp, expires_at: '2999-01-01T00:00:00Z', is_expired: false,
  subscriber: {
    subscriber_id: 'synthetic-id', billing_account: 'synthetic-account',
    full_name: 'Синтетический абонент', organization_name: null, phone: null,
    contacts: [
      { type: 'mobile', value: 'synthetic-phone-a', source: 'NETUP.users.mobile_telephone', observed_at: timestamp, quality: 'CONFLICT' },
      { type: 'work', value: 'synthetic-phone-b', source: 'NETUP.users.work_telephone', observed_at: timestamp, quality: 'CONFLICT' },
    ],
    email: null, actual_address: 'г. Тест, ул. Примерная', house_number: '20',
    field_meta: { billing_account: stale, full_name: stale, actual_address: stale, house_number: stale, email: unknown, subscriber_id: stale, contracts: unknown },
  },
  accounts: [{ account_id: 'synthetic-account', is_primary: true, int_status: 1, is_blocked: 0, field_meta: { account_id: stale, int_status: stale, is_blocked: stale } }],
  financial_accounts: [{ account_id: 'synthetic-account', balance: '-1000', credit: '1000.01', currency_code: 810, currency: 'RUR', account_blocked: false, field_meta: { balance: stale, credit: stale, currency_code: stale, currency: stale, account_blocked: stale } }],
  contracts: [],
  services: [{ service_id: 'synthetic-service', name: 'Синтетическая услуга', account_id: 'synthetic-account', tariff_name: 'Синтетический тариф', billing_active: true, billing_blocked: false, field_meta: { tariff_name: stale, billing_active: stale } }],
  connections: [{
    connection_snapshot_id: '00000000-0000-4000-8000-000000000002', service_id: 'synthetic-service',
    access_type: 'UNKNOWN', device: null, port: null, vlan: null, onu_id: null, ip_group: 'synthetic-group',
    field_meta: { device: unknown, port: unknown, vlan: unknown, access_type: unknown, onu_id: unknown, ip_group: stale },
    ip_groups: [{ group_id: 'synthetic-group', field_meta: {}, rows: [{
      row_id: 'synthetic-row', ip_type_code: 4, configured_ipv4: '192.0.2.10', configured_prefix_length: 32, address_assignment: 'CONFIGURED', ip_raw: 3221225994, mask_raw: 4294967295,
      ip_ext_raw: null, mask_ext_raw: null, dhcp_pool_id: null,
      switch_id: null, device: null, port_id: null, vlan_id: null,
      field_meta: { ip_raw: unknown, configured_ipv4: stale, device: unknown, port_id: unknown, vlan_id: unknown },
    }] }],
  }],
  technical_state: 'UNKNOWN',
}

function session(role: 'operator' | 'engineer'): MeResponse {
  return {
    user: { id: '00000000-0000-4000-8000-000000000003', login: role, display_name: role, role, is_active: true, created_at: timestamp, updated_at: timestamp },
    permissions: role === 'operator' ? ['subscribers.read', 'subscribers.finance.read', 'tickets.create'] : ['subscribers.read', 'tickets.create', 'diagnostics.run'],
  }
}

function renderPage(role: 'operator' | 'engineer', initial = `/subscribers/${card.lookup_id}`) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[initial]}><Routes>
    <Route path="/subscribers" element={<SubscriberPage session={session(role)} />} />
    <Route path="/subscribers/:lookupId" element={<SubscriberPage session={session(role)} />} />
    <Route path="/tickets/new" element={<div>Форма связанной заявки</div>} />
  </Routes></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(ticketApi.lookup).mockReset()
  vi.mocked(ticketApi.getLookup).mockReset().mockResolvedValue(card)
  vi.mocked(ticketApi.subscriberTickets).mockReset().mockResolvedValue({ items: [], page: 1, size: 10, total: 0 })
  vi.mocked(ticketApi.subscriberFinance).mockReset().mockResolvedValue({
    summary: { cash: '20350.00', charges: '21350.00', credit: '10100.07', adjustments: '0.00', unclassified_negative: '0.00' },
    currency: 'RUR', items: [{ id: 1, at: timestamp, amount: '1250.01', currency: 'RUR', type: 'cash', method: 'Терминал', canceled: false, details: { method_id: 1 } }],
    total: 1, page: 1, size: 30, start: timestamp, end: timestamp,
  })
  vi.mocked(ticketApi.subscriberServices).mockReset().mockResolvedValue({
    source: 'NETUP', as_of: timestamp, items: [
      { link_id: 1, service_id: 10, name: 'Единый', type: 'ip_traffic', role: 'main_tariff', status: 'active', configured_cost: '1400.00', currency: 'RUR', period: null, starts_at: timestamp, ends_at: null, can_filter_charges: true },
      { link_id: 2, service_id: 20, name: 'Внешний IP', type: 'periodic', role: 'additional', status: 'active', configured_cost: '187.50', currency: 'RUR', period: null, starts_at: timestamp, ends_at: null, can_filter_charges: true },
    ],
  })
  vi.mocked(ticketApi.subscriberTariffHistory).mockReset().mockResolvedValue({
    available: true, truncated: false, reason: null,
    items: [{ name: 'Единый(арх)', starts_at: timestamp, ends_at: timestamp, configured_cost: null, source: 'NETUP.tariffs_history' }],
  })
  vi.mocked(ticketApi.checkSubscriber).mockReset()
  vi.mocked(ticketApi.subscriberOnu).mockReset().mockResolvedValue({
    source: 'BDCOM', retrieved_at: timestamp,
    availability: 'UNAVAILABLE', association: 'UNCONFIRMED', candidates: [],
    limitations: ['onu_source_not_configured'], time_basis: 'SOURCE_LOCAL_UNVERIFIED',
  })
})

it('shows a compact overview and preserves source time, contacts and linked action', async () => {
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  expect(screen.queryByText('Синтетический тариф')).not.toBeInTheDocument()
  expect(screen.getByText('synthetic-phone-a')).toBeInTheDocument()
  expect(screen.getByText('synthetic-phone-b')).toBeInTheDocument()
  expect(screen.getByText(/Подключений: 1/)).toBeInTheDocument()
  expect(screen.getByText('г. Тест, ул. Примерная, д. 20')).toBeInTheDocument()
  expect(screen.getByText('Интернет включён в NetUP')).toBeInTheDocument()
  expect(screen.queryByText(/Блокировка счёта|Системная блокировка не обнаружена/)).not.toBeInTheDocument()
  expect(screen.getByText('Текущий баланс · задолженность')).toBeInTheDocument()
  expect(screen.getByText('-1 000,00 RUR')).toBeInTheDocument()
  expect(screen.getByText('1 000,01 RUR')).toBeInTheDocument()
  expect(screen.getByText('0,01 RUR')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Создать заявку/ })).toHaveAttribute('href', `/tickets/new?lookup=${card.lookup_id}`)
  expect(ticketApi.subscriberTickets).toHaveBeenCalledWith(card.lookup_id, 1, expect.any(AbortSignal))
})

it.each([
  [0, null],
  [16, 'Тип блокировки не определён'],
  [48, 'Тип блокировки не определён'],
  [256, 'Системная блокировка'],
  [768, 'Системная блокировка'],
  [1280, 'Системная блокировка'],
  [1792, 'Системная блокировка'],
] as const)('shows confirmed blocking code %s without inventing its class', async (isBlocked, expected) => {
  vi.mocked(ticketApi.getLookup).mockResolvedValue({
    ...card,
    accounts: [{ ...card.accounts[0]!, is_blocked: isBlocked }],
  })
  renderPage('operator')
  expect(await screen.findByText('Интернет включён в NetUP')).toBeInTheDocument()
  if (expected) expect(screen.getByText(expected)).toBeInTheDocument()
  else expect(screen.queryByText(/блокировка|Причина отключения/i)).not.toBeInTheDocument()
  expect(screen.queryByText('Административная блокировка')).not.toBeInTheDocument()
  expect(screen.queryByText('Пользовательская блокировка')).not.toBeInTheDocument()
})

it.each([
  [1, stale, 'Интернет включён в NetUP'],
  [0, stale, 'Интернет отключён в NetUP'],
  [null, unknown, 'Не удалось определить'],
  [1, unknown, 'Не удалось определить'],
] as const)('shows billing internet status %s according to source quality', async (intStatus, meta, expected) => {
  vi.mocked(ticketApi.getLookup).mockResolvedValue({
    ...card,
    accounts: [{
      ...card.accounts[0]!, int_status: intStatus,
      field_meta: { ...card.accounts[0]!.field_meta, int_status: meta },
    }],
  })
  renderPage('operator')
  expect(await screen.findByText(expected)).toBeInTheDocument()
})

it('does not attribute disabled internet with code 0 to a system block', async () => {
  vi.mocked(ticketApi.getLookup).mockResolvedValue({
    ...card,
    accounts: [{ ...card.accounts[0]!, int_status: 0, is_blocked: 0 }],
    financial_accounts: [{ ...card.financial_accounts[0]!, balance: '700' }],
  })
  renderPage('operator')
  expect(await screen.findByText('Интернет отключён в NetUP')).toBeInTheDocument()
  expect(screen.getByText('Причина отключения не установлена')).toBeInTheDocument()
  expect(screen.queryByText('Системная блокировка')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('Данные NetUP и качество источника'))
  expect(screen.getByText('int_status')).toBeInTheDocument()
  expect(screen.getByText('is_blocked')).toBeInTheDocument()
  expect(screen.getByText('700,00 RUR')).toBeInTheDocument()
})

it('names a system block only when its NetUP code confirms it', async () => {
  vi.mocked(ticketApi.getLookup).mockResolvedValue({
    ...card,
    accounts: [{ ...card.accounts[0]!, int_status: 0, is_blocked: 256 }],
  })
  renderPage('operator')
  expect(await screen.findByText('Интернет отключён в NetUP')).toBeInTheDocument()
  expect(screen.getByText('Системная блокировка')).toBeInTheDocument()
  expect(screen.queryByText('Причина отключения не установлена')).not.toBeInTheDocument()
})

it('shows an unknown cause for disabled internet with unclassified codes', async () => {
  vi.mocked(ticketApi.getLookup).mockResolvedValue({
    ...card,
    accounts: [{ ...card.accounts[0]!, int_status: 0, is_blocked: 48 }],
  })
  renderPage('operator')
  expect(await screen.findByText('Причина отключения не установлена')).toBeInTheDocument()
  expect(screen.queryByText('Системная блокировка')).not.toBeInTheDocument()
})

it('keeps unknown blocking data out of the main summary', async () => {
  vi.mocked(ticketApi.getLookup).mockResolvedValue({
    ...card,
    accounts: [{
      ...card.accounts[0]!, is_blocked: null,
      field_meta: { ...card.accounts[0]!.field_meta, is_blocked: unknown },
    }],
  })
  renderPage('operator')
  expect(await screen.findByText('Интернет включён в NetUP')).toBeInTheDocument()
  expect(screen.queryByText('Системная блокировка')).not.toBeInTheDocument()
})

it('shows source time once without confirmed dots or repeated labels', async () => {
  vi.mocked(ticketApi.getLookup).mockResolvedValue({
    ...card,
    subscriber: { ...card.subscriber, field_meta: { ...card.subscriber.field_meta,
      full_name: { source: 'NETUP', observed_at: timestamp, quality: 'KNOWN' } } },
  })
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  expect(screen.queryByText('Подтверждено источником')).not.toBeInTheDocument()
  expect(screen.queryByRole('img', { name: /^NETUP ·/ })).not.toBeInTheDocument()
  expect(screen.getByText(/Данные источника:/)).toBeInTheDocument()
})

it('hides finance from engineer and searches without account number in browser URL', async () => {
  vi.mocked(ticketApi.lookup).mockResolvedValue(card)
  renderPage('engineer', '/subscribers')
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск по лицевому счёту' }), { target: { value: 'synthetic-account' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
  await waitFor(() => expect(ticketApi.lookup).toHaveBeenCalledWith('synthetic-account', expect.anything()))
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Платежи' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Финансы абонента')).not.toBeInTheDocument()
})

it('does not invent currency or calculate an unreliable available balance', async () => {
  vi.mocked(ticketApi.getLookup).mockResolvedValue({
    ...card,
    financial_accounts: [{
      ...card.financial_accounts[0]!, currency_code: null, currency: null,
      field_meta: { ...card.financial_accounts[0]!.field_meta, credit: unknown, currency: unknown },
    }],
  })
  renderPage('operator')
  expect(await screen.findByLabelText('Финансы абонента')).toBeInTheDocument()
  expect(screen.getByText('Валюта источника не подтверждена. Значения показаны без обозначения валюты.')).toBeInTheDocument()
  expect(screen.getByText('Не удалось рассчитать')).toBeInTheDocument()
  expect(screen.queryByText(/RUR/)).not.toBeInTheDocument()
})

it('runs monitoring only for engineer and shows unavailable data without a made-up state', async () => {
  vi.mocked(ticketApi.checkSubscriber).mockResolvedValue({
    connection_snapshot_id: card.connections[0]!.connection_snapshot_id,
    checked_at: timestamp, device_match: 'UNKNOWN', interface_match: 'UNKNOWN',
    observations: [], limitations: ['monitoring_mapping_missing'],
  })
  renderPage('engineer')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Подключения' }))
  fireEvent.click(screen.getByRole('button', { name: 'Проверить подключение' }))
  await waitFor(() => expect(ticketApi.checkSubscriber).toHaveBeenCalledWith(card.lookup_id, card.connections[0]!.connection_snapshot_id))
  expect(await screen.findByText('Нет подтверждённого сопоставления с мониторингом.')).toBeInTheDocument()
})

it('keeps multiple services and detailed IP rows accessible', async () => {
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  expect(screen.queryByText('Всего услуг: 2')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Услуги' }))
  expect(await screen.findByText('Внешний IP')).toBeInTheDocument()
  expect(screen.getByText('Основной тариф')).toBeInTheDocument()
  expect(screen.getByText('Дополнительные услуги')).toBeInTheDocument()
  expect(screen.getByText('1 400,00 RUR')).toBeInTheDocument()
  expect(screen.getByText('187,50 RUR')).toBeInTheDocument()
  expect(screen.getAllByText('Расчётный период не подтверждён')).toHaveLength(2)
  expect(screen.queryByText('Единый(арх)')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('История тарифов'))
  expect(await screen.findByText('Единый(арх)')).toBeInTheDocument()
  expect(screen.getByText('Не подтверждена')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Платежи' }))
  expect(await screen.findByText(/20.350,00 RUR/)).toBeInTheDocument()
  expect(screen.getByText(/10.100,07 RUR/)).toBeInTheDocument()
  expect(screen.getByText('Терминал')).toBeInTheDocument()
  expect(screen.queryByText('10.5')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Подключения' }))
  expect(screen.getByText('IP-группы и исходные записи')).toBeInTheDocument()
  fireEvent.click(screen.getByText('IP-группы и исходные записи'))
  expect(screen.getAllByText('192.0.2.10/32').length).toBeGreaterThan(0)
  fireEvent.click(screen.getByRole('button', { name: 'Обращения' }))
  expect(screen.getByText('Связанных обращений пока нет')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Создать обращение' })).toHaveAttribute('href', `/tickets/new?lookup=${card.lookup_id}`)
})

it('filters actual charges by selected service link without replacing configured price', async () => {
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Услуги' }))
  await screen.findByText('Внешний IP')
  fireEvent.click(screen.getAllByRole('button', { name: 'Показать фактические начисления' })[1]!)
  expect(screen.getByText('Начисления услуги «Внешний IP»')).toBeInTheDocument()
  await waitFor(() => expect(ticketApi.subscriberFinance).toHaveBeenCalledWith(
    card.lookup_id, expect.any(String), expect.any(String), 'charges', 1, expect.any(AbortSignal), 2,
  ))
  fireEvent.click(screen.getByRole('button', { name: 'Показать все' }))
  await waitFor(() => expect(ticketApi.subscriberFinance).toHaveBeenCalledWith(
    card.lookup_id, expect.any(String), expect.any(String), 'charges', 1, expect.any(AbortSignal), null,
  ))
})

it('does not fetch configured costs or history without finance permission', async () => {
  renderPage('engineer')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Услуги' }))
  expect(screen.getByText('Синтетическая услуга')).toBeInTheDocument()
  expect(screen.queryByText('1 400,00 RUR')).not.toBeInTheDocument()
  expect(ticketApi.subscriberServices).not.toHaveBeenCalled()
  expect(ticketApi.subscriberTariffHistory).not.toHaveBeenCalled()
})

it('keeps source errors visible and does not invent a service price', async () => {
  vi.mocked(ticketApi.subscriberServices).mockRejectedValue(new Error('NetUP unavailable'))
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Услуги' }))
  expect(await screen.findByText(/Не удалось получить услуги NetUP/)).toBeInTheDocument()
  expect(screen.queryByText('1 400,00 RUR')).not.toBeInTheDocument()
})

it('shows an explicit unavailable history result without guessed prices', async () => {
  vi.mocked(ticketApi.subscriberTariffHistory).mockResolvedValue({ available: false, reason: 'history_scan_too_large', items: [] })
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Услуги' }))
  await screen.findByText('Внешний IP')
  fireEvent.click(screen.getByText('История тарифов'))
  expect(await screen.findByText(/Историю нельзя безопасно прочитать/)).toBeInTheDocument()
})

it('loads finance only after opening the tab and applies server filters', async () => {
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  expect(ticketApi.subscriberFinance).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Платежи' }))
  await waitFor(() => expect(ticketApi.subscriberFinance).toHaveBeenCalledWith(
    card.lookup_id, expect.any(String), expect.any(String), 'payments', 1, expect.anything(), null,
  ))
  fireEvent.change(screen.getByLabelText('Операции'), { target: { value: 'credit' } })
  await waitFor(() => expect(ticketApi.subscriberFinance).toHaveBeenCalledWith(
    card.lookup_id, expect.any(String), expect.any(String), 'credit', 1, expect.anything(), null,
  ))
  fireEvent.change(screen.getByLabelText('Период'), { target: { value: 'custom' } })
  fireEvent.change(screen.getByLabelText('С даты'), { target: { value: '2026-01-01' } })
  fireEvent.change(screen.getByLabelText('По дату'), { target: { value: '2026-01-31' } })
  await waitFor(() => expect(ticketApi.subscriberFinance).toHaveBeenCalledWith(
    card.lookup_id, new Date('2026-01-01T00:00:00').toISOString(),
    new Date('2026-02-01T00:00:00').toISOString(), 'credit', 1, expect.anything(), null,
  ))
})

it('shows a finance error and does not invent zero values', async () => {
  vi.mocked(ticketApi.subscriberFinance).mockRejectedValue(new Error('История временно недоступна'))
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Платежи' }))
  expect(await screen.findByText(/Не удалось связаться с сервером/)).toBeInTheDocument()
  expect(screen.queryByText(/Денежные поступления/)).not.toBeInTheDocument()
})

it('paginates financial history on the server', async () => {
  vi.mocked(ticketApi.subscriberFinance).mockImplementation(async (_id, start, end, _kind, page) => ({
    summary: { cash: '1.00', charges: '0.00', credit: '0.00', adjustments: '0.00', unclassified_negative: '0.00' },
    currency: 'RUR', items: [{ id: page, at: timestamp, amount: '1.00', currency: 'RUR', type: 'cash', method: 'Терминал', details: { method_id: 1 } }],
    total: 31, page, size: 30, start, end,
  }))
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Платежи' }))
  expect(await screen.findByText('1 / 2')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Далее' }))
  expect(await screen.findByText('2 / 2')).toBeInTheDocument()
  expect(ticketApi.subscriberFinance).toHaveBeenLastCalledWith(
    card.lookup_id, expect.any(String), expect.any(String), 'payments', 2, expect.anything(), null,
  )
})

it('keeps credit totals separate and expands row details with keyboard', async () => {
  vi.mocked(ticketApi.subscriberFinance).mockImplementation(async (_id, start, end, kind) => ({
    summary: { cash: '20350.00', charges: '21350.00', credit: '10100.07', adjustments: '0.00', unclassified_negative: '0.00' },
    currency: 'RUR', items: kind === 'credit' ? [{ id: 7, at: timestamp, amount: '1000.01', currency: 'RUR', type: 'credit', method: 'Credit', transaction_amount: '1000.01', transaction_currency: 'RUR', details: { method_id: 7 } }] : [],
    total: kind === 'credit' ? 1 : 0, page: 1, size: 30, start, end,
  }))
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Платежи' }))
  fireEvent.change(screen.getByLabelText('Операции'), { target: { value: 'credit' } })
  expect(await screen.findByText('Изменение кредитного лимита')).toBeInTheDocument()
  expect(screen.getByText('Итоги за выбранный период')).toBeInTheDocument()
  expect(screen.getByText('Фильтр операций влияет только на список')).toBeInTheDocument()
  expect(screen.getByText(/20.350,00 RUR/)).toBeInTheDocument()
  expect(screen.getByText(/10.100,07 RUR/)).toBeInTheDocument()
  expect(screen.queryByRole('columnheader', { name: 'Подробнее' })).not.toBeInTheDocument()
  expect(screen.queryByRole('cell', { name: 'Credit' })).not.toBeInTheDocument()
  const toggle = screen.getByRole('button', { name: /Подробности операции/ })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  const details = document.getElementById(toggle.getAttribute('aria-controls')!)!
  expect(details).not.toBeVisible()
  toggle.focus()
  await userEvent.setup().keyboard('{Enter}')
  expect(toggle).toHaveAttribute('aria-expanded', 'true')
  expect(details).toBeVisible()
  expect(screen.getByText('Метод NetUP: Credit')).toBeVisible()
  await userEvent.setup().keyboard('{Enter}')
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
})

it('shows cancellation clearly and retains the time caveat in a compact disclosure', async () => {
  vi.mocked(ticketApi.subscriberFinance).mockResolvedValue({
    summary: { cash: '0.00', charges: '0.00', credit: '0.00', adjustments: '0.00', unclassified_negative: '0.00' },
    currency: 'RUR', items: [{ id: 8, at: timestamp, amount: '-500.00', currency: 'RUR', type: 'canceled', method: 'Сторно', canceled: true, details: { cancel_id: 7 } }],
    total: 1, page: 1, size: 30, start: timestamp, end: timestamp,
  })
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Платежи' }))
  expect(await screen.findByText('Отмена / сторно')).toBeInTheDocument()
  expect(screen.getByText('-500,00 RUR')).toHaveClass('subscriber-finance-canceled')
  fireEvent.click(screen.getByText('Отмена / сторно'))
  expect(screen.getByText('Отменена или сторнирована')).toBeVisible()
  const summary = screen.getByText('О времени операций')
  expect(summary).toBeVisible()
  expect(screen.getByText(/Время биллинга пока не подтверждено/)).not.toBeVisible()
  fireEvent.click(summary)
  expect(screen.getByText(/Время биллинга пока не подтверждено/)).toBeVisible()
})

it('shows an empty finance list without losing the period totals', async () => {
  vi.mocked(ticketApi.subscriberFinance).mockResolvedValue({
    summary: { cash: '5.00', charges: '0.00', credit: '0.00', adjustments: '0.00', unclassified_negative: '0.00' },
    currency: 'RUR', items: [], total: 0, page: 1, size: 30, start: timestamp, end: timestamp,
  })
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Платежи' }))
  expect(await screen.findByText('За выбранный период операций этого типа нет.')).toBeInTheDocument()
  expect(screen.getByText('5,00 RUR')).toBeInTheDocument()
})
