export type TicketType = 'SUBSCRIBER_LINKED' | 'MANUAL' | 'INTERNAL'
export type TicketStatus = 'OPEN' | 'IN_PROGRESS' | 'CLOSED'
export type TicketPriority = 'LOW' | 'NORMAL' | 'HIGH' | 'URGENT'

export interface Page<T> {
  items: T[]
  page: number
  size: number
  total: number
}

export interface Ticket {
  id: string
  number: number
  ticket_type: TicketType
  subject: string | null
  description: string
  status: TicketStatus
  priority: TicketPriority
  account_no: string | null
  subscriber_snapshot_id: string | null
  connection_snapshot_id: string | null
  selected_service_id: string | null
  created_by: string
  assigned_engineer_id: string | null
  closed_by: string | null
  closed_decision_id: string | null
  closed_at: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface SubscriberConnection {
  id: string
  access_type: string
  device: string | null
  port: string | null
  vlan: string | null
  onu_id: string | null
  address: string | null
  service_id: string | null
  ip_groups: unknown[]
  field_meta: Record<string, unknown>
}

export interface TicketDetail extends Ticket {
  subscriber: {
    full_name: string | null
    organization_name: string | null
    phone: string | null
    actual_address: string | null
    fetched_at: string
    source: string
    services: Array<{ service_id?: string; id?: string; name?: string | null }>
    connections: SubscriberConnection[]
  } | null
  selected_connection: SubscriberConnection | null
  technical_state: string
}

export interface SubscriberCard {
  lookup_id: string
  source: string
  fetched_at: string
  expires_at: string
  is_expired: boolean
  subscriber: {
    billing_account: string
    full_name: string | null
    organization_name: string | null
    phone: string | null
    actual_address: string | null
  }
  services: Array<{ service_id: string; name: string | null }>
  connections: Array<{
    connection_snapshot_id: string
    access_type: string
    device: string | null
    port: string | null
    vlan: string | null
    service_id: string | null
  }>
  technical_state: string
}

export interface Engineer {
  id: string
  display_name: string
}

export interface TicketEvent {
  id: string
  ticket_id: string
  event_type: string
  actor_id: string | null
  ticket_version: number
  occurred_at: string
  event_data: Record<string, unknown>
}

export interface RecentEvent extends Omit<TicketEvent, 'event_data'> {
  ticket_number: number
}

export interface TicketComment {
  id: string
  ticket_id: string
  author_id: string
  body: string
  created_at: string
}

export interface Diagnostic {
  id: string
  ticket_id: string
  source_kind: string
  availability: string
  observation: { finding?: string }
  observed_at: string
  recorded_at: string
  recorded_by: string
}

export interface Decision {
  id: string
  ticket_id: string
  engineer_id: string
  decision_text: string
  performed_actions: string
  created_at: string
}

export type CreateTicketInput = {
  subject?: string
  description: string
  priority: TicketPriority
  client_request_id: string
} & (
  | { ticket_type: 'MANUAL' | 'INTERNAL' }
  | {
      ticket_type: 'SUBSCRIBER_LINKED'
      lookup_id: string
      selected_service_id?: string
      connection_snapshot_id?: string
    }
)

export interface TicketFilters {
  page: number
  size: number
  status?: TicketStatus
  ticket_type?: TicketType
  priority?: TicketPriority
  assignee?: string
  unassigned?: boolean
}
