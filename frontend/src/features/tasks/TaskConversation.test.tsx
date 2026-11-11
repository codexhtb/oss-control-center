import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import type { MeResponse } from '../../shared/api/types'
import { TaskConversation } from './TaskConversation'
import { taskApi, type InternalTask } from './task-api'

vi.mock('./task-api', () => ({ taskApi: { get: vi.fn(), events: vi.fn(), act: vi.fn() } }))

const engineer: MeResponse = {
  user: { id: 'engineer-id', login: 'engineer', display_name: 'Инженер', role: 'engineer', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  permissions: ['tickets.read.all', 'tasks.create', 'tasks.execute'],
}
const task: InternalTask = {
  id: 'task-id', ticket_id: 'ticket-id', ticket_number: 10, ticket_subject: 'Тема',
  creator_id: 'engineer-id', creator_name: 'Инженер', assignee_id: 'sima-id', assignee_name: 'Сима',
  title: 'Позвонить', description: 'Уточнить время', due_at: null, status: 'OPEN', version: 1,
  created_at: '2026-11-01T10:00:00Z', updated_at: '2026-11-01T10:00:00Z', completed_at: null,
}

function mount(session: MeResponse) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter>
    <TaskConversation taskId="task-id" session={session} />
  </MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(taskApi.get).mockReset().mockResolvedValue(task)
  vi.mocked(taskApi.events).mockReset().mockResolvedValue({ items: [], total: 0, page: 1, size: 50 })
  vi.mocked(taskApi.act).mockReset().mockResolvedValue({ ...task, version: 2 })
})

it('shows the conversation and lets the author send a clarification without completing it', async () => {
  mount(engineer)
  expect(await screen.findByText('Уточнить время')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'Ответ или уточнение' }), { target: { value: 'Позвоните завтра' } })
  fireEvent.click(screen.getByRole('button', { name: 'Отправить ответ' }))
  await waitFor(() => expect(taskApi.act).toHaveBeenCalledWith('task-id', 1, 'MESSAGE', 'Позвоните завтра', expect.any(String)))
  expect(screen.queryByRole('button', { name: 'Завершить' })).not.toBeInTheDocument()
})

it('lets the assignee finish or report no answer and blocks author-only reopening', async () => {
  mount({ ...engineer, user: { ...engineer.user, id: 'sima-id', role: 'operator' }, permissions: ['tickets.read.all', 'tasks.execute'] })
  expect(await screen.findByRole('button', { name: 'Завершить' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Не дозвонилась' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Не дозвонилась' }))
  await waitFor(() => expect(taskApi.act).toHaveBeenCalledWith('task-id', 1, 'NO_ANSWER', null, expect.any(String)))
  expect(screen.queryByRole('button', { name: 'Открыть повторно' })).not.toBeInTheDocument()
})
