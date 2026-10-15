export type Role = 'operator' | 'engineer' | 'admin'

export type Permission =
  | 'subscribers.read'
  | 'subscribers.finance.read'
  | 'tickets.read.all'
  | 'tickets.read.own'
  | 'tickets.edit.all'
  | 'tickets.edit.own'
  | 'tickets.delete.own'
  | 'tickets.delete.all'
  | 'tickets.restore'
  | 'tickets.purge'
  | 'tickets.create'
  | 'tickets.comment'
  | 'tickets.start'
  | 'tickets.assign'
  | 'tickets.field_work'
  | 'tickets.diagnose'
  | 'diagnostics.run'
  | 'diagnostics.read'
  | 'tickets.resolve'
  | 'tickets.close'
  | 'audit.read'
  | 'users.manage'
  | 'users.read'

export interface User {
  id: string
  login: string
  display_name: string
  role: Role
  is_active: boolean
  must_change_password?: boolean
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
