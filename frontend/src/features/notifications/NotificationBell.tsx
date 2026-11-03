import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, ArrowUpRight } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { formatDate } from '../tickets/presentation'
import { errorText } from '../../shared/api/client'
import type { MeResponse } from '../../shared/api/types'
import { notificationApi } from './notification-api'

export function NotificationBell({ session }: { session: MeResponse }) {
  const [open, setOpen] = useState(false)
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const root = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const enabled = session.user.role === 'engineer'
    && session.permissions.includes('tickets.field_work')
    && (session.permissions.includes('tickets.read.all') || session.permissions.includes('tickets.read.own'))
  const inbox = useQuery({
    queryKey: ['field-report-notifications', session.user.id],
    queryFn: ({ signal }) => notificationApi.list(signal),
    enabled,
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  })

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  if (!enabled) return null

  async function openNotification(notificationId: string, ticketId: string, unread: boolean) {
    if (openingId) return
    setActionError(null)
    if (unread) {
      setOpeningId(notificationId)
      try {
        await notificationApi.markRead(notificationId)
        await queryClient.invalidateQueries({ queryKey: ['field-report-notifications', session.user.id] })
      } catch (error) {
        setActionError(error)
        setOpeningId(null)
        return
      }
      setOpeningId(null)
    }
    setOpen(false)
    navigate(`/tickets/${ticketId}`)
  }

  const unread = inbox.data?.unread_count ?? 0
  return <div className="notification-bell" ref={root}>
    <button className="notification-bell__trigger" type="button" aria-label={`Уведомления, непрочитанных: ${unread}`} aria-expanded={open} aria-controls="field-report-inbox" onClick={() => { setOpen((value) => !value); setActionError(null) }}>
      <Bell size={19} aria-hidden="true" />
      {unread > 0 && <span className="notification-bell__count">{unread > 99 ? '99+' : unread}</span>}
    </button>
    {open && <div id="field-report-inbox" className="notification-bell__popover" role="region" aria-label="Уведомления о выездных работах">
      <div className="notification-bell__heading"><strong>Отчёты монтажников</strong><span>{unread ? `${unread} непрочитанных` : 'Новых нет'}</span></div>
      {inbox.isPending && <p className="notification-bell__state">Загружаем уведомления…</p>}
      {inbox.isError && <p className="notification-bell__state" role="alert">{errorText(inbox.error)}</p>}
      {actionError !== null && <p className="notification-bell__state" role="alert">Не удалось отметить прочтение. {errorText(actionError)}</p>}
      {inbox.data && (inbox.data.items.length ? <div className="notification-bell__list">{inbox.data.items.map((item) => <button className={`notification-bell__item${item.read_at ? '' : ' notification-bell__item--unread'}`} type="button" key={item.id} disabled={openingId !== null} onClick={() => void openNotification(item.id, item.ticket_id, !item.read_at)}>
        <span className="notification-bell__item-title">Заявка #{item.ticket_number} {item.awaiting_verification && <span className="notification-bell__attention" aria-label="Ожидает подтверждения" />}</span>
        <span>Отчёт: {item.sender_name}</span>
        <time dateTime={item.reported_at}>{formatDate(item.reported_at)}</time>
        <ArrowUpRight size={15} aria-hidden="true" />
      </button>)}</div> : <p className="notification-bell__state">Отчётов пока нет.</p>)}
    </div>}
  </div>
}
