import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { formatDate, statusLabel, ticketTitle } from './presentation'
import { ticketApi } from './ticket-api'

export function PreviousTickets({ ticketId, snapshotId }: { ticketId: string; snapshotId: string }) {
  const [expanded, setExpanded] = useState(false)
  const [page, setPage] = useState(1)
  const preview = useQuery({
    queryKey: ['ticket-previous', ticketId, snapshotId, 1, 3],
    queryFn: ({ signal }) => ticketApi.previousTickets(ticketId, 1, 3, signal),
  })
  const all = useQuery({
    queryKey: ['ticket-previous', ticketId, snapshotId, page, 10],
    queryFn: ({ signal }) => ticketApi.previousTickets(ticketId, page, 10, signal),
    enabled: expanded,
  })
  const visible = expanded ? all.data ?? (page === 1 ? preview.data : undefined) : preview.data
  const total = preview.data?.total ?? 0

  return <section className="detail-panel previous-tickets" aria-label="История обращений абонента">
    <div className="detail-panel__heading">
      <h2>История обращений абонента</h2>
      {preview.isSuccess && <span>Предыдущих обращений: {total}</span>}
    </div>
    {preview.isPending ? <p role="status" className="muted">Загружаем обращения…</p>
      : preview.isError ? <Notice tone="error">Не удалось загрузить обращения: {errorText(preview.error)}</Notice>
        : total === 0 ? <p className="muted">Предыдущих обращений нет.</p>
          : <>
            {expanded && all.isPending && <p role="status" className="muted">Загружаем все обращения…</p>}
            {expanded && all.isError && <Notice tone="error">Не удалось загрузить список: {errorText(all.error)}</Notice>}
            {visible && <div className="table-scroll"><table className="subscriber-table previous-tickets__table">
              <thead><tr><th>Номер</th><th>Тема</th><th>Статус</th><th>Исполнитель</th><th>Создана</th></tr></thead>
              <tbody>{visible.items.map((ticket) => <tr key={ticket.id} className={ticket.status !== 'CLOSED' ? 'previous-tickets__row--open' : undefined}>
                <td><Link to={`/tickets/${ticket.id}`} aria-label={`Открыть заявку #${ticket.number}`}>#{ticket.number}</Link></td>
                <td>{ticketTitle(ticket.subject)}</td>
                <td><span className={ticket.status !== 'CLOSED' ? 'previous-tickets__status--open' : undefined}>{statusLabel[ticket.status]}</span></td>
                <td>{ticket.assigned_engineer_name || 'Не назначен'}</td>
                <td><time dateTime={ticket.created_at}>{formatDate(ticket.created_at)}</time></td>
              </tr>)}</tbody>
            </table></div>}
            <div className="previous-tickets__actions">
              {total > 3 && <Button variant="quiet" aria-expanded={expanded} onClick={() => { setExpanded(!expanded); setPage(1) }}>{expanded ? 'Свернуть' : 'Все обращения'}</Button>}
              {expanded && visible && total > 10 && <div className="subscriber-pagination">
                <Button variant="quiet" disabled={page === 1} onClick={() => setPage(page - 1)}>Назад</Button>
                <span>{page} / {Math.ceil(total / 10)}</span>
                <Button variant="quiet" disabled={page * 10 >= total} onClick={() => setPage(page + 1)}>Далее</Button>
              </div>}
            </div>
          </>}
  </section>
}
