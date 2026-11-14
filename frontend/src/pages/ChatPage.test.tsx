import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { chatDeletedEvent, chatMessageEvent } from '../features/chat/ChatRealtime'
import { chatApi } from '../features/chat/chat-api'
import type { User } from '../shared/api/types'
import { ChatPage } from './ChatPage'

vi.mock('../features/chat/chat-api', () => ({ chatApi: {
  history: vi.fn(), send: vi.fn(), remove: vi.fn(), markRead: vi.fn(), summary: vi.fn(),
} }))

const user = { id: 'self', display_name: 'Инженер', login: 'engineer', role: 'engineer', is_active: true,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' } as User
const incoming = { id: 1, sender_id: 'other', sender_name: 'Оператор', body: 'Привет', created_at: '2026-11-03T10:00:00Z', reply_to: null }

function renderPage(canDelete = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ChatPage user={user} canDelete={canDelete} /></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(chatApi.history).mockReset().mockResolvedValue({ items: [incoming], next_before: null })
  vi.mocked(chatApi.send).mockReset().mockImplementation(async (body) => ({ ...incoming, id: 2, sender_id: 'self', sender_name: 'Инженер', body }))
  vi.mocked(chatApi.markRead).mockReset().mockResolvedValue({ latest_id: 2, latest_event_id: 2, last_read_id: 2, unread_count: 0 })
  vi.mocked(chatApi.remove).mockReset().mockResolvedValue(undefined)
})

it('offers moderation only to administrators and removes a message after confirmation', async () => {
  const regular = renderPage()
  await screen.findByText('Привет')
  expect(screen.queryByRole('button', { name: 'Удалить сообщение 1' })).not.toBeInTheDocument()
  regular.unmount()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  renderPage(true)
  await screen.findByText('Привет')
  fireEvent.click(screen.getByRole('button', { name: 'Удалить сообщение 1' }))
  await waitFor(() => expect(chatApi.remove).toHaveBeenCalledWith(1))
  await waitFor(() => expect(screen.queryByText('Привет')).not.toBeInTheDocument())
  vi.restoreAllMocks()
})

it('removes messages received through the live deletion event', async () => {
  renderPage()
  await screen.findByText('Привет')
  window.dispatchEvent(new CustomEvent(chatDeletedEvent, { detail: 1 }))
  await waitFor(() => expect(screen.queryByText('Привет')).not.toBeInTheDocument())
})

it('loads history, receives realtime messages and marks the visible end as read', async () => {
  renderPage()
  expect(screen.getByRole('heading', { name: 'Общий чат' })).toBeInTheDocument()
  expect(screen.queryByText('Операторы, инженеры OSS и администраторы')).not.toBeInTheDocument()
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

it('quotes a selected message and sends its ID, then clears the reply context', async () => {
  vi.mocked(chatApi.send).mockImplementation(async (body, _id, replyId) => ({
    ...incoming, id: 2, sender_id: 'self', sender_name: 'Инженер', body,
    reply_to: replyId ? { id: incoming.id, sender_name: incoming.sender_name, body: incoming.body, deleted: false } : null,
  }))
  renderPage()
  await screen.findByText('Привет')
  fireEvent.click(screen.getByRole('button', { name: 'Ответить на сообщение 1 от Оператор' }))
  expect(screen.getByText('Ответ для Оператор')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Ответ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Отправить сообщение' }))
  await waitFor(() => expect(chatApi.send).toHaveBeenCalledWith('Ответ', expect.any(String), 1))
  expect(await screen.findByText('Ответ')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText('Ответ для Оператор')).not.toBeInTheDocument())
})

it('can cancel a reply without clearing the typed message', async () => {
  renderPage()
  await screen.findByText('Привет')
  fireEvent.click(screen.getByRole('button', { name: 'Ответить на сообщение 1 от Оператор' }))
  fireEvent.change(screen.getByLabelText('Сообщение'), { target: { value: 'Самостоятельное сообщение' } })
  fireEvent.click(screen.getByRole('button', { name: 'Отменить ответ' }))
  expect(screen.queryByText('Ответ для Оператор')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Сообщение')).toHaveValue('Самостоятельное сообщение')
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

it('groups nearby messages from one author and expands the composer for multiple lines', async () => {
  vi.mocked(chatApi.history).mockResolvedValueOnce({ items: [
    incoming,
    { ...incoming, id: 2, body: 'Продолжение', created_at: '2026-11-03T10:02:00Z' },
    { ...incoming, id: 3, body: 'Позже', created_at: '2026-11-03T11:00:00Z' },
  ], next_before: null })
  const { container } = renderPage()
  expect(await screen.findByText('Продолжение')).toBeInTheDocument()
  expect(container.querySelectorAll('.staff-chat__message--grouped')).toHaveLength(1)
  expect(container.querySelectorAll('.staff-chat__avatar')).toHaveLength(2)
  const input = screen.getByLabelText('Сообщение') as HTMLTextAreaElement
  Object.defineProperty(input, 'scrollHeight', { configurable: true, get: () => input.value.includes('\n') ? 72 : 36 })
  fireEvent.change(input, { target: { value: 'Первая\nВторая' } })
  expect(input.style.height).toBe('72px')
})
