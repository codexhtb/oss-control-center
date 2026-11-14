import { apiRequest } from '../../shared/api/client'

export interface ChatMessage {
  id: number
  sender_id: string
  sender_name: string
  body: string
  created_at: string
  reply_to: { id: number; sender_name: string; body: string | null; deleted: boolean } | null
}

export interface ChatHistory { items: ChatMessage[]; next_before: number | null }
export interface ChatSummary { latest_id: number; latest_event_id: number; last_read_id: number; unread_count: number }

export const chatApi = {
  summary: (signal?: AbortSignal) => apiRequest<ChatSummary>('/api/v1/chat/summary', { signal }),
  history: (before?: number, signal?: AbortSignal) => apiRequest<ChatHistory>(
    `/api/v1/chat/messages?limit=40${before ? `&before=${before}` : ''}`, { signal },
  ),
  send: (body: string, clientId: string, replyToMessageId?: number) => apiRequest<ChatMessage>('/api/v1/chat/messages', {
    method: 'POST', json: { body, client_id: clientId, ...(replyToMessageId ? { reply_to_message_id: replyToMessageId } : {}) },
  }),
  remove: (messageId: number) => apiRequest<void>(`/api/v1/chat/messages/${messageId}`, { method: 'DELETE' }),
  markRead: (throughId: number) => apiRequest<ChatSummary>('/api/v1/chat/read', {
    method: 'POST', json: { through_id: throughId },
  }),
}
