import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { apiRequest } from '../../shared/api/client'
import { InstallerManager } from './InstallerManager'

vi.mock('../../shared/api/client', () => ({ apiRequest: vi.fn(), errorText: () => 'Ошибка' }))

beforeEach(() => {
  vi.mocked(apiRequest).mockReset()
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    if (path === '/api/v1/field-work/installers') return { items: [] }
    return { id: 'installer-1', telegram_user_id: 12345, name: 'Монтажник', is_active: true,
      directions: ['SUKHUM', 'OCHAMCHIRA'], updated_at: '2026-11-17T00:00:00Z' }
  })
})

it('creates an installer using the existing management API', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><InstallerManager /></QueryClientProvider>)
  await screen.findByText('Монтажники не найдены.')
  fireEvent.click(screen.getByRole('button', { name: 'Добавить монтажника' }))
  fireEvent.change(screen.getByLabelText('Имя'), { target: { value: 'Монтажник' } })
  fireEvent.change(screen.getByLabelText('Telegram User ID'), { target: { value: '12345' } })
  fireEvent.click(screen.getByLabelText('Сухум'))
  fireEvent.click(screen.getByLabelText('Очамчира'))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(apiRequest).toHaveBeenCalledWith('/api/v1/field-work/installers', {
    method: 'POST', json: { telegram_user_id: 12345, name: 'Монтажник',
      is_active: true, directions: ['SUKHUM', 'OCHAMCHIRA'] },
  }))
})

it('requires confirmation and deletes an unused installer', async () => {
  const installer = { id: 'installer-1', telegram_user_id: 12345, name: 'Монтажник', is_active: true,
    directions: ['SUKHUM'] as const, updated_at: '2026-11-17T00:00:00Z' }
  vi.mocked(apiRequest).mockImplementation(async (path) => {
    if (path === '/api/v1/field-work/installers') return { items: [installer] }
    return undefined
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><InstallerManager /></QueryClientProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'Изменить' }))
  fireEvent.click(screen.getByRole('button', { name: 'Удалить монтажника' }))
  expect(apiRequest).not.toHaveBeenCalledWith('/api/v1/field-work/installers/installer-1', { method: 'DELETE' })
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить удаление' }))
  await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
    '/api/v1/field-work/installers/installer-1', { method: 'DELETE' },
  ))
})

it('suggests deactivation when installer history blocks deletion', async () => {
  const installer = { id: 'installer-1', telegram_user_id: 12345, name: 'Монтажник', is_active: true,
    directions: ['SUKHUM'] as const, updated_at: '2026-11-17T00:00:00Z' }
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    if (path === '/api/v1/field-work/installers') return { items: [installer] }
    if (options?.method === 'DELETE') throw { code: 'installer_has_history' }
    return installer
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><InstallerManager /></QueryClientProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'Изменить' }))
  fireEvent.click(screen.getByRole('button', { name: 'Удалить монтажника' }))
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить удаление' }))
  expect(await screen.findByText(/Снимите флажок «Активен»/)).toBeInTheDocument()
})
