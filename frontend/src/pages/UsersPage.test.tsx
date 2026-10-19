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
    if (path === '/api/v1/users') return { ...worker, temporary_password: null }
    return worker
  })
})

it('creates a user with an administrator-supplied one-character password', async () => {
  renderPage()
  expect(await screen.findByText('Сотрудник')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }))
  fireEvent.change(screen.getByLabelText('Логин'), { target: { value: 'new.worker' } })
  fireEvent.change(screen.getByLabelText('Имя сотрудника'), { target: { value: 'Новый сотрудник' } })
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: '1' } })
  fireEvent.change(screen.getByLabelText('Подтвердите пароль'), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать' }))
  expect(await screen.findByText('Пользователь создан.')).toBeInTheDocument()
  const call = vi.mocked(apiRequest).mock.calls.find(([path]) => path === '/api/v1/users')
  expect(call?.[1]).toMatchObject({ method: 'POST', json: {
    login: 'new.worker', display_name: 'Новый сотрудник', password: '1', must_change_password: false,
  } })
})

it('changes an employee password with confirmation and optional first-login change', async () => {
  renderPage()
  const employee = await screen.findByRole('button', { name: 'Сотрудник' })
  expect(employee).toHaveClass('employee-name')
  expect(screen.getByText('С', { selector: '.employee-name__avatar' })).toBeInTheDocument()
  fireEvent.click(employee)
  fireEvent.click(screen.getByRole('button', { name: 'Изменить пароль' }))
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: '1' } })
  fireEvent.change(screen.getByLabelText('Подтвердите пароль'), { target: { value: '1' } })
  expect(screen.getByLabelText('Пароль')).toHaveAttribute('type', 'password')
  fireEvent.click(screen.getByRole('button', { name: 'Показать: Пароль' }))
  expect(screen.getByLabelText('Пароль')).toHaveAttribute('type', 'text')
  fireEvent.click(screen.getByRole('checkbox', { name: /Потребовать смену/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить пароль' }))
  await waitFor(() => expect(vi.mocked(apiRequest).mock.calls.some(([path, options]) =>
    path === `/api/v1/users/${worker.id}/password` && options?.method === 'PUT' &&
    (options.json as { new_password: string }).new_password === '1' &&
    (options.json as { must_change_password: boolean }).must_change_password === true,
  )).toBe(true))
})

it('offers a generated password without forcing first-login replacement', async () => {
  renderPage()
  fireEvent.click(await screen.findByRole('button', { name: 'Создать пользователя' }))
  fireEvent.click(screen.getByRole('button', { name: 'Сгенерировать пароль' }))
  const generated = screen.getByLabelText('Пароль') as HTMLInputElement
  const confirmation = screen.getByLabelText('Подтвердите пароль') as HTMLInputElement
  expect(generated.value).toHaveLength(24)
  expect(confirmation.value).toBe(generated.value)
  expect(screen.getByRole('checkbox', { name: /Потребовать смену/ })).not.toBeChecked()
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
