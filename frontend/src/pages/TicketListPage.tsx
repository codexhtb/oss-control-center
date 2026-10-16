import { useQuery } from '@tanstack/react-query'
import { ArrowRight, ListFilter, Plus, RefreshCw, Search } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'
import type { MeResponse } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Notice } from '../shared/ui/Notice'
import { Button } from '../shared/ui/Button'
import { ticketApi } from '../features/tickets/ticket-api'
import { formatDate, priorityLabel, statusLabel, ticketTitle, typeLabel } from '../features/tickets/presentation'
import type { TicketFilters, TicketPriority, TicketStatus, TicketType } from '../features/tickets/types'

const statuses: TicketStatus[] = ['OPEN', 'IN_PROGRESS', 'CLOSED']
const types: TicketType[] = ['SUBSCRIBER_LINKED', 'MANUAL', 'INTERNAL']
const priorities: TicketPriority[] = ['LOW', 'NORMAL', 'HIGH', 'URGENT']

export function TicketListPage({ session }: { session: MeResponse }) {
  const [params, setParams] = useSearchParams()
  const canAssign = session.permissions.includes('tickets.assign')
  const page = Math.max(1, Number(params.get('page')) || 1)
  const filters: TicketFilters = { page, size: 20 }
  const status = params.get('status')
  const type = params.get('ticket_type')
  const priority = params.get('priority')
  const assignee = params.get('assignee')
  if (statuses.includes(status as TicketStatus)) filters.status = status as TicketStatus
  if (types.includes(type as TicketType)) filters.ticket_type = type as TicketType
  if (priorities.includes(priority as TicketPriority)) filters.priority = priority as TicketPriority
  if (params.get('unassigned') === 'true') filters.unassigned = true
  if (assignee && !filters.unassigned) filters.assignee = assignee

  const tickets = useQuery({
    queryKey: ['tickets', filters],
    queryFn: ({ signal }) => ticketApi.list(filters, signal),
  })
  const engineers = useQuery({
    queryKey: ['assignable-engineers'],
    queryFn: ({ signal }) => ticketApi.engineers(signal),
    enabled: canAssign,
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

  function setPage(nextPage: number) {
    setParams((current) => {
      const next = new URLSearchParams(current)
      next.set('page', String(nextPage))
      return next
    })
  }

  return (
    <div className="page desk-page">
      <div className="page-heading desk-heading">
        <div><p className="eyebrow">SERVICEDESK / РЕЕСТР</p><h1>Заявки</h1><p className="page-heading__description">Единая очередь обращений и внутренних задач OSS.</p></div>
        <Link className="button button--primary" to="/tickets/new"><Plus size={17} /> Новая заявка</Link>
      </div>
      <div className="desk-toolbar" aria-label="Фильтры заявок">
        <div className="desk-toolbar__title"><ListFilter size={17} /><span>Фильтры</span></div>
        <label className="compact-field"><span>Статус</span><select aria-label="Статус" value={filters.status || ''} onChange={(event) => setFilter('status', event.target.value)}><option value="">Все</option>{statuses.map((item) => <option value={item} key={item}>{statusLabel[item]}</option>)}</select></label>
        <label className="compact-field"><span>Тип</span><select aria-label="Тип" value={filters.ticket_type || ''} onChange={(event) => setFilter('ticket_type', event.target.value)}><option value="">Все</option>{types.map((item) => <option value={item} key={item}>{typeLabel[item]}</option>)}</select></label>
        <label className="compact-field"><span>Приоритет</span><select aria-label="Приоритет" value={filters.priority || ''} onChange={(event) => setFilter('priority', event.target.value)}><option value="">Все</option>{priorities.map((item) => <option value={item} key={item}>{priorityLabel[item]}</option>)}</select></label>
        {canAssign ? (
          <label className="compact-field compact-field--wide"><span>Исполнитель</span><select aria-label="Исполнитель" value={filters.assignee || ''} onChange={(event) => setFilter('assignee', event.target.value)}><option value="">Все</option>{engineers.data?.items.map((item) => <option value={item.id} key={item.id}>{item.display_name}</option>)}</select></label>
        ) : (
          <label className="compact-field compact-field--wide"><span>ID исполнителя</span><input aria-label="ID исполнителя" value={filters.assignee || ''} maxLength={36} placeholder="UUID" onChange={(event) => setFilter('assignee', event.target.value)} /></label>
        )}
        <label className="desk-check"><input type="checkbox" checked={!!filters.unassigned} onChange={(event) => setFilter('unassigned', event.target.checked ? 'true' : '')} /><span>Без исполнителя</span></label>
      </div>
      <section className="desk-table-wrap" aria-label="Список заявок">
        <div className="desk-table-caption"><div><strong>Реестр заявок</strong><span>{tickets.data ? `Всего: ${tickets.data.total}` : 'Загрузка данных'}</span></div><Button variant="quiet" icon={<RefreshCw size={15} />} onClick={() => void tickets.refetch()} disabled={tickets.isFetching}>Обновить</Button></div>
        {tickets.isPending ? <div className="desk-state" role="status">Загружаем заявки…</div> : tickets.isError ? <div className="desk-state"><Notice tone="error">{errorText(tickets.error)}</Notice></div> : tickets.data.items.length === 0 ? <div className="desk-state desk-state--empty"><Search size={26} /><strong>Заявок по этим условиям нет</strong><span>Измените фильтры или создайте новую заявку.</span></div> : (
          <div className="desk-table-scroll"><table className="desk-table"><thead><tr><th>Заявка</th><th>Абонент</th><th>Статус</th><th>Приоритет</th><th>Исполнитель</th><th>Создана</th><th><span className="sr-only">Открыть</span></th></tr></thead><tbody>{tickets.data.items.map((ticket) => <tr key={ticket.id}>
            <td className="desk-ticket-cell"><Link className="ticket-link" to={`/tickets/${ticket.id}`}><span className="ticket-number">#{ticket.number} · {typeLabel[ticket.ticket_type]}</span><strong>{ticketTitle(ticket.subject || ticket.category_name || null)}</strong><span className="ticket-excerpt">{ticket.description}</span></Link></td>
            <td className="desk-subscriber-cell">{ticket.ticket_type === 'SUBSCRIBER_LINKED' ? <div className="desk-subscriber"><strong>{ticket.subscriber_name || 'Абонент не указан'}</strong>{ticket.account_no && <span>ЛС {ticket.account_no}</span>}</div> : <span className="muted">{typeLabel[ticket.ticket_type]}</span>}</td>
            <td className="desk-status-cell"><span className={`status-chip status-chip--${ticket.status.toLowerCase()}`}>{statusLabel[ticket.status]}</span></td>
            <td className="desk-priority-cell"><span className={`priority priority--${ticket.priority.toLowerCase()}`}>{priorityLabel[ticket.priority]}</span></td>
            <td className="desk-assignee-cell">{ticket.assigned_engineer_id ? ticket.assigned_engineer_name || engineerNames.get(ticket.assigned_engineer_id) || `ID ${ticket.assigned_engineer_id.slice(0, 8)}` : <span className="muted">Не назначен</span>}</td>
            <td className="desk-date desk-created-cell">{formatDate(ticket.created_at)}</td>
            <td className="desk-open-cell"><Link aria-label={`Открыть заявку №${ticket.number}`} className="row-open" to={`/tickets/${ticket.id}`}><ArrowRight size={16} /></Link></td>
          </tr>)}</tbody></table></div>
        )}
        {tickets.data && tickets.data.total > 0 && <div className="desk-pagination"><span>Страница {page} из {Math.max(1, Math.ceil(tickets.data.total / filters.size))}</span><div><Button variant="quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</Button><Button variant="quiet" disabled={page * filters.size >= tickets.data.total} onClick={() => setPage(page + 1)}>Далее</Button></div></div>}
      </section>
      {engineers.data && engineers.data.total > engineers.data.items.length && <p className="desk-footnote">Список исполнителей ограничен первой страницей API. Для остальных используйте ID исполнителя.</p>}
    </div>
  )
}
