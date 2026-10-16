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
  category_id?: string | null
  category_name?: string | null
  caller_phone?: string | null
  description: string
  status: TicketStatus
  priority: TicketPriority
  account_no: string | null
  subscriber_snapshot_id: string | null
  connection_snapshot_id: string | null
  selected_service_id: string | null
  created_by: string
  created_by_name?: string | null
  assigned_engineer_id: string | null
  assigned_engineer_name?: string | null
  closed_by: string | null
  closed_decision_id: string | null
  closed_at: string | null
  deleted_at: string | null
  deleted_by: string | null
  delete_reason: string | null
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
  ip_group: string | null
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

export type DataQuality = 'KNOWN' | 'UNKNOWN' | 'STALE' | 'CONFLICT' | 'UNAVAILABLE'
export interface FieldOrigin { source: 'NETUP' | 'MOCK'; observed_at: string; quality: DataQuality }
export interface SubscriberService {
  service_id: string
  name: string | null
  account_id: string | null
  tariff_name: string | null
  billing_active: boolean | null
  billing_blocked: boolean | null
  field_meta: Record<string, FieldOrigin>
}
export interface SubscriberIpRow {
  row_id: string
  ip_type_code: number
  ip_raw: number | null
  mask_raw: number | null
  ip_ext_raw: number | null
  mask_ext_raw: number | null
  dhcp_pool_id: string | null
  switch_id: string | null
  device: string | null
  port_id: number | null
  vlan_id: number | null
  field_meta: Record<string, FieldOrigin>
}
export interface SubscriberConnectionCard {
  connection_snapshot_id: string
  access_type: string
  device: string | null
  port: string | null
  vlan: string | null
  onu_id: string | null
  ip_group: string | null
  service_id: string | null
  ip_groups: Array<{ group_id: string; rows: SubscriberIpRow[]; field_meta: Record<string, FieldOrigin> }>
  field_meta: Record<string, FieldOrigin>
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
    contacts: Array<{ type: 'mobile' | 'work' | 'home'; value: string; source: string; observed_at: string; quality: DataQuality }>
    email: string | null
    actual_address: string | null
    subscriber_id: string | null
    field_meta: Record<string, FieldOrigin>
  }
  accounts: Array<{ account_id: string; is_primary: boolean; field_meta: Record<string, FieldOrigin> }>
  financial_accounts: Array<{ account_id: string; balance: string | null; credit: string | null; account_blocked: boolean | null; field_meta: Record<string, FieldOrigin> }>
  contracts: Array<{ contract_id: string; label: string | null; field_meta: Record<string, FieldOrigin> }>
  services: SubscriberService[]
  connections: SubscriberConnectionCard[]
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
  actor_name?: string | null
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
  author_name?: string | null
  body: string
  created_at: string
}

export interface Diagnostic {
  id: string
  ticket_id: string
  check_id: string | null
  source_kind: string
  availability: string
  observation: { finding?: string } | DiagnosticReport
  observed_at: string
  recorded_at: string
  recorded_by: string
}

export interface MonitoringObservation {
  source: 'LIBRENMS' | 'ICINGA'
  measured_object: string
  result: string
  observed_at: string | null
  retrieved_at: string
  quality: DataQuality
}

export interface DiagnosticReport {
  connection_snapshot_id: string
  checked_at: string
  device_match: DataQuality
  interface_match: DataQuality
  observations: MonitoringObservation[]
  limitations: string[]
}

export interface TicketCheckResult {
  check_id: string
  expires_at: string
  report: DiagnosticReport
}

export interface OnuCandidate {
  olt: string | null
  interface: string
  pon_type: 'EPON' | 'GPON'
  mac: string | null
  serial: string | null
  state: 'ONLINE' | 'OFFLINE' | 'DEREGISTERED' | 'UNKNOWN'
  reason: string | null
  onu_rx_dbm: string | null
  olt_rx_dbm: string | null
  onu_tx_dbm: string | null
  inventory_polled_at: string | null
  optical_measured_at: string | null
  disconnection_at?: string | null
  disconnection_time_basis?: 'EPON_DEREGISTRATION' | 'FIRST_OBSERVED_OFFLINE' | null
}

export interface OnuResult {
  source: 'BDCOM'
  retrieved_at: string
  availability: 'AVAILABLE' | 'UNAVAILABLE' | 'UNKNOWN' | 'CONFLICT'
  association: 'UNCONFIRMED' | 'MATCHED_BY_RULE'
  match?: {
    netup_port: number
    pon_port: number
    vlan: number
    olt_mapping_basis: 'manual_verified' | 'unique_exact_sysname'
    billing_observed_at: string | null
    billing_source: 'NETUP'
    onu_source: 'LIBRENMS_BDCOM'
  } | null
  candidates: OnuCandidate[]
  limitations: string[]
  time_basis: 'SOURCE_LOCAL_UNVERIFIED'
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
  description?: string
  category_id?: string
  caller_phone?: string
  priority: TicketPriority
  client_request_id: string
} & (
  | { ticket_type: 'MANUAL' | 'INTERNAL' }
  | {
      ticket_type: 'SUBSCRIBER_LINKED'
      lookup_id: string
      selected_service_id?: string
      connection_snapshot_id?: string
      duplicate_reason?: string
    }
)

export interface ActiveTicketSummary {
  id: string
  number: number
  subject: string | null
  status: TicketStatus
  created_at: string
  assigned_engineer_name: string | null
  description: string
}

export interface TicketCategory {
  id: string
  name: string
  sort_order: number
  is_active: boolean
  is_other: boolean
  version: number
}

export interface ActiveTickets {
  items: ActiveTicketSummary[]
  page: number
  size: number
  total_visible: number
  has_hidden_active: boolean
}

export interface TicketFilters {
  page: number
  size: number
  status?: TicketStatus
  ticket_type?: TicketType
  priority?: TicketPriority
  assignee?: string
  unassigned?: boolean
}
