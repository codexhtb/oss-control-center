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

export const notificationApi = {
  list: (signal?: AbortSignal) => apiRequest<NotificationPage>('/api/v1/notifications?size=20', { signal }),
  markRead: (id: string) => apiRequest<void>(`/api/v1/notifications/${encodeURIComponent(id)}/read`, { method: 'POST' }),
}
