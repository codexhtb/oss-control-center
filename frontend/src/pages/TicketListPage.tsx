import { useQuery } from '@tanstack/react-query'
import { ArrowRight, ListFilter, Plus, RefreshCw, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import type { MeResponse } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Notice } from '../shared/ui/Notice'
import { Button } from '../shared/ui/Button'
import { ticketApi } from '../features/tickets/ticket-api'
import { formatDate, priorityLabel, statusLabel, ticketTitle, typeLabel } from '../features/tickets/presentation'
import type { TicketFilters, TicketPriority, TicketStatus, TicketType } from '../features/tickets/types'

const statuses: TicketStatus[] = ['OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED']
const types: TicketType[] = ['SUBSCRIBER_LINKED', 'MANUAL', 'INTERNAL']
const priorities: TicketPriority[] = ['LOW', 'NORMAL', 'HIGH', 'URGENT']
type Queue = 'all' | 'waiting' | 'mine'

function workAge(value: string): string {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000))
  if (minutes < 1) return 'В работе меньше минуты'
  if (minutes < 60) return `В работе ${minutes} мин`
  if (minutes < 1440) return `В работе ${Math.floor(minutes / 60)} ч`
  return `В работе ${Math.floor(minutes / 1440)} д`
}

function calendarDate(value: string | null, inclusiveEnd = false): string {
  if (!value) return ''
  const instant = new Date(value)
  if (Number.isNaN(instant.getTime())) return ''
  if (inclusiveEnd) instant.setMilliseconds(instant.getMilliseconds() - 1)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${instant.getFullYear()}-${pad(instant.getMonth() + 1)}-${pad(instant.getDate())}`
}

function localDayBoundary(value: string, followingDay = false): string {
  const [year = '0', month = '1', day = '1'] = value.split('-')
  return new Date(Number(year), Number(month) - 1, Number(day) + (followingDay ? 1 : 0)).toISOString()
}

export function TicketListPage({ session }: { session: MeResponse }) {
  const [params, setParams] = useSearchParams()
  const urlQuery = params.get('q') || ''
  const urlCategory = params.get('category') || ''
  const [searchText, setSearchText] = useState(urlQuery)
  const [categoryText, setCategoryText] = useState(urlCategory)
  useEffect(() => setSearchText(urlQuery), [urlQuery])
  useEffect(() => setCategoryText(urlCategory), [urlCategory])
  useEffect(() => {
    const next = searchText.trim()
    if (next === urlQuery) return
    const timer = window.setTimeout(() => setParams((current) => {
      const updated = new URLSearchParams(current)
      if (next) updated.set('q', next)
      else updated.delete('q')
      updated.delete('page')
      return updated
    }, { replace: true }), 350)
    return () => window.clearTimeout(timer)
  }, [searchText, urlQuery, setParams])
  useEffect(() => {
    const next = categoryText.trim()
    if (next === urlCategory) return
    const timer = window.setTimeout(() => setParams((current) => {
      const updated = new URLSearchParams(current)
      if (next) updated.set('category', next)
      else updated.delete('category')
      updated.delete('page')
      return updated
    }, { replace: true }), 350)
    return () => window.clearTimeout(timer)
  }, [categoryText, urlCategory, setParams])
  const canReadEngineers = session.permissions.some((permission) => ['tickets.read.all', 'tickets.read.own', 'tickets.assign'].includes(permission))
  const showQueues = session.permissions.includes('tickets.read.all')
  const queue: Queue = showQueues && (params.get('queue') === 'waiting' || params.get('queue') === 'mine') ? params.get('queue') as Queue : 'all'
  const page = Math.max(1, Number(params.get('page')) || 1)
  const filters: TicketFilters = { page, size: 20 }
  const status = params.get('status')
  const type = params.get('ticket_type')
  const priority = params.get('priority')
  const assignee = params.get('assignee')
  const createdFrom = params.get('created_from')
  const createdBefore = params.get('created_before')
  if (urlQuery) filters.q = urlQuery
  if (urlCategory) filters.category = urlCategory
  if (createdFrom) filters.created_from = createdFrom
  if (createdBefore) filters.created_before = createdBefore
  if (params.get('active_only') === 'true') filters.active_only = true
  if (statuses.includes(status as TicketStatus)) filters.status = status as TicketStatus
  if (types.includes(type as TicketType)) filters.ticket_type = type as TicketType
  if (priorities.includes(priority as TicketPriority)) filters.priority = priority as TicketPriority
  if (params.get('unassigned') === 'true') filters.unassigned = true
  if (assignee && !filters.unassigned) filters.assignee = assignee
  if (queue !== 'all') {
    filters.active_only = true
    if (queue === 'waiting') {
      filters.unassigned = true
      delete filters.assignee
    } else {
      filters.assignee = session.user.id
      delete filters.unassigned
    }
  }
  const invalidRange = !!(createdFrom && createdBefore && new Date(createdFrom) >= new Date(createdBefore))
  const searchPending = searchText.trim() !== urlQuery || categoryText.trim() !== urlCategory
  const hasFilters = Array.from(params.keys()).some((key) => key !== 'page')

  const tickets = useQuery({
    queryKey: ['tickets', filters],
    queryFn: ({ signal }) => ticketApi.list(filters, signal),
    enabled: !invalidRange,
  })
  const engineers = useQuery({
    queryKey: ['assignable-engineers'],
    queryFn: ({ signal }) => ticketApi.engineers(signal),
    enabled: canReadEngineers,
  })
  const waitingCount = useQuery({
    queryKey: ['tickets', 'queue-count', 'waiting'],
    queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 1, active_only: true, unassigned: true }, signal),
    enabled: showQueues,
  })
  const mineCount = useQuery({
    queryKey: ['tickets', 'queue-count', 'mine', session.user.id],
    queryFn: ({ signal }) => ticketApi.list({ page: 1, size: 1, active_only: true, assignee: session.user.id }, signal),
    enabled: showQueues,
  })
  const engineerNames = new Map(engineers.data?.items.map((item) => [item.id, item.display_name]))

  function setFilter(name: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current)
      if (value) next.set(name, value)
      else next.delete(name)
      if (name === 'unassigned' && value === 'true') next.delete('assignee')
      if (name === 'assignee' && value) next.delete('unassigned')
      next.delete('page')
      return next
    })
  }

  function setStatus(value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current)
      if (value === 'ACTIVE') {
        next.set('active_only', 'true')
        next.delete('status')
      } else {
        next.delete('active_only')
        if (value) next.set('status', value)
        else next.delete('status')
        if (value === 'CLOSED' || value === 'RESOLVED') next.delete('queue')
      }
      next.delete('page')
      return next
    })
  }

  function setQueue(value: Queue) {
    setParams((current) => {
      const next = new URLSearchParams(current)
      if (value === 'all') next.delete('queue')
      else next.set('queue', value)
      next.delete('page')
      next.delete('assignee')
      next.delete('unassigned')
      next.delete('active_only')
      if (value !== 'all' && ['CLOSED', 'RESOLVED'].includes(next.get('status') || '')) next.delete('status')
      return next
    })
  }

  function setPage(nextPage: number) {
    setParams((current) => {
      const next = new URLSearchParams(current)
      next.set('page', String(nextPage))
      return next
    })
  }

  function resetFilters() {
    setSearchText('')
    setCategoryText('')
    setParams(new URLSearchParams())
  }

  return (
    <div className="page desk-page">
      <div className="page-heading desk-heading">
        <div><p className="eyebrow">SERVICEDESK / РЕЕСТР</p><h1>Заявки</h1><p className="page-heading__description">Единая очередь обращений и внутренних задач OSS.</p></div>
        <Link className="button button--primary" to="/tickets/new"><Plus size={17} /> Новая заявка</Link>
      </div>
      {showQueues && <nav className="desk-queue-nav" aria-label="Рабочие очереди">
        <button type="button" className={queue === 'all' ? 'is-active' : ''} aria-current={queue === 'all' ? 'page' : undefined} onClick={() => setQueue('all')}>Все заявки</button>
        <button type="button" className={queue === 'waiting' ? 'is-active' : ''} aria-current={queue === 'waiting' ? 'page' : undefined} onClick={() => setQueue('waiting')}>Ожидают инженера <span>{waitingCount.data?.total ?? '—'}</span></button>
        <button type="button" className={queue === 'mine' ? 'is-active' : ''} aria-current={queue === 'mine' ? 'page' : undefined} onClick={() => setQueue('mine')}>Взяты мной <span>{mineCount.data?.total ?? '—'}</span></button>
      </nav>}
      <div className="ticket-search-row">
        <label className="ticket-search"><Search size={19} aria-hidden="true" /><span className="sr-only">Поиск по заявкам</span><input aria-label="Поиск по заявкам" type="search" value={searchText} maxLength={160} placeholder="Номер, абонент, ЛС, телефон или текст обращения" onChange={(event) => setSearchText(event.target.value)} /></label>
        <Button variant="quiet" disabled={!hasFilters && !searchPending} onClick={resetFilters}>Сбросить фильтры</Button>
      </div>
      <div className="desk-toolbar" aria-label="Фильтры заявок">
        <div className="desk-toolbar__title"><ListFilter size={17} /><span>Фильтры</span></div>
        <label className="compact-field"><span>Статус</span><select aria-label="Статус" value={filters.status || (filters.active_only ? 'ACTIVE' : '')} onChange={(event) => setStatus(event.target.value)}><option value="">Все</option><option value="ACTIVE">Не закрыты</option>{statuses.map((item) => <option value={item} key={item}>{statusLabel[item]}</option>)}</select></label>
        <label className="compact-field compact-field--wide"><span>Категория</span><input aria-label="Категория" value={categoryText} maxLength={160} placeholder="Название категории" onChange={(event) => setCategoryText(event.target.value)} /></label>
        <label className="compact-field"><span>Тип</span><select aria-label="Тип" value={filters.ticket_type || ''} onChange={(event) => setFilter('ticket_type', event.target.value)}><option value="">Все</option>{types.map((item) => <option value={item} key={item}>{typeLabel[item]}</option>)}</select></label>
        <label className="compact-field"><span>Приоритет</span><select aria-label="Приоритет" value={filters.priority || ''} onChange={(event) => setFilter('priority', event.target.value)}><option value="">Все</option>{priorities.map((item) => <option value={item} key={item}>{priorityLabel[item]}</option>)}</select></label>
        {canReadEngineers && <label className="compact-field compact-field--wide"><span>Исполнитель</span><select aria-label="Исполнитель" value={filters.assignee || ''} disabled={queue !== 'all'} onChange={(event) => setFilter('assignee', event.target.value)}><option value="">Все</option>{queue === 'mine' && <option value={session.user.id}>Я</option>}{engineers.data?.items.filter((item) => item.id !== (queue === 'mine' ? session.user.id : '')).map((item) => <option value={item.id} key={item.id}>{item.display_name}</option>)}</select></label>}
        <label className="desk-check"><input type="checkbox" checked={!!filters.unassigned} disabled={queue !== 'all'} onChange={(event) => setFilter('unassigned', event.target.checked ? 'true' : '')} /><span>Без исполнителя</span></label>
        <label className="compact-field"><span>Создана от</span><input aria-label="Создана от" type="date" value={calendarDate(createdFrom)} onChange={(event) => setFilter('created_from', event.target.value ? localDayBoundary(event.target.value) : '')} /></label>
        <label className="compact-field"><span>Создана до</span><input aria-label="Создана до" type="date" value={calendarDate(createdBefore, true)} onChange={(event) => setFilter('created_before', event.target.value ? localDayBoundary(event.target.value, true) : '')} /></label>
      </div>
      <section className="desk-table-wrap" aria-label="Список заявок">
        <div className="desk-table-caption"><div><strong>Реестр заявок</strong><span>{tickets.data && !searchPending && !invalidRange ? `${hasFilters ? 'Найдено' : 'Всего'}: ${tickets.data.total}` : 'Загрузка данных'}</span></div><Button variant="quiet" icon={<RefreshCw size={15} />} onClick={() => void tickets.refetch()} disabled={tickets.isFetching || invalidRange}>Обновить</Button></div>
        {invalidRange ? <div className="desk-state"><Notice tone="error">Дата «до» должна быть не раньше даты «от».</Notice></div> : searchPending ? <div className="desk-state" role="status">Обновляем поиск…</div> : tickets.isPending ? <div className="desk-state" role="status">Загружаем заявки…</div> : tickets.isError ? <div className="desk-state"><Notice tone="error">{errorText(tickets.error)}</Notice></div> : tickets.data.items.length === 0 ? <div className="desk-state desk-state--empty"><Search size={26} /><strong>Заявок по этим условиям нет</strong><span>Измените фильтры или создайте новую заявку.</span></div> : (
          <div className="desk-table-scroll"><table className="desk-table"><thead><tr><th>Заявка</th><th>Абонент</th><th>Статус</th><th>Приоритет</th><th>Исполнитель</th><th>Создана</th><th><span className="sr-only">Открыть</span></th></tr></thead><tbody>{tickets.data.items.map((ticket) => <tr key={ticket.id}>
            <td className="desk-ticket-cell"><Link className="ticket-link" to={`/tickets/${ticket.id}`} state={{ ticketListSearch: params.toString() }}><span className="ticket-number">#{ticket.number} · {typeLabel[ticket.ticket_type]}</span><strong>{ticketTitle(ticket.subject || ticket.category_name || null)}</strong><span className="ticket-excerpt">{ticket.description}</span></Link></td>
            <td className="desk-subscriber-cell">{ticket.ticket_type === 'SUBSCRIBER_LINKED' ? <div className="desk-subscriber"><strong>{ticket.subscriber_name || 'Абонент не указан'}</strong>{ticket.account_no && <span>ЛС {ticket.account_no}</span>}</div> : <span className="muted">{typeLabel[ticket.ticket_type]}</span>}</td>
            <td className="desk-status-cell"><span className={`status-chip status-chip--${ticket.status.toLowerCase()}`}>{statusLabel[ticket.status]}</span></td>
            <td className="desk-priority-cell"><span className={`priority priority--${ticket.priority.toLowerCase()}`}>{priorityLabel[ticket.priority]}</span></td>
            <td className="desk-assignee-cell">{ticket.assigned_engineer_id ? <>{ticket.assigned_engineer_name || engineerNames.get(ticket.assigned_engineer_id) || 'Инженер'}{ticket.work_started_at && <span className="desk-work-age">{workAge(ticket.work_started_at)}</span>}</> : <span className="muted">Не назначен</span>}</td>
            <td className="desk-date desk-created-cell">{formatDate(ticket.created_at)}</td>
            <td className="desk-open-cell"><Link aria-label={`Открыть заявку №${ticket.number}`} className="row-open" to={`/tickets/${ticket.id}`} state={{ ticketListSearch: params.toString() }}><ArrowRight size={16} /></Link></td>
          </tr>)}</tbody></table></div>
        )}
        {tickets.data && tickets.data.total > 0 && <div className="desk-pagination"><span>Страница {page} из {Math.max(1, Math.ceil(tickets.data.total / filters.size))}</span><div><Button variant="quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</Button><Button variant="quiet" disabled={page * filters.size >= tickets.data.total} onClick={() => setPage(page + 1)}>Далее</Button></div></div>}
      </section>
      {engineers.data && engineers.data.total > engineers.data.items.length && <p className="desk-footnote">Список исполнителей ограничен первой страницей API. Для остальных используйте ID исполнителя.</p>}
    </div>
  )
}
