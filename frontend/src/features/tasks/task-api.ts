import { apiRequest } from '../../shared/api/client'

export type TaskStatus = 'OPEN' | 'COMPLETED' | 'NO_ANSWER'
export type TaskEventKind = 'CREATED' | 'MESSAGE' | 'COMPLETED' | 'NO_ANSWER' | 'REOPENED'
export interface InternalTask {
  id: string
  ticket_id: string
  ticket_number: number
  ticket_subject: string | null
  creator_id: string
  creator_name: string
  assignee_id: string
  assignee_name: string
  title: string
  description: string
  due_at: string | null
  status: TaskStatus
  version: number
  created_at: string
  updated_at: string
  completed_at: string | null
  subscriber_name?: string | null
  phone?: string | null
  account_no?: string | null
  latest_answer?: string | null
}
export interface TaskEvent {
  id: string
  actor_id: string
  actor_name: string
  kind: TaskEventKind
  body: string | null
  created_at: string
}
export interface TaskNotification {
  id: string
  task_id: string
  ticket_id: string
  ticket_number: number
  title: string
  kind: TaskEventKind
  created_at: string
  read_at: string | null
}
interface Page<T> { items: T[]; page: number; size: number; total: number }
export interface NotificationPage extends Page<TaskNotification> { unread_count: number }
export const taskApi = {
  assignees: (ticketId: string, signal?: AbortSignal) => apiRequest<{ id: string; display_name: string }[]>(`/api/v1/tasks/assignees?ticket_id=${encodeURIComponent(ticketId)}`, { signal }),
  list: (scope: 'assigned' | 'created' | 'all', state: 'all' | 'active' | 'completed' | 'overdue', page: number, ticketId?: string, signal?: AbortSignal) => {
    const params = new URLSearchParams({ scope, state, page: String(page), size: '20' })
    if (ticketId) params.set('ticket_id', ticketId)
    return apiRequest<Page<InternalTask>>(`/api/v1/tasks?${params}`, { signal })
  },
  get: (id: string, signal?: AbortSignal) => apiRequest<InternalTask>(`/api/v1/tasks/${encodeURIComponent(id)}`, { signal }),
  events: (id: string, page: number, signal?: AbortSignal) => apiRequest<Page<TaskEvent>>(`/api/v1/tasks/${encodeURIComponent(id)}/events?page=${page}&size=50`, { signal }),
  create: (payload: { ticket_id: string; assignee_id: string; description: string; client_request_id: string }) => apiRequest<InternalTask>('/api/v1/tasks', { method: 'POST', json: payload }),
  act: (id: string, version: number, body: string, requestId: string) => apiRequest<InternalTask>(`/api/v1/tasks/${encodeURIComponent(id)}/events`, { method: 'POST', ifMatch: `"${version}"`, json: { kind: 'COMPLETED', body, client_request_id: requestId } }),
  notifications: (signal?: AbortSignal) => apiRequest<NotificationPage>('/api/v1/tasks/notifications?size=20', { signal }),
  markRead: (id: string) => apiRequest<void>(`/api/v1/tasks/notifications/${encodeURIComponent(id)}/read`, { method: 'POST' }),
}
