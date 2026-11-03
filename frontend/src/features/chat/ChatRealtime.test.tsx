import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { chatApi } from './chat-api'
import { chatDeletedEvent, useChatRealtime } from './ChatRealtime'

vi.mock('./chat-api', () => ({ chatApi: { summary: vi.fn() } }))

const streams: FakeStream[] = []
class FakeStream {
  listeners: Record<string, (event: MessageEvent) => void> = {}
  closed = false
  onopen: (() => void) | null = null
  constructor(public url: string) { streams.push(this) }
  addEventListener(name: string, listener: (event: MessageEvent) => void) { this.listeners[name] = listener }
  close() { this.closed = true }
}

function Host() { useChatRealtime(); return null }

afterEach(() => {
  vi.unstubAllGlobals()
  streams.length = 0
})

it('keeps one SSE connection as summaries advance and resumes from the initial cursor', async () => {
  let latest = 4
  vi.mocked(chatApi.summary).mockImplementation(async () => ({ latest_id: latest, latest_event_id: latest, last_read_id: 0, unread_count: 1 }))
  vi.stubGlobal('EventSource', FakeStream)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><Host /></QueryClientProvider>)
  await waitFor(() => expect(streams).toHaveLength(1))
  expect(streams[0]!.url).toBe('/api/v1/chat/events?after=4')
  latest = 5
  await act(async () => { await client.invalidateQueries({ queryKey: ['chat', 'summary'] }) })
  expect(streams).toHaveLength(1)
  expect(streams[0]!.closed).toBe(false)
  view.unmount()
  expect(streams[0]!.closed).toBe(true)
})

it('forwards a deletion event to the chat page', async () => {
  vi.mocked(chatApi.summary).mockResolvedValue({ latest_id: 1, latest_event_id: 3, last_read_id: 0, unread_count: 1 })
  vi.stubGlobal('EventSource', FakeStream)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const received: number[] = []
  const listener = (event: Event) => received.push((event as CustomEvent<number>).detail)
  window.addEventListener(chatDeletedEvent, listener)
  const view = render(<QueryClientProvider client={client}><Host /></QueryClientProvider>)
  await waitFor(() => expect(streams).toHaveLength(1))
  expect(streams[0]!.url).toBe('/api/v1/chat/events?after=3')
  await act(async () => streams[0]!.listeners.delete?.({ data: '{"message_id":1}' } as MessageEvent))
  expect(received).toEqual([1])
  view.unmount()
  window.removeEventListener(chatDeletedEvent, listener)
})
