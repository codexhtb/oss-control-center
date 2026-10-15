import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { authApi } from './auth-api'
import { PasswordChangePage } from './PasswordChangePage'

vi.mock('./auth-api', () => ({ authApi: { changePassword: vi.fn() } }))

it('requires a new password and uses the authenticated change endpoint', async () => {
  const done = vi.fn()
  vi.mocked(authApi.changePassword).mockResolvedValue({ status: 'password_changed' })
  render(<PasswordChangePage onChanged={done} />)
  fireEvent.change(screen.getByLabelText('Временный пароль'), { target: { value: 'temporary-only' } })
  fireEvent.change(screen.getByLabelText('Новый пароль'), { target: { value: 'new-synthetic-password-42' } })
  fireEvent.change(screen.getByLabelText('Повторите пароль'), { target: { value: 'new-synthetic-password-42' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сменить пароль' }))
  await waitFor(() => expect(done).toHaveBeenCalledOnce())
  expect(authApi.changePassword).toHaveBeenCalledWith('temporary-only', 'new-synthetic-password-42')
})
