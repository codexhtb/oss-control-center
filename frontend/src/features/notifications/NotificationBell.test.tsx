import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import type { MeResponse } from '../../shared/api/types'
import { NotificationBell } from './NotificationBell'
import { notificationApi, type CenterNotification, type CenterPage } from './notification-api'

vi.mock('./notification-api', () => ({ notificationApi: { center: vi.fn(), readCenter: vi.fn(), readAll: vi.fn() } }))

const engineer: MeResponse = {
  user: {
    id: '00000000-0000-4000-8000-000000000001', login: 'engineer',
    display_name: 'Инженер', role: 'engineer', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  },
  permissions: ['tickets.read.all', 'tickets.field_work', 'tasks.create'],
}
const task: CenterNotification = {
  id: 'task-notice', source: 'task', ticket_id: 'ticket-2', task_id: 'task-2', ticket_number: 67,
  title: 'Позвонить абоненту', kind: 'COMPLETED', created_at: '2026-11-03T11:00:00Z', read_at: null,
}
const field: CenterNotification = {
  id: 'field-notice', source: 'field_report', ticket_id: 'ticket-1', task_id: null, ticket_number: 66,
  title: 'Отчёт: Монтажник Тест', kind: 'FIELD_REPORT', created_at: '2026-11-03T10:00:00Z', read_at: null,
}
let notices: CenterNotification[]
function response(state: 'unread' | 'read', page = 1): CenterPage {
  const items = notices.filter((item) => state === 'unread' ? !item.read_at : !!item.read_at)
  return { items: items.slice((page - 1) * 20, page * 20), page, size: 20,
    total: items.length, unread_count: notices.filter((item) => !item.read_at).length }
}
function Destination() {
  const location = useLocation()
  return <span data-testid="destination">{location.pathname}{location.search}</span>
}
function mount(session = engineer, start = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } } })
  const rendered = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[start]}>
    <NotificationBell session={session} />
    <Routes><Route path="*" element={<Destination />} /></Routes>
  </MemoryRouter></QueryClientProvider>)
  return { client, ...rendered }
}
async function open(count = 2) {
  fireEvent.click(await screen.findByRole('button', { name: `Уведомления, непрочитанных: ${count}` }))
}

beforeEach(() => {
  notices = [{ ...task }, { ...field }]
  vi.mocked(notificationApi.center).mockReset().mockImplementation(async (state, page) => response(state, page))
  vi.mocked(notificationApi.readCenter).mockReset().mockImplementation(async (source, id) => {
    notices = notices.map((item) => item.id === id && item.source === source ? { ...item, read_at: '2026-11-24T10:00:00Z' } : item)
  })
  vi.mocked(notificationApi.readAll).mockReset().mockImplementation(async () => {
    notices = notices.map((item) => ({ ...item, read_at: item.read_at ?? '2026-11-24T10:00:00Z' }))
  })
})

it('shows one chronological list and navigates to a task after persisting its read', async () => {
  mount(engineer, '/tickets/another-ticket?task=another-task')
  await open()
  expect(screen.getAllByRole('button', { name: /Заявка #/ })[0]).toHaveTextContent('Поручение завершено')
  fireEvent.click(screen.getByRole('button', { name: /Поручение завершено/ }))
  await waitFor(() => expect(notificationApi.readCenter).toHaveBeenCalledWith('task', 'task-notice'))
  await waitFor(() => expect(screen.getByTestId('destination')).toHaveTextContent('/tickets/ticket-2?task=task-2'))
  await open(1)
  expect(screen.queryByText('Поручение завершено')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: 'История' }))
  expect(await screen.findByText('Поручение завершено')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Поручение завершено/ }))
  expect(notificationApi.readCenter).toHaveBeenCalledTimes(1)
})

it('marks field reports read before opening the associated ticket', async () => {
  mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: /Отчёт монтажника/ }))
  await waitFor(() => expect(notificationApi.readCenter).toHaveBeenCalledWith('field_report', 'field-notice'))
  await waitFor(() => expect(screen.getByTestId('destination')).toHaveTextContent('/tickets/ticket-1'))
  expect(screen.getByRole('button', { name: 'Уведомления, непрочитанных: 1' })).toBeInTheDocument()
})

it('reads both sources in one action, retains history and stays read across reload', async () => {
  const first = mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Прочитать все' }))
  expect(await screen.findByText('Непрочитанных уведомлений нет.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Уведомления, непрочитанных: 0' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Прочитать все' })).toBeDisabled()
  fireEvent.click(screen.getByRole('tab', { name: 'История' }))
  expect(await screen.findByText('Поручение завершено')).toBeInTheDocument()
  expect(screen.getByText('Отчёт монтажника')).toBeInTheDocument()
  first.unmount()
  mount()
  await open(0)
  expect(screen.getByText('Непрочитанных уведомлений нет.')).toBeInTheDocument()
})

it('retains unread notice and location when persisting read fails', async () => {
  vi.mocked(notificationApi.readCenter).mockRejectedValue(new Error('Offline'))
  mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: /Поручение завершено/ }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось отметить прочтение')
  expect(screen.getByRole('button', { name: 'Уведомления, непрочитанных: 2' })).toBeInTheDocument()
  expect(screen.getByText('Поручение завершено')).toBeInTheDocument()
  expect(screen.getByTestId('destination').textContent).toBe('/')
})

it('does not clear counts or history when bulk reading fails', async () => {
  vi.mocked(notificationApi.readAll).mockRejectedValue(new Error('Offline'))
  mount()
  await open()
  fireEvent.click(screen.getByRole('button', { name: 'Прочитать все' }))
  expect(await screen.findByRole('alert')).toBeInTheDocument()
  expect(screen.getAllByRole('button', { name: /Заявка #/ })).toHaveLength(2)
  expect(screen.getByRole('button', { name: 'Уведомления, непрочитанных: 2' })).toBeInTheDocument()
})

it('cancels stale polling so an old response cannot resurrect a read notice', async () => {
  const { client } = mount()
  await open()
  let resolvePoll!: (page: CenterPage) => void
  let pollSignal: AbortSignal | undefined
  const stalePage = response('unread')
  vi.mocked(notificationApi.center).mockImplementationOnce((_state, _page, signal) => {
    pollSignal = signal
    return new Promise((resolve) => { resolvePoll = resolve })
  })
  void client.invalidateQueries({ queryKey: ['notification-center'] })
  await waitFor(() => expect(resolvePoll).toBeDefined())
  fireEvent.click(screen.getByRole('button', { name: /Поручение завершено/ }))
  await waitFor(() => expect(screen.getByTestId('destination')).toHaveTextContent('/tickets/ticket-2?task=task-2'))
  expect(pollSignal?.aborted).toBe(true)
  await act(async () => { resolvePoll(stalePage) })
  await open(1)
  expect(screen.queryByText('Поручение завершено')).not.toBeInTheDocument()
  await act(async () => { await client.invalidateQueries({ queryKey: ['notification-center'] }) })
  expect(screen.queryByText('Поручение завершено')).not.toBeInTheDocument()
})

it('refreshes the count when another tab reads and when a new event arrives', async () => {
  const { client } = mount()
  await open()
  notices = notices.map((item) => ({ ...item, read_at: '2026-11-24T10:00:00Z' }))
  await act(async () => { await client.invalidateQueries({ queryKey: ['notification-center'] }) })
  expect(await screen.findByText('Непрочитанных уведомлений нет.')).toBeInTheDocument()
  notices.push({ ...task, id: 'new-notice' })
  await act(async () => { await client.invalidateQueries({ queryKey: ['notification-center'] }) })
  expect(await screen.findByRole('button', { name: 'Уведомления, непрочитанных: 1' })).toBeInTheDocument()
  await waitFor(() => expect(screen.getAllByRole('button', { name: /Заявка #/ })).toHaveLength(1))
})

it('paginates without truncating counts and excludes read entries from the main list', async () => {
  notices = Array.from({ length: 23 }, (_, index) => ({ ...task, id: `notice-${index}` }))
  mount()
  await open(23)
  expect(screen.getAllByRole('button', { name: /Заявка #/ })).toHaveLength(20)
  fireEvent.click(screen.getByRole('button', { name: 'Далее' }))
  await waitFor(() => expect(screen.getAllByRole('button', { name: /Заявка #/ })).toHaveLength(3))
  expect(screen.getByRole('button', { name: 'Уведомления, непрочитанных: 23' })).toBeInTheDocument()
})

it('does not request notifications without access and allows admin with saved permissions', async () => {
  const view = mount({ ...engineer, user: { ...engineer.user, role: 'operator' }, permissions: ['tickets.read.all'] })
  expect(screen.queryByRole('button', { name: /Уведомления/ })).not.toBeInTheDocument()
  expect(notificationApi.center).not.toHaveBeenCalled()
  view.unmount()
  mount({ ...engineer, user: { ...engineer.user, role: 'admin' }, permissions: ['tickets.read.all', 'tickets.field_work'] })
  await open()
  expect(screen.getByRole('tab', { name: 'Непрочитанные' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('region', { name: 'Уведомления' })).not.toBeInTheDocument()
})
