import { apiRequest } from '../../shared/api/client'
import type {
  CreateTicketInput, Decision, Diagnostic, Engineer, Page, RecentEvent, SubscriberCard,
  Ticket, TicketComment, TicketDetail, TicketEvent, TicketFilters,
} from './types'

function withQuery(path: `/api/v1/${string}`, values: Record<string, string | number | boolean | undefined>) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  return `${path}?${query.toString()}` as `/api/v1/${string}`
}

const ticketPath = (id: string) => `/api/v1/tickets/${encodeURIComponent(id)}` as const

export const ticketApi = {
  list: (filters: TicketFilters, signal?: AbortSignal) =>
    apiRequest<Page<Ticket>>(withQuery('/api/v1/tickets', { ...filters }), { signal }),
  get: (id: string, signal?: AbortSignal) => apiRequest<TicketDetail>(ticketPath(id), { signal }),
  create: (payload: CreateTicketInput) =>
    apiRequest<Ticket>('/api/v1/tickets', { method: 'POST', json: payload }),
  engineers: (signal?: AbortSignal) =>
    apiRequest<Page<Engineer>>('/api/v1/tickets/assignable-engineers?size=100', { signal }),
  recentEvents: (size = 8, signal?: AbortSignal) =>
    apiRequest<Page<RecentEvent>>(withQuery('/api/v1/tickets/events/recent', { page: 1, size }), { signal }),
  lookup: (accountNo: string) =>
    apiRequest<SubscriberCard>('/api/v1/subscriber-lookups', {
      method: 'POST', json: { account_no: accountNo },
    }),
  comments: (id: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<TicketComment>>(withQuery(`${ticketPath(id)}/comments`, { page, size: 20 }), { signal }),
  history: (id: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<TicketEvent>>(withQuery(`${ticketPath(id)}/history`, { page, size: 20 }), { signal }),
  diagnostics: (id: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<Diagnostic>>(withQuery(`${ticketPath(id)}/manual-diagnostics`, { page, size: 20 }), { signal }),
  decisions: (id: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<Decision>>(withQuery(`${ticketPath(id)}/decisions`, { page, size: 20 }), { signal }),
  assign: (id: string, engineerId: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/assignment`, {
      method: 'POST', json: { engineer_id: engineerId }, ifMatch: `"${version}"`,
    }),
  start: (id: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/start`, { method: 'POST', ifMatch: `"${version}"` }),
  requestFieldWork: (id: string, reason: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/field-work-request`, {
      method: 'POST', json: { reason }, ifMatch: `"${version}"`,
    }),
  addComment: (id: string, body: string) =>
    apiRequest<TicketComment>(`${ticketPath(id)}/comments`, { method: 'POST', json: { body } }),
  addDiagnostic: (id: string, finding: string, connectionId?: string) =>
    apiRequest<Diagnostic>(`${ticketPath(id)}/manual-diagnostics`, {
      method: 'POST',
      json: {
        finding,
        observed_at: new Date().toISOString(),
        ...(connectionId ? { connection_snapshot_id: connectionId } : {}),
      },
    }),
  addDecision: (id: string, decisionText: string, performedActions: string) =>
    apiRequest<Decision>(`${ticketPath(id)}/decisions`, {
      method: 'POST', json: { decision_text: decisionText, performed_actions: performedActions },
    }),
  close: (id: string, decisionId: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/close`, {
      method: 'POST', json: { decision_id: decisionId }, ifMatch: `"${version}"`,
    }),
}
