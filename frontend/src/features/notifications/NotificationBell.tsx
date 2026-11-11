import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, ArrowUpRight } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { formatDate } from '../tickets/presentation'
import { errorText } from '../../shared/api/client'
import type { MeResponse } from '../../shared/api/types'
import { taskApi, type TaskEventKind } from '../tasks/task-api'
import { notificationApi } from './notification-api'

const taskEventNames: Record<TaskEventKind, string> = {
  CREATED: 'Новое поручение', MESSAGE: 'Ответ по поручению', COMPLETED: 'Поручение завершено',
  NO_ANSWER: 'Не дозвонилась', REOPENED: 'Поручение открыто повторно',
}

export function NotificationBell({ session }: { session: MeResponse }) {
  const [open, setOpen] = useState(false)
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [toast, setToast] = useState<string | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const seenTaskNotices = useRef<Set<string>>(new Set())
  const taskInboxInitialized = useRef(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const canReadTicket = session.permissions.includes('tickets.read.all') || session.permissions.includes('tickets.read.own')
  const fieldEnabled = session.user.role === 'engineer' && session.permissions.includes('tickets.field_work') && canReadTicket
  const taskEnabled = canReadTicket && (session.permissions.includes('tasks.create') || session.permissions.includes('tasks.execute'))
  const fieldInbox = useQuery({
    queryKey: ['field-report-notifications', session.user.id],
    queryFn: ({ signal }) => notificationApi.list(signal),
    enabled: fieldEnabled, refetchInterval: 15_000, refetchOnWindowFocus: true,
  })
  const taskInbox = useQuery({
    queryKey: ['internal-task-notifications', session.user.id],
    queryFn: ({ signal }) => taskApi.notifications(signal),
    enabled: taskEnabled, refetchInterval: 15_000, refetchOnWindowFocus: true,
  })

  useEffect(() => {
    const items = taskInbox.data?.items
    if (!items) return
    if (!taskInboxInitialized.current) {
      items.forEach((item) => seenTaskNotices.current.add(item.id))
      taskInboxInitialized.current = true
      return
    }
    const fresh = items.filter((item) => !item.read_at && !seenTaskNotices.current.has(item.id))
    items.forEach((item) => seenTaskNotices.current.add(item.id))
    if (fresh[0]) setToast(`${taskEventNames[fresh[0].kind]} · ${fresh[0].title}`)
  }, [taskInbox.data])
  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 5000)
    return () => window.clearTimeout(timer)
  }, [toast])
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape) }
  }, [open])

  if (!fieldEnabled && !taskEnabled) return null

  async function openFieldNotification(notificationId: string, ticketId: string, unread: boolean) {
    if (openingId) return
    setActionError(null)
    if (unread) {
      setOpeningId(notificationId)
      try {
        await notificationApi.markRead(notificationId)
        await queryClient.invalidateQueries({ queryKey: ['field-report-notifications', session.user.id] })
      } catch (error) { setActionError(error); setOpeningId(null); return }
      setOpeningId(null)
    }
    setOpen(false)
    navigate(`/tickets/${ticketId}`)
  }

  async function openTaskNotification(notificationId: string, taskId: string, unread: boolean) {
    if (openingId) return
    setActionError(null)
    if (unread) {
      setOpeningId(notificationId)
      try {
        await taskApi.markRead(notificationId)
        await queryClient.invalidateQueries({ queryKey: ['internal-task-notifications', session.user.id] })
      } catch (error) { setActionError(error); setOpeningId(null); return }
      setOpeningId(null)
    }
    setOpen(false)
    navigate(`/tasks?task=${encodeURIComponent(taskId)}`)
  }

  const unread = (fieldInbox.data?.unread_count ?? 0) + (taskInbox.data?.unread_count ?? 0)
  return <div className="notification-bell" ref={root}>
    <button className="notification-bell__trigger" type="button" aria-label={`Уведомления, непрочитанных: ${unread}`} aria-expanded={open} aria-controls="field-report-inbox" onClick={() => { setOpen((value) => !value); setActionError(null) }}>
      <Bell size={19} aria-hidden="true" />{unread > 0 && <span className="notification-bell__count">{unread > 99 ? '99+' : unread}</span>}
    </button>
    {open && <div id="field-report-inbox" className="notification-bell__popover" role="region" aria-label="Уведомления">
      <div className="notification-bell__heading"><strong>Уведомления</strong><span>{unread ? `${unread} непрочитанных` : 'Новых нет'}</span></div>
      {actionError !== null && <p className="notification-bell__state" role="alert">Не удалось отметить прочтение. {errorText(actionError)}</p>}
      {taskEnabled && <><div className="notification-bell__subheading">Поручения</div>{taskInbox.isPending && <p className="notification-bell__state">Загружаем поручения…</p>}{taskInbox.isError && <p className="notification-bell__state" role="alert">{errorText(taskInbox.error)}</p>}{taskInbox.data && (taskInbox.data.items.length ? <div className="notification-bell__list">{taskInbox.data.items.map((item) => <button className={`notification-bell__item${item.read_at ? '' : ' notification-bell__item--unread'}`} key={item.id} type="button" disabled={openingId !== null} onClick={() => void openTaskNotification(item.id, item.task_id, !item.read_at)}><span className="notification-bell__item-title">{taskEventNames[item.kind]} · Заявка #{item.ticket_number}</span><span>{item.title}</span><time dateTime={item.created_at}>{formatDate(item.created_at)}</time><ArrowUpRight size={15} aria-hidden="true" /></button>)}</div> : <p className="notification-bell__state">Новых поручений нет.</p>)}</>}
      {fieldEnabled && <><div className="notification-bell__subheading">Отчёты монтажников</div>{fieldInbox.isPending && <p className="notification-bell__state">Загружаем уведомления…</p>}{fieldInbox.isError && <p className="notification-bell__state" role="alert">{errorText(fieldInbox.error)}</p>}{fieldInbox.data && (fieldInbox.data.items.length ? <div className="notification-bell__list">{fieldInbox.data.items.map((item) => <button className={`notification-bell__item${item.read_at ? '' : ' notification-bell__item--unread'}`} type="button" key={item.id} disabled={openingId !== null} onClick={() => void openFieldNotification(item.id, item.ticket_id, !item.read_at)}><span className="notification-bell__item-title">Заявка #{item.ticket_number} {item.awaiting_verification && <span className="notification-bell__attention" aria-label="Ожидает подтверждения" />}</span><span>Отчёт: {item.sender_name}</span><time dateTime={item.reported_at}>{formatDate(item.reported_at)}</time><ArrowUpRight size={15} aria-hidden="true" /></button>)}</div> : <p className="notification-bell__state">Отчётов пока нет.</p>)}</>}
    </div>}
    {toast && <div className="internal-task-toast" role="status" onClick={() => setToast(null)}>{toast}</div>}
  </div>
}
