import { apiRequest } from '../../shared/api/client'

export type TemplatePurpose = 'decision' | 'comment' | 'callback'

export interface DecisionReasonSetting {
  code: string
  name: string
  sort_order: number
  is_active: boolean
  version: number
  updated_at: string
  is_builtin: boolean
}

export interface TicketTextTemplate {
  id: string
  name: string
  body: string
  purpose: TemplatePurpose
  sort_order: number
  is_active: boolean
  version: number
  created_at: string
  updated_at: string
}

export const settingsApi = {
  reasons: (includeInactive = false, signal?: AbortSignal) => apiRequest<DecisionReasonSetting[]>(
    `/api/v1/settings/reasons${includeInactive ? '?include_inactive=true' : ''}`, { signal },
  ),
  createReason: (value: Pick<DecisionReasonSetting, 'name' | 'sort_order'>) =>
    apiRequest<DecisionReasonSetting>('/api/v1/settings/reasons', { method: 'POST', json: value }),
  updateReason: (code: string, version: number, changes: Partial<Pick<DecisionReasonSetting, 'name' | 'sort_order' | 'is_active'>>) =>
    apiRequest<DecisionReasonSetting>(`/api/v1/settings/reasons/${encodeURIComponent(code)}`, {
      method: 'PATCH', ifMatch: `"${version}"`, json: changes,
    }),
  deleteReason: (code: string, version: number) =>
    apiRequest<void>(`/api/v1/settings/reasons/${encodeURIComponent(code)}`, {
      method: 'DELETE', ifMatch: `"${version}"`,
    }),
  templates: (purpose?: TemplatePurpose, includeInactive = false, signal?: AbortSignal) => {
    const query = new URLSearchParams()
    if (purpose) query.set('purpose', purpose)
    if (includeInactive) query.set('include_inactive', 'true')
    return apiRequest<TicketTextTemplate[]>(`/api/v1/settings/templates${query.size ? `?${query}` : ''}`, { signal })
  },
  createTemplate: (value: Pick<TicketTextTemplate, 'name' | 'body' | 'purpose' | 'sort_order'>) =>
    apiRequest<TicketTextTemplate>('/api/v1/settings/templates', { method: 'POST', json: value }),
  updateTemplate: (id: string, version: number, changes: Partial<Pick<TicketTextTemplate, 'name' | 'body' | 'purpose' | 'sort_order' | 'is_active'>>) =>
    apiRequest<TicketTextTemplate>(`/api/v1/settings/templates/${encodeURIComponent(id)}`, {
      method: 'PATCH', ifMatch: `"${version}"`, json: changes,
    }),
}
