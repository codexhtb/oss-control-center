import type { ResolutionReason, TicketPriority, TicketStatus, TicketType } from './types'

export const resolutionReasonLabel: Record<ResolutionReason, string> = {
  CABLE_BREAK: 'Обрыв кабеля',
  CUSTOMER_EQUIPMENT: 'Оборудование абонента',
  ONU_POWER: 'Питание ONU',
  NETWORK_FAULT: 'Неисправность сети',
  CONFIGURATION_IP: 'Конфигурация / IP',
  CONNECTION_SETUP: 'Подключение / настройка',
  CONSULTATION: 'Консультация',
  OTHER: 'Другое',
  NOT_ESTABLISHED: 'Причина не установлена',
}

export const typeLabel: Record<TicketType, string> = {
  SUBSCRIBER_LINKED: 'По абоненту',
  MANUAL: 'Ручная',
  INTERNAL: 'Внутренняя',
}

export const statusLabel: Record<TicketStatus, string> = {
  OPEN: 'Открыта',
  IN_PROGRESS: 'В работе',
  RESOLVED: 'Решено',
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
  ticket_edited: 'Заявка отредактирована',
  engineer_assigned: 'Назначен инженер',
  engineer_claimed: 'Инженер взял заявку',
  engineer_released: 'Инженер освободил заявку',
  ticket_started: 'Работа начата',
  comment_added: 'Добавлен комментарий',
  manual_diagnostic_added: 'Записана диагностика',
  decision_recorded: 'Записано решение',
  ticket_resolved: 'Инженерное решение принято',
  field_work_requested: 'Запрошены выездные работы',
  field_work_sent: 'Заявка передана монтажникам',
  field_work_delivery_failed: 'Не удалось передать заявку монтажникам',
  field_work_delivery_uncertain: 'Статус передачи монтажникам не определён',
  field_external_message_received: 'Получен ответ монтажника',
  field_work_reported: 'Монтажники сообщили о выполнении',
  field_work_verified: 'Инженер подтвердил выездные работы',
  ticket_closed: 'Заявка закрыта',
  ticket_closed_without_call: 'Закрыта без контрольного звонка',
  callback_confirmed_closed: 'Абонент подтвердил устранение проблемы',
  callback_returned_engineers: 'Проблема осталась — возвращена инженерам',
  callback_no_answer: 'Не удалось дозвониться',
  ticket_deleted: 'Заявка перемещена в корзину',
  ticket_restored: 'Заявка восстановлена',
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

export function ticketTitle(subject: string | null): string {
  return subject || 'Без темы'
}
