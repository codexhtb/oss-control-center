export type Role = 'operator' | 'engineer' | 'admin'

export type Permission =
  | 'subscriber.read'
  | 'subscriber.finance.read'
  | 'ticket.read'
  | 'ticket.create'
  | 'ticket.comment'
  | 'ticket.start'
  | 'ticket.assign'
  | 'ticket.field_work'
  | 'ticket.manual_diagnostic'
  | 'ticket.decision'
  | 'ticket.close'
  | 'ticket.audit.read'
  | 'field_dispatch.send'
  | 'identity.manage'

export interface User {
  id: string
  login: string
  display_name: string
  role: Role
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface MeResponse {
  user: User
  permissions: Permission[]
}

export interface LoginResponse extends MeResponse {
  expires_at: string
}

export interface ReadyResponse {
  status: 'ready' | 'not_ready'
  dependencies: { postgres: 'up' | 'down' }
}

export interface ApiErrorBody {
  code: string
  message: string
  request_id: string
}
