import { useQuery } from '@tanstack/react-query'
import { ArrowRight, ArrowUpRight, KeyRound, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router'
import { roleLabel } from '../layout/AppShell'
import { ticketApi } from '../features/tickets/ticket-api'
import { eventLabel, formatDate, statusLabel, ticketTitle } from '../features/tickets/presentation'
import type { MeResponse } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Notice } from '../shared/ui/Notice'
import { Panel } from '../shared/ui/Panel'
import { WorkspaceWelcome } from './WorkspaceWelcome'

export function WorkspacePage({ session }: { session: MeResponse }) {
  const hasAllDeskAccess = session.permissions.includes('tickets.read.all')
  const hasDeskAccess = hasAllDeskAccess || session.permissions.includes('tickets.read.own')
  const isEngineer = session.permissions.includes('tickets.assign')
  const open = useQuery({ queryKey: ['dashboard', 'open'], queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 1, status: 'OPEN' }, signal), enabled: hasDeskAccess })
  const working = useQuery({ queryKey: ['dashboard', 'working'], queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 1, status: 'IN_PROGRESS' }, signal), enabled: hasDeskAccess })
  const unassigned = useQuery({ queryKey: ['dashboard', 'unassigned'], queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 1, unassigned: true }, signal), enabled: hasDeskAccess })
  const mine = useQuery({ queryKey: ['dashboard', 'mine', session.user.id], queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 1, assignee: session.user.id }, signal), enabled: hasDeskAccess && isEngineer })
  const queue = useQuery({ queryKey: ['dashboard', 'queue'], queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 5, status: 'OPEN', unassigned: true }, signal), enabled: hasDeskAccess })
  const events = useQuery({ queryKey: ['dashboard', 'events'], queryFn: ({ signal }) => ticketApi.recentEvents(8, signal), enabled: hasDeskAccess })

  return (
    <div className="page page--overview desk-page">
      {session.user.role === 'operator'
        ? <WorkspaceWelcome user={session.user} />
        : <div className="page-heading desk-heading"><div><p className="eyebrow">ОБЗОР</p><h1>Добрый день, {session.user.display_name}</h1><p className="page-heading__description">Очередь заявок и действия команды на основе данных ServiceDesk.</p></div><div className="role-chip"><ShieldCheck size={17} /> {roleLabel(session.user.role)}</div></div>}
      {hasDeskAccess ? <>
        <div className="dashboard-metrics">
          <Link to="/tickets?status=OPEN" className="metric-card"><span>ОТКРЫТЫЕ</span><strong>{open.data?.total ?? '—'}</strong><small>{open.isError ? 'Не удалось загрузить' : 'Ожидают работы'}</small><ArrowUpRight size={17} /></Link>
          <Link to="/tickets?status=IN_PROGRESS" className="metric-card"><span>В РАБОТЕ</span><strong>{working.data?.total ?? '—'}</strong><small>{working.isError ? 'Не удалось загрузить' : 'Активные заявки'}</small><ArrowUpRight size={17} /></Link>
          <Link to="/tickets?unassigned=true" className="metric-card metric-card--accent"><span>БЕЗ ИСПОЛНИТЕЛЯ</span><strong>{unassigned.data?.total ?? '—'}</strong><small>{unassigned.isError ? 'Не удалось загрузить' : hasAllDeskAccess ? 'Общая очередь' : 'В пределах моего доступа'}</small><ArrowUpRight size={17} /></Link>
          {isEngineer && <Link to={`/tickets?assignee=${session.user.id}`} className="metric-card"><span>МОИ ЗАЯВКИ</span><strong>{mine.data?.total ?? '—'}</strong><small>{mine.isError ? 'Не удалось загрузить' : 'Назначены мне'}</small><ArrowUpRight size={17} /></Link>}
        </div>
        <div className="dashboard-columns">
          <Panel className="dashboard-panel"><div className="dashboard-panel__head"><div><p className="eyebrow">ОЧЕРЕДЬ</p><h2>Без исполнителя</h2></div><Link className="text-link" to="/tickets?status=OPEN&unassigned=true">Весь список <ArrowRight size={15} /></Link></div>{queue.isPending ? <p className="dashboard-empty" role="status">Загружаем очередь…</p> : queue.isError ? <Notice tone="error">{errorText(queue.error)}</Notice> : queue.data.items.length ? <div className="dashboard-list">{queue.data.items.map((ticket) => <Link to={`/tickets/${ticket.id}`} key={ticket.id}><span className="ticket-number">#{ticket.number}</span><strong>{ticketTitle(ticket.subject)}</strong><small>{statusLabel[ticket.status]} · {formatDate(ticket.created_at)}</small><ArrowRight size={14} /></Link>)}</div> : <p className="dashboard-empty">Открытых заявок без исполнителя сейчас нет.</p>}</Panel>
          <Panel className="dashboard-panel"><div className="dashboard-panel__head"><div><p className="eyebrow">ИСТОРИЯ</p><h2>Недавние события</h2></div></div>{events.isPending ? <p className="dashboard-empty" role="status">Загружаем события…</p> : events.isError ? <Notice tone="error">{errorText(events.error)}</Notice> : events.data.items.length ? <div className="dashboard-list dashboard-list--events">{events.data.items.map((event) => <Link to={`/tickets/${event.ticket_id}`} key={event.id}><span className="event-icon" /><strong>{eventLabel[event.event_type] || event.event_type}</strong><small>#{event.ticket_number} · {formatDate(event.occurred_at)}</small><ArrowRight size={14} /></Link>)}</div> : <p className="dashboard-empty">Событий заявок пока нет.</p>}</Panel>
        </div>
      </> : <div className="overview-grid"><Panel className="welcome-panel"><div className="panel-icon panel-icon--accent"><KeyRound size={22} /></div><p className="eyebrow">ВАША СЕССИЯ</p><h2>Сессия активна</h2><p>Вы вошли как <strong>{session.user.display_name}</strong>. Доступные действия определяются назначенными разрешениями и проверяются сервером.</p><Link className="text-link" to="/account">Посмотреть права доступа <ArrowUpRight size={16} /></Link></Panel><Notice>Для просмотра заявок необходимо разрешение на чтение заявок.</Notice></div>}
    </div>
  )
}
