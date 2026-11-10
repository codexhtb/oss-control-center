import { apiRequest } from '../../shared/api/client'
import type {
  ActiveTickets, ConfiguredServicesPage, TariffHistoryPage, CreateTicketInput, Decision, Diagnostic, DiagnosticReport, TicketCheckResult, Engineer, OnuResult, Page, RecentEvent, SubscriberCard, SubscriberFinanceKind, SubscriberFinancePage,
  Ticket, TicketCallback, CallbackOutcome, TicketCategory, TicketComment, TicketDetail, TicketEvent, TicketFilters,
  PreviousTicket,
  ResolutionReason,
  FieldWorkHandoff, EditTicketInput,
  FieldExternalMessage,
  FieldWorkReschedule,
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
  categories: (signal?: AbortSignal) => apiRequest<TicketCategory[]>('/api/v1/ticket-categories', { signal }),
  list: (filters: TicketFilters, signal?: AbortSignal) =>
    apiRequest<Page<Ticket>>(withQuery('/api/v1/tickets', { ...filters }), { signal }),
  get: (id: string, signal?: AbortSignal) => apiRequest<TicketDetail>(ticketPath(id), { signal }),
  create: (payload: CreateTicketInput) =>
    apiRequest<Ticket>('/api/v1/tickets', { method: 'POST', json: payload }),
  edit: (id: string, version: number, payload: EditTicketInput) =>
    apiRequest<Ticket>(ticketPath(id), {
      method: 'PATCH', json: payload, ifMatch: `"${version}"`,
    }),
  trash: (page: number, signal?: AbortSignal) =>
    apiRequest<Page<Ticket>>(withQuery('/api/v1/tickets/trash', { page, size: 20 }), { signal }),
  remove: (id: string, version: number, reason?: string) =>
    apiRequest<Ticket>(ticketPath(id), {
      method: 'DELETE', json: reason ? { reason } : {}, ifMatch: `"${version}"`,
    }),
  restore: (id: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/restore`, { method: 'POST', ifMatch: `"${version}"` }),
  purge: (id: string, version: number, confirmNumber: number) =>
    apiRequest<void>(`${ticketPath(id)}/purge`, {
      method: 'DELETE', json: { confirm_number: confirmNumber }, ifMatch: `"${version}"`,
    }),
  engineers: (signal?: AbortSignal) =>
    apiRequest<Page<Engineer>>('/api/v1/tickets/assignable-engineers?size=100', { signal }),
  recentEvents: (size = 8, signal?: AbortSignal) =>
    apiRequest<Page<RecentEvent>>(withQuery('/api/v1/tickets/events/recent', { page: 1, size }), { signal }),
  lookup: (accountNo: string) =>
    apiRequest<SubscriberCard>('/api/v1/subscriber-lookups', {
      method: 'POST', json: { account_no: accountNo },
    }),
  getLookup: (lookupId: string, signal?: AbortSignal) =>
    apiRequest<SubscriberCard>(`/api/v1/subscriber-lookups/${encodeURIComponent(lookupId)}`, { signal }),
  subscriberFinance: (lookupId: string, start: string, end: string, kind: SubscriberFinanceKind, page: number, signal?: AbortSignal, serviceLinkId?: number | null) =>
    apiRequest<SubscriberFinancePage>(withQuery(`/api/v1/subscriber-lookups/${encodeURIComponent(lookupId)}/finance`, { start, end, kind, page, size: 30, service_link_id: serviceLinkId ?? undefined }), { signal }),
  subscriberServices: (lookupId: string, signal?: AbortSignal) =>
    apiRequest<ConfiguredServicesPage>(`/api/v1/subscriber-lookups/${encodeURIComponent(lookupId)}/services`, { signal }),
  subscriberTariffHistory: (lookupId: string, signal?: AbortSignal) =>
    apiRequest<TariffHistoryPage>(`/api/v1/subscriber-lookups/${encodeURIComponent(lookupId)}/tariff-history`, { signal }),
  subscriberTickets: (lookupId: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<Ticket>>(withQuery(`/api/v1/subscriber-lookups/${encodeURIComponent(lookupId)}/tickets`, { page, size: 10 }), { signal }),
  previousTickets: (id: string, page: number, size: number, signal?: AbortSignal) =>
    apiRequest<Page<PreviousTicket>>(withQuery(`${ticketPath(id)}/previous-tickets`, { page, size }), { signal }),
  activeSubscriberTickets: (lookupId: string, page: number, signal?: AbortSignal) =>
    apiRequest<ActiveTickets>(withQuery(`/api/v1/subscriber-lookups/${encodeURIComponent(lookupId)}/active-tickets`, { page, size: 20 }), { signal }),
  checkSubscriber: (lookupId: string, connectionId: string) =>
    apiRequest<DiagnosticReport>(`/api/v1/subscriber-lookups/${encodeURIComponent(lookupId)}/diagnostics/check`, {
      method: 'POST', json: { connection_snapshot_id: connectionId },
    }),
  subscriberOnu: (lookupId: string, connectionId: string, signal?: AbortSignal) =>
    apiRequest<OnuResult>(`/api/v1/subscriber-lookups/${encodeURIComponent(lookupId)}/connections/${encodeURIComponent(connectionId)}/onu`, { signal }),
  ticketOnu: (id: string, signal?: AbortSignal) =>
    apiRequest<OnuResult>(`${ticketPath(id)}/onu`, { signal }),
  checkTicket: (id: string, version: number) =>
    apiRequest<TicketCheckResult>(`${ticketPath(id)}/diagnostics/check`, {
      method: 'POST', ifMatch: `"${version}"`,
    }),
  confirmCheck: (id: string, checkId: string, version: number) =>
    apiRequest<Diagnostic>(`${ticketPath(id)}/diagnostics/confirm`, {
      method: 'POST', json: { check_id: checkId }, ifMatch: `"${version}"`,
    }),
  comments: (id: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<TicketComment>>(withQuery(`${ticketPath(id)}/comments`, { page, size: 20 }), { signal }),
  history: (id: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<TicketEvent>>(withQuery(`${ticketPath(id)}/history`, { page, size: 20 }), { signal }),
  diagnostics: (id: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<Diagnostic>>(withQuery(`${ticketPath(id)}/manual-diagnostics`, { page, size: 20 }), { signal }),
  decisions: (id: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<Decision>>(withQuery(`${ticketPath(id)}/decisions`, { page, size: 20 }), { signal }),
  decision: (id: string, decisionId: string, signal?: AbortSignal) =>
    apiRequest<Decision>(`${ticketPath(id)}/decisions/${encodeURIComponent(decisionId)}`, { signal }),
  callbacks: (id: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<TicketCallback>>(withQuery(`${ticketPath(id)}/callbacks`, { page, size: 20 }), { signal }),
  recordCallback: (id: string, outcome: CallbackOutcome, comment: string | null, version: number, requestId: string) =>
    apiRequest<Ticket>(`${ticketPath(id)}/callbacks`, {
      method: 'POST', json: { client_request_id: requestId, outcome, comment }, ifMatch: `"${version}"`,
    }),
  assign: (id: string, engineerId: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/assignment`, {
      method: 'POST', json: { engineer_id: engineerId }, ifMatch: `"${version}"`,
    }),
  start: (id: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/start`, { method: 'POST', ifMatch: `"${version}"` }),
  claim: (id: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/claim`, { method: 'POST', ifMatch: `"${version}"` }),
  release: (id: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/release`, { method: 'POST', ifMatch: `"${version}"` }),
  requestFieldWork: (id: string, reason: string, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/field-work-request`, {
      method: 'POST', json: { reason }, ifMatch: `"${version}"`,
    }),
  latestFieldWork: (id: string, signal?: AbortSignal) =>
    apiRequest<{ handoff: FieldWorkHandoff | null }>(`${ticketPath(id)}/field-work-handoffs/latest`, { signal }),
  sendFieldWork: (id: string, handoffId: string, requestId: string, visitAddress: string | null | undefined, messageText: string, direction: 'SUKHUM' | 'OCHAMCHIRA') =>
    apiRequest<FieldWorkHandoff>(`${ticketPath(id)}/field-work-handoffs/${encodeURIComponent(handoffId)}/send`, {
      method: 'POST', json: { client_request_id: requestId, visit_address: visitAddress, message_text: messageText, direction },
    }),
  fieldWorkMessages: (id: string, handoffId: string, signal?: AbortSignal) =>
    apiRequest<Page<FieldExternalMessage>>(
      `${ticketPath(id)}/field-work-handoffs/${encodeURIComponent(handoffId)}/messages?page=1&size=50`,
      { signal },
    ),
  fieldWorkReschedules: (id: string, handoffId: string, page: number, signal?: AbortSignal) =>
    apiRequest<Page<FieldWorkReschedule>>(
      `${ticketPath(id)}/field-work-handoffs/${encodeURIComponent(handoffId)}/reschedules?page=${page}&size=20`,
      { signal },
    ),
  rescheduleFieldWork: (id: string, handoffId: string, reason: string, scheduledFor: string, expectedVersion: number) =>
    apiRequest<FieldWorkHandoff>(
      `${ticketPath(id)}/field-work-handoffs/${encodeURIComponent(handoffId)}/reschedules`,
      { method: 'POST', json: { reason, scheduled_for: scheduledFor, expected_version: expectedVersion } },
    ),
  verifyFieldWork: (id: string, handoffId: string) =>
    apiRequest<FieldWorkHandoff>(
      `${ticketPath(id)}/field-work-handoffs/${encodeURIComponent(handoffId)}/verify`,
      { method: 'POST' },
    ),
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
  addDecision: (id: string, decisionText: string, reason: ResolutionReason) =>
    apiRequest<Decision>(`${ticketPath(id)}/decisions`, {
      method: 'POST', json: { decision_text: decisionText, reason },
    }),
  resolve: (id: string, decisionText: string, reason: ResolutionReason, version: number) =>
    apiRequest<Ticket>(`${ticketPath(id)}/resolve`, {
      method: 'POST', json: { decision_text: decisionText, reason }, ifMatch: `"${version}"`,
    }),
  close: (id: string, decisionId: string, version: number, reason: string) =>
    apiRequest<Ticket>(`${ticketPath(id)}/close`, {
      method: 'POST', json: { decision_id: decisionId, reason }, ifMatch: `"${version}"`,
    }),
}
