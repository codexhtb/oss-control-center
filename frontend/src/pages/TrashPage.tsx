import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { MeResponse } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { ticketApi } from '../features/tickets/ticket-api'
import { formatDate, ticketTitle } from '../features/tickets/presentation'
import type { Ticket } from '../features/tickets/types'

export function TrashPage({ session }: { session: MeResponse }) {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Ticket | null>(null)
  const [number, setNumber] = useState('')
  const [finalStep, setFinalStep] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const canRestore = session.permissions.includes('tickets.restore')
  const canPurge = session.permissions.includes('tickets.purge')
  const trash = useQuery({ queryKey: ['ticket-trash', page], queryFn: ({ signal }) => ticketApi.trash(page, signal) })

  async function run(action: () => Promise<unknown>) {
    if (busy) return
    setBusy(true); setError(null)
    try {
      await action()
      setSelected(null); setNumber(''); setFinalStep(false)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ticket-trash'] }),
        queryClient.invalidateQueries({ queryKey: ['tickets'] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      ])
    } catch (reason) {
      setError(reason)
      await queryClient.invalidateQueries({ queryKey: ['ticket-trash'] })
    } finally { setBusy(false) }
  }

  return <div className="page desk-page">
    <div className="page-heading"><div><h1>Корзина</h1><p className="page-heading__description">Удалённые заявки скрыты из реестра. Восстановление возвращает историю без изменений.</p></div></div>
    {error !== null && <Notice tone="error">{errorText(error)} Обновите список при конфликте версии.</Notice>}
    <section className="detail-panel"><div className="detail-panel__heading"><h2>Удалённые заявки</h2><span>{trash.data?.total ?? '—'}</span></div>
      {trash.isPending ? <p role="status">Загружаем корзину…</p> : trash.isError ? <Notice tone="error">{errorText(trash.error)}</Notice> : trash.data.items.length === 0 ? <p className="empty-facts">Корзина пуста.</p> : <div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Номер</th><th>Тема</th><th>Удалена</th><th>Причина</th><th>Действия</th></tr></thead><tbody>{trash.data.items.map((ticket) => <tr key={ticket.id}><td>#{ticket.number}</td><td>{ticketTitle(ticket.subject)}</td><td>{ticket.deleted_at ? formatDate(ticket.deleted_at) : '—'}</td><td>{ticket.delete_reason || '—'}</td><td><div className="user-actions">{canRestore && <Button variant="secondary" disabled={busy} onClick={() => void run(() => ticketApi.restore(ticket.id, ticket.version))}>Восстановить</Button>}{canPurge && <Button variant="quiet" disabled={busy} onClick={() => { setSelected(ticket); setNumber(''); setFinalStep(false); setError(null) }}>Окончательно удалить</Button>}</div></td></tr>)}</tbody></table></div>}
      {trash.data && trash.data.total > 20 && <div className="fact-pager"><Button variant="quiet" disabled={page === 1} onClick={() => setPage(page - 1)}>Назад</Button><span>Страница {page} из {Math.ceil(trash.data.total / 20)}</span><Button variant="quiet" disabled={page * 20 >= trash.data.total} onClick={() => setPage(page + 1)}>Далее</Button></div>}
    </section>
    {selected && canPurge && <section className="detail-panel" role="dialog" aria-label="Подтверждение окончательного удаления"><h2>Окончательное удаление</h2><p>Заявка №{selected.number} «{ticketTitle(selected.subject)}». Содержимое, комментарии, история и диагностика этой заявки будут удалены без возможности восстановления. Минимальная запись аудита сохранится.</p><label className="field"><span>Введите номер заявки для подтверждения</span><input inputMode="numeric" value={number} onChange={(event) => { setNumber(event.target.value); setFinalStep(false) }} /></label><div className="user-actions">{!finalStep ? <Button variant="secondary" disabled={number !== String(selected.number)} onClick={() => setFinalStep(true)}>Продолжить</Button> : <Button variant="secondary" disabled={busy} onClick={() => void run(() => ticketApi.purge(selected.id, selected.version, selected.number))}>Да, удалить без возможности восстановления</Button>}<Button variant="quiet" onClick={() => setSelected(null)}>Отмена</Button></div></section>}
  </div>
}
