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
