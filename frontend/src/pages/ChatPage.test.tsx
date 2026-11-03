import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { chatMessageEvent } from '../features/chat/ChatRealtime'
import { chatApi } from '../features/chat/chat-api'
import type { User } from '../shared/api/types'
import { ChatPage } from './ChatPage'

vi.mock('../features/chat/chat-api', () => ({ chatApi: {
  history: vi.fn(), send: vi.fn(), markRead: vi.fn(), summary: vi.fn(),
} }))

const user = { id: 'self', display_name: 'Инженер', login: 'engineer', role: 'engineer', is_active: true,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' } as User
const incoming = { id: 1, sender_id: 'other', sender_name: 'Оператор', body: 'Привет', created_at: '2026-11-03T10:00:00Z' }

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ChatPage user={user} /></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(chatApi.history).mockReset().mockResolvedValue({ items: [incoming], next_before: null })
  vi.mocked(chatApi.send).mockReset().mockImplementation(async (body) => ({ ...incoming, id: 2, sender_id: 'self', sender_name: 'Инженер', body }))
  vi.mocked(chatApi.markRead).mockReset().mockResolvedValue({ latest_id: 2, last_read_id: 2, unread_count: 0 })
})

it('loads history, receives realtime messages and marks the visible end as read', async () => {
  renderPage()
  expect(await screen.findByText('Привет')).toBeInTheDocument()
  await waitFor(() => expect(chatApi.markRead).toHaveBeenCalledWith(1))
  window.dispatchEvent(new CustomEvent(chatMessageEvent, { detail: { ...incoming, id: 2, body: 'Новый ответ' } }))
  expect(await screen.findByText('Новый ответ')).toBeInTheDocument()
  await waitFor(() => expect(chatApi.markRead).toHaveBeenCalledWith(2))
})

it('sends with Enter, keeps Shift+Enter for a newline and shows own message', async () => {
  renderPage()
  await screen.findByText('Привет')
  const input = screen.getByLabelText('Сообщение')
  fireEvent.change(input, { target: { value: 'Первая' } })
  fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
  expect(chatApi.send).not.toHaveBeenCalled()
  fireEvent.keyDown(input, { key: 'Enter' })
  await waitFor(() => expect(chatApi.send).toHaveBeenCalledWith('Первая', expect.any(String)))
  expect(await screen.findByText('Первая')).toBeInTheDocument()
  expect(screen.getByText('Вы')).toBeInTheDocument()
})

it('loads older pages without replacing the latest messages', async () => {
  vi.mocked(chatApi.history).mockResolvedValueOnce({ items: [{ ...incoming, id: 2 }], next_before: 2 })
    .mockResolvedValueOnce({ items: [{ ...incoming, id: 1, body: 'Ранее' }], next_before: null })
  renderPage()
  await screen.findByText('Привет')
  fireEvent.click(screen.getByRole('button', { name: 'Показать ранние сообщения' }))
  expect(await screen.findByText('Ранее')).toBeInTheDocument()
  expect(screen.getByText('Привет')).toBeInTheDocument()
})
