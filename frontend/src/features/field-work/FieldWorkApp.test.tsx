import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FieldWorkApp } from './FieldWorkApp'

const id = '00000000-0000-4000-8000-000000000101'
const partnerId = '00000000-0000-4000-8000-000000000102'
const handoff = {
  id, ticket_id: '00000000-0000-4000-8000-000000000103', ticket_number: 201,
  direction: 'SUKHUM', work_status: 'AWAITING_REPORT', subject: 'Нет связи',
  sent_at: '2026-11-17T07:00:00Z', reported_at: null, verified_at: null,
  scheduled_for: null, scheduled_date: null, schedule_status: 'ORIGINAL',
}

beforeEach(() => {
  Object.defineProperty(window, 'Telegram', { configurable: true, value: { WebApp: {
    initData: 'signed-test-init-data', colorScheme: 'light',
    ready: vi.fn(), expand: vi.fn(), onEvent: vi.fn(), offEvent: vi.fn(),
  } } })
})
afterEach(() => { vi.unstubAllGlobals(); Reflect.deleteProperty(window, 'Telegram') })

it('loads the shared queue, detail and submits one structured report', async () => {
  let reported = false
  const requests: { path: string; options?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string, options?: RequestInit) => {
    requests.push({ path, options })
    let body: unknown
    if (path.endsWith('/me')) body = { name: 'Монтажник', directions: ['SUKHUM'] }
    else if (path.includes('/partners')) body = { items: [{ id: partnerId, name: 'Напарник', kind: 'installer' }] }
    else if (path.includes('/reschedules')) body = { items: [], total: 0, page: 1, page_size: 20 }
    else if (path.endsWith('/report')) { reported = true; body = { handoff_id: id, work_status: 'REPORTED', report_id: 'report' } }
    else if (path.endsWith(`/${id}`)) body = { ...handoff, reason: 'Проверить линию',
      name: 'Абонент', phone: '+7 900 111 22 33', city: 'Сухум', address: 'Тестовая улица 1',
      account_no: null, schedule_version: 0, schedule_reason: null, schedule_author: null, report: null }
    else body = { items: reported ? [] : [handoff], total: reported ? 0 : 1, page: 1, page_size: 20 }
    return { ok: true, status: 200, json: async () => body } as Response
  }))
  render(<FieldWorkApp />)
  expect(await screen.findByText('Заявка №201')).toBeInTheDocument()
  expect(document.querySelector('.fw-app')).toHaveAttribute('data-theme', 'light')
  fireEvent.click(screen.getByRole('button', { name: /Заявка №201/ }))
  expect(await screen.findByText('Проверить линию')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Позвонить/ })).toHaveAttribute('href', 'tel:+79001112233')
  fireEvent.click(screen.getByRole('button', { name: /Выполнить работу/ }))
  fireEvent.change(screen.getByLabelText(/Работали вместе/), { target: { value: partnerId } })
  fireEvent.change(screen.getByLabelText('Что было сделано'), { target: { value: 'Линия восстановлена' } })
  fireEvent.click(screen.getByRole('button', { name: 'Отправить отчёт' }))
  await waitFor(() => expect(requests.some((item) => item.path.endsWith('/report'))).toBe(true))
  const report = requests.find((item) => item.path.endsWith('/report'))
  expect(report?.options?.headers).toMatchObject({ 'X-Telegram-Init-Data': 'signed-test-init-data' })
  expect(JSON.parse(report?.options?.body as string)).toMatchObject({
    partner_id: partnerId, partner_kind: 'installer', work_text: 'Линия восстановлена',
  })
  expect(report?.options?.credentials).toBe('omit')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Завершённые' })).toHaveClass('active'))
})

it('blocks access outside Telegram without calling the queue API', async () => {
  Reflect.deleteProperty(window, 'Telegram')
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  const originalAppend = document.head.append.bind(document.head)
  vi.spyOn(document.head, 'append').mockImplementation((...nodes: (Node | string)[]) => {
    for (const node of nodes) {
      if (node instanceof HTMLScriptElement) queueMicrotask(() => node.dispatchEvent(new Event('load')))
    }
    return originalAppend(...nodes)
  })
  render(<FieldWorkApp />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Откройте OSS Field Work через Telegram')
  expect(fetch).not.toHaveBeenCalled()
  vi.restoreAllMocks()
})

it('lets an unknown Telegram user request installer access without automatic approval', async () => {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    requests.push(path)
    if (path.endsWith('/me') || path.endsWith('/staff/me')) {
      return { ok: false, status: 403, json: async () => ({ code: 'installer_forbidden' }) } as Response
    }
    return { ok: true, status: 201, json: async () => ({ id: 'request', status: 'PENDING' }) } as Response
  }))
  render(<FieldWorkApp />)
  expect(await screen.findByText('Доступ не предоставлен')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Запросить доступ' }))
  expect(await screen.findByText('Запрос отправлен')).toBeInTheDocument()
  expect(requests.some((path) => path.endsWith('/access-request'))).toBe(true)
})

it('links verified OCC staff and allows verification without installer report actions', async () => {
  let linked = false
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    requests.push(path)
    if (path.endsWith('/me') && !path.endsWith('/staff/me')) return { ok: false, status: 403, json: async () => ({ code: 'installer_forbidden' }) } as Response
    if (path.endsWith('/staff/me') && !linked) return { ok: false, status: 403, json: async () => ({ code: 'staff_forbidden' }) } as Response
    let body: unknown
    if (path.endsWith('/link-staff')) { linked = true; body = { name: 'Инженер', directions: ['SUKHUM'], kind: 'staff' } }
    else if (path.endsWith('/staff/me')) body = { name: 'Инженер', directions: ['SUKHUM'], can_verify: true, can_perform: false, role: 'engineer' }
    else if (path.includes('/reschedules')) body = { items: [], total: 0, page: 1, page_size: 20 }
    else if (path.endsWith('/verify')) body = { handoff_id: id, work_status: 'VERIFIED' }
    else if (path.endsWith(`/${id}`)) body = { ...handoff, work_status: 'REPORTED', reason: 'Проверить линию', name: 'Абонент', phone: null, city: 'Сухум', address: null, account_no: null, schedule_version: 0, schedule_reason: null, schedule_author: null, report: { text: 'Линия восстановлена', installer: 'Монтажник', partner: null, reported_at: '2026-11-17T08:00:00Z' } }
    else body = { items: [{ ...handoff, work_status: 'REPORTED' }], total: 1, page: 1, page_size: 20 }
    return { ok: true, status: 200, json: async () => body } as Response
  }))
  render(<FieldWorkApp />)
  expect(await screen.findByText('Доступ не предоставлен')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Я сотрудник OCC' }))
  fireEvent.change(screen.getByLabelText('Код привязки'), { target: { value: 'synthetic-code-123456' } })
  fireEvent.click(screen.getByRole('button', { name: 'Привязать Telegram' }))
  expect(await screen.findByText('Заявка №201')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Заявка №201/ }))
  expect(await screen.findByText('Линия восстановлена')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Завершить работу/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить работу' }))
  await waitFor(() => expect(requests.some((path) => path.endsWith('/staff/handoffs/' + id + '/verify'))).toBe(true))
})
