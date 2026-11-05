import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, expect, it, vi } from 'vitest'
import { settingsApi } from './settings-api'
import { ReasonManager, TemplateManager } from './SettingsCatalogs'

vi.mock('./settings-api', () => ({ settingsApi: {
  reasons: vi.fn(), updateReason: vi.fn(), templates: vi.fn(), createTemplate: vi.fn(), updateTemplate: vi.fn(),
} }))

function show(content: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{content}</QueryClientProvider>)
}

beforeEach(() => {
  for (const fn of Object.values(settingsApi)) vi.mocked(fn).mockReset()
  vi.mocked(settingsApi.reasons).mockResolvedValue([{
    code: 'NETWORK_FAULT', name: 'Сеть', sort_order: 40, is_active: true, version: 3,
    updated_at: '2026-01-01T00:00:00Z',
  }])
  vi.mocked(settingsApi.templates).mockResolvedValue([])
})

it('edits a reason by immutable code and current version', async () => {
  vi.mocked(settingsApi.updateReason).mockResolvedValue({
    code: 'NETWORK_FAULT', name: 'Сетевой узел', sort_order: 40, is_active: true, version: 4,
    updated_at: '2026-01-01T00:00:00Z',
  })
  show(<ReasonManager />)
  fireEvent.change(await screen.findByRole('textbox', { name: 'Название причины NETWORK_FAULT' }), {
    target: { value: 'Сетевой узел' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
  await waitFor(() => expect(settingsApi.updateReason).toHaveBeenCalledWith(
    'NETWORK_FAULT', 3, { name: 'Сетевой узел', sort_order: 40, is_active: true },
  ))
})

it('creates a typed server template and shows an empty list state', async () => {
  vi.mocked(settingsApi.createTemplate).mockResolvedValue({
    id: 'template-1', name: 'Обзвон', body: 'Проверка связи', purpose: 'callback',
    sort_order: 5, is_active: true, version: 1,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  })
  show(<TemplateManager />)
  expect(await screen.findByText('Шаблонов пока нет.')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'Название нового шаблона' }), { target: { value: 'Обзвон' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Назначение нового шаблона' }), { target: { value: 'callback' } })
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Порядок нового шаблона' }), { target: { value: '5' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Текст нового шаблона' }), { target: { value: 'Проверка связи' } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать шаблон' }))
  await waitFor(() => expect(settingsApi.createTemplate).toHaveBeenCalledWith({
    name: 'Обзвон', body: 'Проверка связи', purpose: 'callback', sort_order: 5,
  }))
})
