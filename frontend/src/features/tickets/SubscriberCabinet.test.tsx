import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SubscriberCabinet } from './SubscriberCabinet'
import { ticketApi } from './ticket-api'

vi.mock('./ticket-api', () => ({ ticketApi: { subscriberCabinet: vi.fn(), subscriberPassword: vi.fn() } }))

beforeEach(() => {
  vi.mocked(ticketApi.subscriberCabinet).mockReset().mockResolvedValue({ login: 'synthetic-login', password_available: true })
  vi.mocked(ticketApi.subscriberPassword).mockReset().mockResolvedValue({ password: 'synthetic-secret' })
})
afterEach(() => vi.useRealTimers())

function mount(permitted = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><SubscriberCabinet lookupId="synthetic-lookup" canReadPassword={permitted} /></QueryClientProvider>)
  return { client, ...view }
}

it('loads login immediately, reveals only on click, copies and clears without caching', async () => {
  const user = userEvent.setup()
  const { client } = mount()
  expect(await screen.findByText('synthetic-login')).toBeInTheDocument()
  expect(ticketApi.subscriberPassword).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Копировать пароль' })).toBeDisabled()
  const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
  await user.click(screen.getByRole('button', { name: 'Копировать логин' }))
  expect(write).toHaveBeenLastCalledWith('synthetic-login')
  await user.click(screen.getByRole('button', { name: 'Показать пароль' }))
  expect(await screen.findByText('synthetic-secret')).toBeInTheDocument()
  expect(ticketApi.subscriberPassword).toHaveBeenCalledTimes(1)
  expect(client.getMutationCache().getAll()).toHaveLength(0)
  expect(JSON.stringify(client.getQueryCache().getAll().map((query) => query.state.data))).not.toContain('synthetic-secret')
  await user.click(screen.getByRole('button', { name: 'Копировать пароль' }))
  expect(write).toHaveBeenLastCalledWith('synthetic-secret')
  await user.click(screen.getByRole('button', { name: 'Скрыть пароль' }))
  expect(screen.queryByText('synthetic-secret')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Показать пароль' }))
  await screen.findByText('synthetic-secret')
  expect(ticketApi.subscriberPassword).toHaveBeenCalledTimes(2)
})

it('does not request a password without permission or when disabled on the server', async () => {
  const view = mount(false)
  await screen.findByText('synthetic-login')
  expect(screen.getByRole('button', { name: 'Показать пароль' })).toBeDisabled()
  expect(ticketApi.subscriberPassword).not.toHaveBeenCalled()
  view.unmount()
  vi.mocked(ticketApi.subscriberCabinet).mockResolvedValue({ login: 'synthetic-login', password_available: false })
  mount()
  await screen.findByText('synthetic-login')
  expect(screen.getByRole('button', { name: 'Показать пароль' })).toBeDisabled()
})

it('aborts late responses on unmount and never reuses a password for another card', async () => {
  let finish!: (value: { password: string }) => void
  vi.mocked(ticketApi.subscriberPassword).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const view = mount()
  await screen.findByText('synthetic-login')
  fireEvent.click(screen.getByRole('button', { name: 'Показать пароль' }))
  const signal = vi.mocked(ticketApi.subscriberPassword).mock.calls[0]?.[1]
  view.unmount()
  expect(signal?.aborted).toBe(true)
  mount()
  await act(async () => finish({ password: 'synthetic-secret' }))
  expect(screen.queryByText('synthetic-secret')).not.toBeInTheDocument()
})

it('clears on visibility loss and after one minute', async () => {
  mount()
  await screen.findByText('synthetic-login')
  fireEvent.click(screen.getByRole('button', { name: 'Показать пароль' }))
  await screen.findByText('synthetic-secret')
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
  fireEvent(document, new Event('visibilitychange'))
  expect(screen.queryByText('synthetic-secret')).not.toBeInTheDocument()
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  fireEvent.click(screen.getByRole('button', { name: 'Показать пароль' }))
  await screen.findByText('synthetic-secret')
  vi.useFakeTimers()
  // Restart the visibility cycle so the timer is installed under fake timers.
  fireEvent.click(screen.getByRole('button', { name: 'Скрыть пароль' }))
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Показать пароль' })))
  act(() => vi.advanceTimersByTime(60_000))
  expect(screen.queryByText('synthetic-secret')).not.toBeInTheDocument()
})

it('shows a safe error and never copies or caches failed response content', async () => {
  vi.mocked(ticketApi.subscriberPassword).mockRejectedValue(new Error('do-not-display-source-error'))
  const { client } = mount()
  await screen.findByText('synthetic-login')
  fireEvent.click(screen.getByRole('button', { name: 'Показать пароль' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Пароль недоступен'))
  expect(screen.queryByText('do-not-display-source-error')).not.toBeInTheDocument()
  expect(client.getMutationCache().getAll()).toHaveLength(0)
})
