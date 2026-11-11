import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it, vi } from 'vitest'
import type { MeResponse } from '../../shared/api/types'
import { TicketTasks } from './TicketTasks'
import { taskApi, type InternalTask } from './task-api'

vi.mock('./task-api', () => ({ taskApi: { list: vi.fn(), assignees: vi.fn(), create: vi.fn() } }))
vi.mock('./TaskConversation', () => ({ TaskConversation: ({ taskId }: { taskId: string }) => <p>Открыто поручение {taskId}</p> }))

const session: MeResponse = {
  user: { id: 'engineer-id', login: 'engineer', display_name: 'Инженер', role: 'engineer', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  permissions: ['tickets.read.all', 'tasks.create'],
}
const task: InternalTask = {
  id: 'task-id', ticket_id: 'ticket-id', ticket_number: 10, ticket_subject: 'Тема',
  creator_id: 'engineer-id', creator_name: 'Инженер', assignee_id: 'sima-id', assignee_name: 'Сима',
  title: 'Уточнить время', description: 'Уточнить время', due_at: null, status: 'OPEN', version: 1,
  created_at: '2026-11-01T10:00:00Z', updated_at: '2026-11-01T10:00:00Z', completed_at: null,
}

function mount(start = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[start]}>
    <TicketTasks ticketId="ticket-id" session={session} />
  </MemoryRouter></QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(taskApi.list).mockReset().mockResolvedValue({ items: [], page: 1, size: 20, total: 0 })
  vi.mocked(taskApi.assignees).mockReset().mockResolvedValue([{ id: 'sima-id', display_name: 'Сима' }])
  vi.mocked(taskApi.create).mockReset().mockResolvedValue(task)
})

it('creates from one text field without title or due date', async () => {
  mount()
  expect(screen.queryByRole('textbox', { name: 'Заголовок' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText(/Срок/)).not.toBeInTheDocument()
  await screen.findByRole('option', { name: 'Сима' })
  fireEvent.change(screen.getByRole('combobox', { name: 'Сотрудник' }), { target: { value: 'sima-id' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Что нужно узнать или сделать?' }), { target: { value: 'Уточнить время' } })
  const submit = screen.getByRole('button', { name: 'Создать поручение' })
  await waitFor(() => expect(submit).not.toBeDisabled())
  fireEvent.click(submit)
  await waitFor(() => expect(taskApi.create).toHaveBeenCalledWith(expect.objectContaining({
    ticket_id: 'ticket-id', assignee_id: 'sima-id', description: 'Уточнить время',
  })))
  const payload = vi.mocked(taskApi.create).mock.calls.at(0)?.[0]
  expect(payload).not.toHaveProperty('title')
  expect(payload).not.toHaveProperty('due_at')
})

it('opens the task from a deep link inside the ticket', async () => {
  mount('/?task=task-id')
  expect(await screen.findByText('Открыто поручение task-id')).toBeInTheDocument()
})
