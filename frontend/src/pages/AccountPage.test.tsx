import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { authApi } from '../features/auth/auth-api'
import type { MeResponse } from '../shared/api/types'
import { AccountPage } from './AccountPage'

vi.mock('../features/auth/auth-api', () => ({ authApi: { changePassword: vi.fn() } }))

const session: MeResponse = {
  user: {
    id: '00000000-0000-4000-8000-000000000001', login: 'employee',
    display_name: 'Сотрудник', role: 'operator', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  },
  permissions: ['tickets.read.own'],
}

it('lets an employee replace their password after confirming the current one', async () => {
  const changed = vi.fn()
  vi.mocked(authApi.changePassword).mockResolvedValue({ status: 'password_changed' })
  render(<AccountPage session={session} onPasswordChanged={changed} />)
  fireEvent.change(screen.getByLabelText('Текущий пароль'), { target: { value: 'old' } })
  fireEvent.change(screen.getByLabelText('Новый пароль'), { target: { value: '1' } })
  fireEvent.change(screen.getByLabelText('Подтвердите новый пароль'), { target: { value: '1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Показать: Новый пароль' }))
  expect(screen.getByLabelText('Новый пароль')).toHaveAttribute('type', 'text')
  fireEvent.click(screen.getByRole('button', { name: 'Изменить пароль' }))
  await waitFor(() => expect(changed).toHaveBeenCalledOnce())
  expect(authApi.changePassword).toHaveBeenCalledWith('old', '1')
})
