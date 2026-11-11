import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import type { MeResponse } from '../../shared/api/types'
import { errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { formatDate } from '../tickets/presentation'
import { taskApi, type TaskEventKind } from './task-api'

const eventNames: Record<TaskEventKind, string> = {
  CREATED: 'Поручение создано', MESSAGE: 'Сообщение', COMPLETED: 'Ответ',
  NO_ANSWER: 'Не дозвонилась', REOPENED: 'Открыто повторно',
}

export function TaskConversation({ taskId, session, ticketId, onUpdated }: { taskId: string; session: MeResponse; ticketId?: string; onUpdated?: () => void }) {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [copied, setCopied] = useState(false)
  const task = useQuery({ queryKey: ['internal-task', taskId], queryFn: ({ signal }) => taskApi.get(taskId, signal), refetchInterval: 15_000 })
  const events = useQuery({ queryKey: ['internal-task-events', taskId, page], queryFn: ({ signal }) => taskApi.events(taskId, page, signal), refetchInterval: page === 1 ? 15_000 : false })
  const canFinish = task.data?.status === 'OPEN' && task.data?.assignee_id === session.user.id && session.permissions.includes('tasks.execute')

  async function finish() {
    if (!task.data || busy || !answer.trim()) return
    setBusy(true)
    setError(null)
    try {
      await taskApi.act(taskId, task.data.version, answer.trim(), crypto.randomUUID())
      setAnswer('')
      setPage(1)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['internal-task', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['internal-task-events', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['internal-tasks'] }),
        queryClient.invalidateQueries({ queryKey: ['internal-task-notifications'] }),
      ])
      onUpdated?.()
    } catch (cause) {
      setError(cause)
      await queryClient.invalidateQueries({ queryKey: ['internal-task', taskId] })
    } finally { setBusy(false) }
  }

  async function copyPhone(phone: string) {
    try { await navigator.clipboard.writeText(phone); setCopied(true) }
    catch (cause) { setError(cause) }
  }

  if (task.isPending) return <p role="status">Загружаем поручение…</p>
  if (task.isError) return <Notice tone="error">{errorText(task.error)}</Notice>
  const item = task.data
  if (ticketId && item.ticket_id !== ticketId) return <Notice tone="error">Поручение не относится к этой заявке.</Notice>
  return <section className="internal-task-conversation" aria-label={`Поручение: ${item.title}`}>
    <div className="internal-task-conversation__heading"><div><h3>Поручение</h3><p>Заявка <Link to={`/tickets/${item.ticket_id}`}>#{item.ticket_number}</Link> · {item.creator_name} → {item.assignee_name}</p></div><span className={`internal-task-status internal-task-status--${item.status.toLowerCase()}`}>{item.status === 'OPEN' ? 'Активно' : 'Завершено'}</span></div>
    {(item.subscriber_name || item.phone || item.account_no) && <dl className="internal-task-contact">{item.subscriber_name && <div><dt>Абонент</dt><dd>{item.subscriber_name}</dd></div>}{item.phone && <div><dt>Телефон</dt><dd><a href={`tel:${item.phone.replace(/[^\d+]/g, '')}`}>{item.phone}</a><Button variant="quiet" onClick={() => void copyPhone(item.phone!)}>{copied ? 'Скопировано' : 'Копировать'}</Button></dd></div>}{item.account_no && <div><dt>Лицевой счёт</dt><dd>{item.account_no}</dd></div>}</dl>}
    <p className="internal-task-description">{item.description}</p>
    {events.isPending && <p>Загружаем ответ…</p>}
    {events.isError && <Notice tone="error">{errorText(events.error)}</Notice>}
    {events.data?.items.filter((entry) => entry.kind !== 'CREATED').slice().reverse().map((entry) => <div key={entry.id} className="internal-task-event"><div><b>{entry.actor_name}</b><time dateTime={entry.created_at}>{formatDate(entry.created_at)}</time></div><span>{eventNames[entry.kind]}</span>{entry.body && <p>{entry.body}</p>}</div>)}
    {events.data && events.data.total > 50 && <div className="fact-pager"><Button variant="quiet" disabled={page === 1} onClick={() => setPage(page - 1)}>Новее</Button><span>{page} / {Math.ceil(events.data.total / 50)}</span><Button variant="quiet" disabled={page * 50 >= events.data.total} onClick={() => setPage(page + 1)}>Ранее</Button></div>}
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    {canFinish && <form className="internal-task-reply" onSubmit={(event) => { event.preventDefault(); void finish() }}><label className="field"><span>Ответ</span><textarea value={answer} maxLength={4000} onChange={(event) => setAnswer(event.target.value)} rows={3} /></label><Button type="submit" disabled={busy || !answer.trim()}>Завершить поручение</Button></form>}
  </section>
}
