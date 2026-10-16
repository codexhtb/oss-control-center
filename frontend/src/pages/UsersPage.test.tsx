import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import { apiRequest } from '../shared/api/client'
import { UsersPage } from './UsersPage'

vi.mock('../shared/api/client', () => ({ apiRequest: vi.fn(), errorText: () => 'Ошибка запроса' }))

const worker = {
  id: '00000000-0000-4000-8000-000000000002', login: 'worker', display_name: 'Сотрудник',
  role: 'operator', is_active: true, must_change_password: false,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  permissions: ['tickets.read.own'],
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><UsersPage /></MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(apiRequest).mockReset()
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    if (path === '/api/v1/ticket-categories?include_inactive=true') return []
    if (path.startsWith('/api/v1/users?page=')) return { items: [worker], page: 1, size: 20, total: 1 }
    if (path === '/api/v1/users/audit?size=20') return { items: [], page: 1, size: 20, total: 0 }
    if (path === '/api/v1/users') return { ...worker, temporary_password: 'synthetic-only-temporary' }
    return worker
  })
})

it('creates a user through HTTP and displays the temporary password once', async () => {
  renderPage()
  expect(await screen.findByText('Сотрудник')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }))
  fireEvent.change(screen.getByLabelText('Логин'), { target: { value: 'new.worker' } })
  fireEvent.change(screen.getByLabelText('Имя сотрудника'), { target: { value: 'Новый сотрудник' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать' }))
  expect(await screen.findByText('synthetic-only-temporary')).toBeInTheDocument()
  const call = vi.mocked(apiRequest).mock.calls.find(([path]) => path === '/api/v1/users')
  expect(call?.[1]).toMatchObject({ method: 'POST', json: { login: 'new.worker', display_name: 'Новый сотрудник' } })
  expect(JSON.stringify(call?.[1])).not.toContain('password')
  fireEvent.click(screen.getByRole('button', { name: 'Скрыть пароль' }))
  expect(screen.queryByText('synthetic-only-temporary')).not.toBeInTheDocument()
})

it('sends a replacement grant set for the selected employee', async () => {
  renderPage()
  fireEvent.click(await screen.findByRole('button', { name: 'Сотрудник' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'Создание' }))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить разрешения' }))
  await waitFor(() => expect(vi.mocked(apiRequest).mock.calls.some(([path, options]) =>
    path === `/api/v1/users/${worker.id}/permissions` && options?.method === 'PUT' &&
    Array.isArray((options.json as { permissions: string[] }).permissions) &&
    (options.json as { permissions: string[] }).permissions.includes('tickets.create'),
  )).toBe(true))
})

it('manages the category catalog through the existing admin page', async () => {
  const item = { id: '00000000-0000-4000-8000-000000000099', name: 'Нет интернета',
    sort_order: 10, is_active: true, is_other: false, version: 1 }
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    if (path === '/api/v1/ticket-categories?include_inactive=true') return [item]
    if (path.startsWith('/api/v1/users?page=')) return { items: [worker], page: 1, size: 20, total: 1 }
    return item
  })
  renderPage()
  expect(await screen.findByDisplayValue('Нет интернета')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'Название категории Нет интернета' }), { target: { value: 'Нет связи' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(vi.mocked(apiRequest).mock.calls.some(([path, options]) =>
    path === `/api/v1/ticket-categories/${item.id}` && options?.method === 'PATCH' &&
    options.ifMatch === '"1"' && (options.json as { name: string }).name === 'Нет связи',
  )).toBe(true))
})
