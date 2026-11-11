import { apiRequest } from '../../shared/api/client'

export interface GeneralReport {
  start: string
  end: string
  metrics: {
    created: number
    resolved_events: number
    closed_events: number
    returned_events: number
    active_now: number
    average_resolution_seconds: number | null
    average_closure_seconds: number | null
  }
  daily: { date: string; created: number; closed: number }[]
  distribution: { types: Record<string, number>; statuses: Record<string, number>; priorities: Record<string, number> }
  details: { items: { id: string; number: number; subject: string | null; ticket_type: string; status: string; priority: string; created_at: string; assignee_name: string | null }[]; total: number; page: number; size: number }
}

export function getGeneralReport(start: string, end: string, page: number, signal?: AbortSignal) {
  const params = new URLSearchParams({ start, end, page: String(page), size: '20' })
  return apiRequest<GeneralReport>(`/api/v1/reports/general?${params}`, { signal })
}

export interface EmployeesReport {
  start: string
  end: string
  items: {
    user_id: string; display_name: string; role: string; is_active: boolean
    created_tickets: number; decision_events: number; callback_events: number
    callback_tickets: number; outcomes: Record<string, number>
  }[]
  daily: { user_id: string; date: string; created: number; decisions: number; callbacks: number }[]
}

export interface CallbacksReport {
  start: string
  end: string
  total_events: number
  unique_tickets: number
  outcomes: Record<string, number>
  daily: { date: string; outcome: string; count: number }[]
  employees: { user_id: string; display_name: string; outcome: string; count: number }[]
  details: { page: number; size: number; total: number; items: {
    id: string; ticket_id: string; ticket_number: number; subject: string | null
    outcome: string; occurred_at: string; actor_id: string; actor_name: string
  }[] }
}

export function getEmployeesReport(start: string, end: string, signal?: AbortSignal) {
  const params = new URLSearchParams({ start, end })
  return apiRequest<EmployeesReport>(`/api/v1/reports/employees?${params}`, { signal })
}

export function getCallbacksReport(start: string, end: string, page: number, signal?: AbortSignal) {
  const params = new URLSearchParams({ start, end, page: String(page), size: '20' })
  return apiRequest<CallbacksReport>(`/api/v1/reports/callbacks?${params}`, { signal })
}
