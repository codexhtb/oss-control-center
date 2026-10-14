import type { TicketPriority, TicketStatus, TicketType } from './types'

export const typeLabel: Record<TicketType, string> = {
  SUBSCRIBER_LINKED: 'По абоненту',
  MANUAL: 'Ручная',
  INTERNAL: 'Внутренняя',
}

export const statusLabel: Record<TicketStatus, string> = {
  OPEN: 'Открыта',
  IN_PROGRESS: 'В работе',
  CLOSED: 'Закрыта',
}

export const priorityLabel: Record<TicketPriority, string> = {
  LOW: 'Низкий',
  NORMAL: 'Обычный',
  HIGH: 'Высокий',
  URGENT: 'Срочный',
}

export const eventLabel: Record<string, string> = {
  ticket_created: 'Заявка создана',
  engineer_assigned: 'Назначен инженер',
  ticket_started: 'Работа начата',
  comment_added: 'Добавлен комментарий',
  manual_diagnostic_added: 'Записана диагностика',
  decision_recorded: 'Записано решение',
  field_work_requested: 'Запрошены выездные работы',
  ticket_closed: 'Заявка закрыта',
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

export function ticketTitle(subject: string | null): string {
  return subject || 'Без темы'
}
