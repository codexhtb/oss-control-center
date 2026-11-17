export type Direction = 'SUKHUM' | 'OCHAMCHIRA'
export type QueueTab = 'open' | 'completed' | 'history'

export type Handoff = {
  id: string
  ticket_id: string
  ticket_number: number
  direction: Direction
  work_status: 'AWAITING_REPORT' | 'REPORTED' | 'VERIFIED'
  subject: string
  sent_at: string | null
  published_at: string | null
  reported_at: string | null
  verified_at: string | null
  scheduled_for: string | null
  scheduled_date: string | null
  schedule_status: 'ORIGINAL' | 'DATE_PENDING' | 'SCHEDULED'
}

export type HandoffDetail = Handoff & {
  reason: string
  name: string | null
  phone: string | null
  account_no: string | null
  city: string | null
  address: string | null
  schedule_version: number
  schedule_reason: string | null
  schedule_author: string | null
  report: { text: string; installer: string; partner: string | null; reported_at: string } | null
}

export type Reschedule = {
  id: string
  reason: string
  scheduled_for: string | null
  scheduled_date: string | null
  author_name: string
  created_at: string
}

export type Page<T> = { items: T[]; total: number; page: number; page_size: number }
export type Partner = { id: string; name: string }

export class FieldWorkError extends Error {
  constructor(message: string, readonly status: number) { super(message) }
}

export async function fieldWorkRequest<T>(initData: string, path: string, body?: object): Promise<T> {
  const response = await fetch(`/api/v1/field-work/miniapp${path}`, {
    method: body ? 'POST' : 'GET',
    credentials: 'omit',
    cache: 'no-store',
    headers: {
      'X-Telegram-Init-Data': initData,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { code?: string }
    const message = payload.code === 'invalid_link_code' ? 'Код неверный или срок его действия истёк.'
      : payload.code === 'telegram_identity_conflict' ? 'Этот Telegram уже зарегистрирован как монтажник или привязан к сотруднику.'
      : response.status === 401 ? 'Сессия Telegram истекла. Откройте приложение заново.'
      : response.status === 403 ? 'Доступ к этому направлению не предоставлен.'
      : response.status === 409 ? 'Задание уже изменилось. Обновите данные.'
      : payload.code === 'field_schedule_date_past' ? 'Выберите будущую дату.'
      : response.status === 422 ? 'Проверьте введённые данные.'
      : 'Не удалось получить данные. Повторите попытку.'
    throw new FieldWorkError(message, response.status)
  }
  return response.json() as Promise<T>
}
