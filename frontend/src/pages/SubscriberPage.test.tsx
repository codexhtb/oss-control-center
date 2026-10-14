import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { ticketApi } from '../features/tickets/ticket-api'
import type { SubscriberCard } from '../features/tickets/types'
import type { MeResponse } from '../shared/api/types'
import { SubscriberPage } from './SubscriberPage'

vi.mock('../features/tickets/ticket-api', () => ({
  ticketApi: { lookup: vi.fn(), getLookup: vi.fn(), subscriberTickets: vi.fn() },
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
    email: null, actual_address: null,
    field_meta: { billing_account: stale, full_name: stale, actual_address: unknown, email: unknown, subscriber_id: stale, contracts: unknown },
  },
  accounts: [{ account_id: 'synthetic-account', is_primary: true, field_meta: { account_id: stale } }],
  financial_accounts: [{ account_id: 'synthetic-account', balance: '10.5', credit: '0', account_blocked: false, field_meta: { balance: stale, credit: stale, account_blocked: stale } }],
  contracts: [],
  services: [{ service_id: 'synthetic-service', name: 'Синтетическая услуга', account_id: 'synthetic-account', tariff_name: 'Синтетический тариф', billing_active: true, billing_blocked: false, field_meta: { tariff_name: stale, billing_active: stale } }],
  connections: [{
    connection_snapshot_id: '00000000-0000-4000-8000-000000000002', service_id: 'synthetic-service',
    access_type: 'UNKNOWN', device: null, port: null, vlan: null, onu_id: null, ip_group: 'synthetic-group',
    field_meta: { device: unknown, port: unknown, vlan: unknown, access_type: unknown, onu_id: unknown, ip_group: stale },
    ip_groups: [{ group_id: 'synthetic-group', field_meta: {}, rows: [{
      row_id: 'synthetic-row', ip_type_code: 4, ip_raw: 12345, mask_raw: 65535,
      ip_ext_raw: null, mask_ext_raw: null, dhcp_pool_id: null,
      switch_id: null, device: null, port_id: null, vlan_id: null,
      field_meta: { ip_raw: unknown, device: unknown, port_id: unknown, vlan_id: unknown },
    }] }],
  }],
  technical_state: 'UNKNOWN',
}

function session(role: 'operator' | 'engineer'): MeResponse {
  return {
    user: { id: '00000000-0000-4000-8000-000000000003', login: role, display_name: role, role, is_active: true, created_at: timestamp, updated_at: timestamp },
    permissions: role === 'operator' ? ['subscriber.read', 'subscriber.finance.read', 'ticket.create'] : ['subscriber.read', 'ticket.create'],
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
})

it('renders source quality, multiple contacts, tariff, IP rows, finance and linked action', async () => {
  renderPage('operator')
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  expect(screen.getByText(/Тариф: Синтетический тариф/)).toBeInTheDocument()
  expect(screen.getByText('synthetic-phone-a')).toBeInTheDocument()
  expect(screen.getByText('synthetic-phone-b')).toBeInTheDocument()
  expect(screen.getByText('Код 12345')).toBeInTheDocument()
  expect(screen.getByText('10.5')).toBeInTheDocument()
  expect(screen.getByText('Номер договора UNKNOWN')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Создать заявку/ })).toHaveAttribute('href', `/tickets/new?lookup=${card.lookup_id}`)
  expect(ticketApi.subscriberTickets).toHaveBeenCalledWith(card.lookup_id, 1, expect.any(AbortSignal))
})

it('hides finance from engineer and searches without account number in browser URL', async () => {
  vi.mocked(ticketApi.lookup).mockResolvedValue(card)
  renderPage('engineer', '/subscribers')
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск по лицевому счёту' }), { target: { value: 'synthetic-account' } })
  fireEvent.click(screen.getByRole('button', { name: 'Найти' }))
  await waitFor(() => expect(ticketApi.lookup).toHaveBeenCalledWith('synthetic-account', expect.anything()))
  expect(await screen.findByRole('heading', { name: 'Синтетический абонент' })).toBeInTheDocument()
  expect(screen.queryByText('Финансовые сведения')).not.toBeInTheDocument()
  expect(screen.queryByText('10.5')).not.toBeInTheDocument()
})
