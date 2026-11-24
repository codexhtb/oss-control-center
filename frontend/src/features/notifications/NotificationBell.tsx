import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, ArrowUpRight } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { formatDate } from '../tickets/presentation'
import { errorText } from '../../shared/api/client'
import type { MeResponse } from '../../shared/api/types'
import { notificationApi, type CenterNotification, type CenterPage } from './notification-api'

const eventNames: Record<string, string> = {
  CREATED: 'Новое поручение', MESSAGE: 'Ответ по поручению', COMPLETED: 'Поручение завершено',
  NO_ANSWER: 'Не дозвонилась', REOPENED: 'Поручение открыто повторно', REMINDER: 'Напоминание по поручению',
  FIELD_REPORT: 'Отчёт монтажника',
}

export function NotificationBell({ session }: { session: MeResponse }) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<'unread' | 'read'>('unread')
  const [page, setPage] = useState(1)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<unknown>(null)
  const locked = useRef(false)
  const root = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const canRead = session.permissions.includes('tickets.read.all') || session.permissions.includes('tickets.read.own')
  const enabled = canRead && (session.permissions.includes('tasks.create') || session.permissions.includes('tasks.execute')
    || (['engineer', 'admin'].includes(session.user.role) && session.permissions.includes('tickets.field_work')))
  const queryKey = ['notification-center', session.user.id]
  const unreadInbox = useQuery({
    queryKey: [...queryKey, 'unread', tab === 'unread' ? page : 1],
    queryFn: ({ signal }) => notificationApi.center('unread', tab === 'unread' ? page : 1, signal),
    enabled, refetchInterval: 15_000, refetchOnWindowFocus: true,
    placeholderData: (previous, query) => query?.queryKey[1] === session.user.id ? previous : undefined,
  })
  const history = useQuery({
    queryKey: [...queryKey, 'read', page],
    queryFn: ({ signal }) => notificationApi.center('read', page, signal),
    enabled: enabled && open && tab === 'read', refetchInterval: 15_000, refetchOnWindowFocus: true,
  })
  const inbox = tab === 'unread' ? unreadInbox : history
  const unread = unreadInbox.data?.unread_count ?? 0

  useEffect(() => {
    if (inbox.data && page > Math.max(1, Math.ceil(inbox.data.total / inbox.data.size))) setPage(1)
  }, [inbox.data, page])
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); root.current?.querySelector('button')?.focus() } }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape) }
  }, [open])

  if (!enabled) return null

  async function read(item?: CenterNotification) {
    if (locked.current) return
    if (item?.read_at) { goToTicket(item); return }
    locked.current = true
    setBusy(true)
    setActionError(null)
    try {
      // A pre-mutation poll must never overwrite the acknowledged read state.
      await queryClient.cancelQueries({ queryKey })
      if (item) await notificationApi.readCenter(item.source, item.id)
      else await notificationApi.readAll()
      await queryClient.cancelQueries({ queryKey })
      queryClient.setQueriesData<CenterPage>({ queryKey: [...queryKey, 'unread'] }, (previous) => previous && ({
        ...previous,
        items: item ? previous.items.filter((entry) => entry.id !== item.id || entry.source !== item.source) : [],
        total: item ? Math.max(0, previous.total - 1) : 0,
        unread_count: item ? Math.max(0, previous.unread_count - 1) : 0,
      }))
      void Promise.all([
        queryClient.invalidateQueries({ queryKey }),
        queryClient.invalidateQueries({ queryKey: ['internal-task-notifications', session.user.id] }),
        queryClient.invalidateQueries({ queryKey: ['internal-tasks'] }),
        queryClient.invalidateQueries({ queryKey: ['internal-task'] }),
      ])
      if (item) goToTicket(item)
      else setPage(1)
    } catch (error) { setActionError(error) }
    finally { locked.current = false; setBusy(false) }
  }

  function goToTicket(item: CenterNotification) {
    setOpen(false)
    navigate(`/tickets/${encodeURIComponent(item.ticket_id)}${item.task_id ? `?task=${encodeURIComponent(item.task_id)}` : ''}`)
  }

  return <div className="notification-bell" ref={root}>
    <button className={`notification-bell__trigger${unread > 0 ? ' notification-bell__trigger--new-task' : ''}`} type="button" aria-label={`Уведомления, непрочитанных: ${unread}`} aria-expanded={open} aria-controls="notification-center" onClick={() => { setOpen((value) => !value); setActionError(null) }}>
      <Bell size={19} aria-hidden="true" />{unread > 0 && <span className="notification-bell__count">{unread > 99 ? '99+' : unread}</span>}
    </button>
    {open && <div id="notification-center" className="notification-bell__popover" role="region" aria-label="Уведомления">
      <div className="notification-bell__heading"><strong>Уведомления</strong><button className="notification-bell__action" type="button" disabled={busy || unread === 0} onClick={() => void read()}>Прочитать все</button></div>
      <div className="notification-bell__tabs" role="tablist" aria-label="Список уведомлений">
        {(['unread', 'read'] as const).map((value) => <button key={value} id={`notification-tab-${value}`} role="tab" aria-selected={tab === value} aria-controls="notification-panel" type="button" onClick={() => { setTab(value); setPage(1); setActionError(null) }}>{value === 'unread' ? 'Непрочитанные' : 'История'}</button>)}
      </div>
      <div id="notification-panel" role="tabpanel" aria-labelledby={`notification-tab-${tab}`} aria-busy={busy || inbox.isFetching}>
        {actionError !== null && <p className="notification-bell__state" role="alert">Не удалось отметить прочтение. {errorText(actionError)}</p>}
        {inbox.isPending && <p className="notification-bell__state" role="status">Загружаем уведомления…</p>}
        {inbox.isError && <p className="notification-bell__state" role="alert">{errorText(inbox.error)}</p>}
        {inbox.data && (inbox.data.items.length ? <div className="notification-bell__list">{inbox.data.items.map((item) => <button className="notification-bell__item" key={`${item.source}:${item.id}`} type="button" disabled={busy} onClick={() => void read(item)}>
          <span className="notification-bell__item-title">{eventNames[item.kind] ?? 'Уведомление'}</span>
          <span className="notification-bell__item-description">{item.title}</span>
          <span className="notification-bell__item-meta">Заявка #{item.ticket_number}<time dateTime={item.created_at}>{formatDate(item.created_at)}</time></span>
          <ArrowUpRight size={15} aria-hidden="true" />
        </button>)}</div> : <p className="notification-bell__state">{tab === 'unread' ? 'Непрочитанных уведомлений нет.' : 'Прочитанных уведомлений пока нет.'}</p>)}
        {inbox.data && inbox.data.total > inbox.data.size && <div className="notification-bell__pager"><button type="button" disabled={busy || page === 1} onClick={() => setPage(page - 1)}>Назад</button><span>{page} / {Math.ceil(inbox.data.total / inbox.data.size)}</span><button type="button" disabled={busy || page * inbox.data.size >= inbox.data.total} onClick={() => setPage(page + 1)}>Далее</button></div>}
      </div>
    </div>}
  </div>
}
