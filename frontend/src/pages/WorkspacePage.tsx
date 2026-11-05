import { useQuery } from '@tanstack/react-query'
import { ArrowRight, ArrowUpRight, KeyRound } from 'lucide-react'
import { Link } from 'react-router'
import { ticketApi } from '../features/tickets/ticket-api'
import { formatDate, statusLabel, ticketTitle } from '../features/tickets/presentation'
import type { MeResponse } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Notice } from '../shared/ui/Notice'
import { Panel } from '../shared/ui/Panel'
import { WorkspaceWelcome } from './WorkspaceWelcome'

export function WorkspacePage({ session }: { session: MeResponse }) {
  const hasAllDeskAccess = session.permissions.includes('tickets.read.all')
  const hasDeskAccess = hasAllDeskAccess || session.permissions.includes('tickets.read.own')
  const canWork = session.permissions.includes('tickets.start') && session.permissions.includes('tickets.resolve')
  const canCallback = session.permissions.includes('tickets.callback')
  const canCreate = session.permissions.includes('tickets.create')
  const unassigned = useQuery({ queryKey: ['dashboard', 'unassigned'], queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 1, active_only: true, unassigned: true }, signal), enabled: hasDeskAccess && canWork })
  const mine = useQuery({ queryKey: ['dashboard', 'mine', session.user.id], queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 1, active_only: true, assignee: session.user.id }, signal), enabled: hasDeskAccess && canWork })
  const callback = useQuery({ queryKey: ['dashboard', 'callback'], queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 1, callback_queue: true }, signal), enabled: hasDeskAccess && canCallback })
  const queue = useQuery({ queryKey: ['dashboard', 'queue'], queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 5, active_only: true, unassigned: true }, signal), enabled: hasDeskAccess && canWork })

  return (
    <div className="page page--overview desk-page">
      <WorkspaceWelcome user={session.user} />
      {hasDeskAccess ? <>
        <div className="dashboard-metrics">
          {canWork && <Link to="/tickets?queue=waiting" className="metric-card metric-card--accent"><span>ОЖИДАЮТ ИНЖЕНЕРА</span><strong>{unassigned.data?.total ?? '—'}</strong><small>{unassigned.isError ? 'Не удалось загрузить' : 'Можно взять в работу'}</small><ArrowUpRight size={17} /></Link>}
          {canWork && <Link to="/tickets?queue=mine" className="metric-card"><span>ВЗЯТЫ МНОЙ</span><strong>{mine.data?.total ?? '—'}</strong><small>{mine.isError ? 'Не удалось загрузить' : 'Текущая работа'}</small><ArrowUpRight size={17} /></Link>}
          {canCallback && <Link to="/tickets?queue=callback" className="metric-card"><span>ОЖИДАЮТ ОБЗВОНА</span><strong>{callback.data?.total ?? '—'}</strong><small>{callback.isError ? 'Не удалось загрузить' : 'Решение уже сохранено'}</small><ArrowUpRight size={17} /></Link>}
          {canCreate && <Link to="/tickets/new" className="metric-card"><span>НОВОЕ ОБРАЩЕНИЕ</span><strong>+</strong><small>Создать заявку</small><ArrowUpRight size={17} /></Link>}
        </div>
        {canWork && <Panel className="dashboard-panel"><div className="dashboard-panel__head"><h2>Следующие заявки</h2><Link className="text-link" to="/tickets?queue=waiting">Вся очередь <ArrowRight size={15} /></Link></div>{queue.isPending ? <p className="dashboard-empty" role="status">Загружаем очередь…</p> : queue.isError ? <Notice tone="error">{errorText(queue.error)}</Notice> : queue.data.items.length ? <div className="dashboard-list">{queue.data.items.map((ticket) => <Link to={`/tickets/${ticket.id}`} key={ticket.id}><span className="ticket-number">#{ticket.number}</span><strong>{ticketTitle(ticket.subject)}</strong><small>{statusLabel[ticket.status]} · {formatDate(ticket.created_at)}</small><ArrowRight size={14} /></Link>)}</div> : <p className="dashboard-empty">Свободных заявок сейчас нет.</p>}</Panel>}
      </> : <div className="overview-grid"><Panel className="welcome-panel"><div className="panel-icon panel-icon--accent"><KeyRound size={22} /></div><p className="eyebrow">ВАША СЕССИЯ</p><h2>Сессия активна</h2><p>Вы вошли как <strong>{session.user.display_name}</strong>. Доступные действия определяются назначенными разрешениями и проверяются сервером.</p><Link className="text-link" to="/account">Посмотреть права доступа <ArrowUpRight size={16} /></Link></Panel><Notice>Для просмотра заявок необходимо разрешение на чтение заявок.</Notice></div>}
    </div>
  )
}
