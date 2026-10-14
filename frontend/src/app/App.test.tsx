import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { authApi } from '../features/auth/auth-api'
import { ApiError } from '../shared/api/client'
import type { MeResponse } from '../shared/api/types'
import { App } from './App'
import { queryClient } from './query-client'

vi.mock('../features/auth/auth-api', () => ({
  authApi: { me: vi.fn(), login: vi.fn(), logout: vi.fn() },
}))

const operator: MeResponse = {
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    login: 'synthetic-operator',
    display_name: 'Тестовый оператор',
    role: 'operator',
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  permissions: ['subscriber.read', 'ticket.read', 'ticket.create', 'ticket.comment'],
}

beforeEach(() => {
  queryClient.clear()
  window.history.replaceState({}, '', '/')
  vi.mocked(authApi.me).mockReset()
  vi.mocked(authApi.login).mockReset()
  vi.mocked(authApi.logout).mockReset()
})

describe('session shell', () => {
  it('opens login on 401 and enters the workspace after login', async () => {
    vi.mocked(authApi.me)
      .mockRejectedValueOnce(new ApiError(401, 'unauthorized'))
      .mockResolvedValue(operator)
    vi.mocked(authApi.login).mockResolvedValue({ ...operator, expires_at: '2026-01-01T08:00:00Z' })
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Вход в систему' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Логин'), { target: { value: 'synthetic-operator' } })
    fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'test-only' } })
    fireEvent.click(screen.getByRole('button', { name: /Войти/ }))
    expect(await screen.findByRole('heading', { name: /Добрый день/ })).toBeInTheDocument()
    expect(screen.getByText('Тестовый оператор', { selector: '.sidebar__profile-text strong' })).toBeInTheDocument()
    expect(vi.mocked(authApi.login)).toHaveBeenCalledWith('synthetic-operator', 'test-only')
  })

  it('uses server permissions and never exposes ticket actions to admin', async () => {
    vi.mocked(authApi.me).mockResolvedValue({
      ...operator,
      user: { ...operator.user, role: 'admin' },
      permissions: ['identity.manage'],
    })
    render(<App />)
    expect(await screen.findByText(/Техническая роль администратора/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Заявки/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: /Мой доступ/ }))
    await waitFor(() => expect(screen.getByText('Управление учётными записями')).toBeInTheDocument())
  })

  it('does not replace a failed session lookup with a login form', async () => {
    vi.mocked(authApi.me).mockRejectedValue(new ApiError(503, 'not_ready'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Не удалось открыть рабочее пространство' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Вход в систему' })).not.toBeInTheDocument()
  })

  it('hides cached private content when a session is later revoked', async () => {
    vi.mocked(authApi.me)
      .mockResolvedValueOnce(operator)
      .mockRejectedValue(new ApiError(401, 'unauthorized'))
    render(<App />)
    expect(await screen.findByRole('heading', { name: /Добрый день/ })).toBeInTheDocument()
    await queryClient.invalidateQueries({ queryKey: ['session', 0] })
    expect(await screen.findByRole('heading', { name: 'Вход в систему' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Добрый день/ })).not.toBeInTheDocument()
  })

  it('clears private query state after confirmed logout', async () => {
    vi.mocked(authApi.me)
      .mockResolvedValueOnce(operator)
      .mockRejectedValue(new ApiError(401, 'unauthorized'))
    vi.mocked(authApi.logout).mockResolvedValue({ status: 'logged_out' })
    render(<App />)
    expect(await screen.findByRole('heading', { name: /Добрый день/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Выйти' }))
    expect(await screen.findByRole('heading', { name: 'Вход в систему' })).toBeInTheDocument()
    expect(queryClient.getQueryData(['session', 0])).toBeUndefined()
  })
})
