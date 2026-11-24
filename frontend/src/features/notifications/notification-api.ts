import { apiRequest } from '../../shared/api/client'

export interface FieldReportNotification {
  id: string
  ticket_id: string
  ticket_number: number
  sender_name: string
  reported_at: string
  read_at: string | null
  awaiting_verification: boolean
}

export interface NotificationPage {
  items: FieldReportNotification[]
  page: number
  size: number
  total: number
  unread_count: number
}

export interface CenterNotification {
  id: string
  source: 'task' | 'field_report'
  ticket_id: string
  ticket_number: number
  task_id: string | null
  kind: string
  title: string
  created_at: string
  read_at: string | null
}
export interface CenterPage {
  items: CenterNotification[]
  page: number
  size: number
  total: number
  unread_count: number
}

export const notificationApi = {
  center: (state: 'unread' | 'read', page: number, signal?: AbortSignal) => apiRequest<CenterPage>(`/api/v1/notifications/center?state=${state}&page=${page}&size=20`, { signal }),
  readCenter: (source: CenterNotification['source'], id: string) => apiRequest<void>(`/api/v1/notifications/center/${source}/${encodeURIComponent(id)}/read`, { method: 'POST' }),
  readAll: () => apiRequest<void>('/api/v1/notifications/center/read-all', { method: 'POST' }),
  list: (signal?: AbortSignal) => apiRequest<NotificationPage>('/api/v1/notifications?size=20', { signal }),
  markRead: (id: string) => apiRequest<void>(`/api/v1/notifications/${encodeURIComponent(id)}/read`, { method: 'POST' }),
}
